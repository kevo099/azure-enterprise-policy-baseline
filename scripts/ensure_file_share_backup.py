#!/usr/bin/env python3
"""Audit or enable Azure Backup for every eligible Azure file share in scope.

The script uses ARM inventory (`az storage share-rm`) so it also sees shares
created without data-plane credentials.  It is dry-run by default.  `--apply`
enables or resumes protection with an existing Recovery Services vault and an
existing Azure Files backup policy, then waits for Azure to record a healthy
configuration (`IRPending` or `Protected`) for every submitted share.

Azure Backup does not support NFS Azure file shares.  They are reported as
`UNSUPPORTED_NFS` and never presented as protected.  A successful apply means
every eligible classic SMB share has either healthy protection or an accepted
configuration whose first recovery point is still pending; it does not claim
recovery-point freshness or restoreability.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable
from urllib.parse import quote, unquote


class AzureCliError(RuntimeError):
    """Raised when Azure CLI returns a non-zero exit status or invalid JSON."""


class AzureCli:
    def __init__(
        self, subscription: str | None = None, command_timeout_seconds: int = 300
    ) -> None:
        self.subscription = subscription
        self.command_timeout_seconds = command_timeout_seconds
        self._resource_manager_endpoint: str | None = None

    def json(
        self,
        *arguments: str,
        timeout_seconds: float | None = None,
        include_subscription: bool = True,
    ) -> Any:
        command = ["az", *arguments, "--only-show-errors", "--output", "json"]
        if self.subscription and include_subscription:
            command.extend(["--subscription", self.subscription])
        effective_timeout = (
            self.command_timeout_seconds
            if timeout_seconds is None
            else min(float(self.command_timeout_seconds), timeout_seconds)
        )
        if effective_timeout <= 0:
            raise AzureCliError(f"{' '.join(command[:-2])}: no command time remains")
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AzureCliError(
                f"{' '.join(command[:-2])}: exceeded "
                f"{effective_timeout:g}s command timeout"
            ) from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise AzureCliError(f"{' '.join(command[:-2])}: {detail}")
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AzureCliError(
                f"{' '.join(command[:-2])}: returned invalid JSON: {exc}"
            ) from exc

    def rest_values(self, url: str) -> list[dict[str, Any]]:
        """Read every page of an ARM list response through `az rest`."""

        values: list[dict[str, Any]] = []
        next_url = url
        while next_url:
            response = self.json("rest", "--method", "get", "--url", next_url)
            if not isinstance(response, dict) or not isinstance(
                response.get("value"), list
            ):
                raise AzureCliError(
                    f"Azure REST list {next_url!r} returned an invalid envelope"
                )
            for value in response["value"]:
                if not isinstance(value, dict):
                    raise AzureCliError(
                        f"Azure REST list {next_url!r} returned a non-object item"
                    )
                values.append(value)
            raw_next_link = response.get("nextLink")
            if raw_next_link is not None and not isinstance(raw_next_link, str):
                raise AzureCliError(
                    f"Azure REST list {next_url!r} returned an invalid nextLink"
                )
            next_url = raw_next_link or ""
        return values

    def resource_graph_values(
        self, query: str, subscription_id: str
    ) -> list[dict[str, Any]]:
        """Read a complete, deterministically ordered Resource Graph query."""

        url = (
            f"{self.resource_manager_endpoint()}/providers/"
            "Microsoft.ResourceGraph/resources?api-version=2024-04-01"
        )
        values: list[dict[str, Any]] = []
        expected_total: int | None = None
        skip_token = ""
        seen_tokens: set[str] = set()
        seen_ids: set[str] = set()

        while True:
            options: dict[str, Any] = {
                "$top": 1000,
                "resultFormat": "objectArray",
            }
            if skip_token:
                options["$skipToken"] = skip_token
            body = {
                "subscriptions": [subscription_id],
                "query": query,
                "options": options,
            }
            response = self.json(
                "rest",
                "--method",
                "post",
                "--url",
                url,
                "--body",
                json.dumps(body, separators=(",", ":")),
            )
            if not isinstance(response, dict) or not isinstance(
                response.get("data"), list
            ):
                raise AzureCliError(
                    "Azure Resource Graph returned an invalid query envelope"
                )

            page = response["data"]
            count = response.get("count")
            total = response.get("totalRecords")
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count != len(page)
                or not isinstance(total, int)
                or isinstance(total, bool)
                or total < 0
            ):
                raise AzureCliError(
                    "Azure Resource Graph returned invalid count metadata"
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise AzureCliError(
                    "Azure Resource Graph totalRecords changed during pagination"
                )

            for value in page:
                if not isinstance(value, dict):
                    raise AzureCliError(
                        "Azure Resource Graph returned a non-object item"
                    )
                resource_id = value.get("id")
                if not isinstance(resource_id, str) or not resource_id:
                    raise AzureCliError(
                        "Azure Resource Graph returned an item without an id"
                    )
                folded_id = resource_id.casefold()
                if folded_id in seen_ids:
                    raise AzureCliError(
                        "Azure Resource Graph returned a duplicate id during pagination"
                    )
                seen_ids.add(folded_id)
                values.append(value)

            truncated = response.get("resultTruncated")
            if isinstance(truncated, bool):
                is_truncated = truncated
            elif isinstance(truncated, str) and truncated.casefold() in {
                "true",
                "false",
            }:
                is_truncated = truncated.casefold() == "true"
            else:
                raise AzureCliError(
                    "Azure Resource Graph returned invalid resultTruncated metadata"
                )
            if is_truncated:
                raise AzureCliError(
                    "Azure Resource Graph result was incomplete and could not "
                    "be paged"
                )

            raw_skip_token = response.get("$skipToken")
            if raw_skip_token is not None and (
                not isinstance(raw_skip_token, str) or not raw_skip_token
            ):
                raise AzureCliError(
                    "Azure Resource Graph returned an invalid $skipToken"
                )
            if raw_skip_token:
                if raw_skip_token in seen_tokens or not page:
                    raise AzureCliError(
                        "Azure Resource Graph pagination made no forward progress"
                    )
                seen_tokens.add(raw_skip_token)
                skip_token = raw_skip_token
                continue

            if len(values) != expected_total:
                raise AzureCliError(
                    "Azure Resource Graph result was incomplete and had no "
                    "continuation token"
                )
            return values

    def resource_manager_endpoint(self) -> str:
        if self._resource_manager_endpoint is None:
            cloud = self.json("cloud", "show", include_subscription=False) or {}
            endpoint = str((cloud.get("endpoints") or {}).get("resourceManager") or "")
            if not endpoint:
                raise AzureCliError(
                    "could not resolve the active Azure cloud Resource Manager endpoint"
                )
            self._resource_manager_endpoint = endpoint.rstrip("/")
        return self._resource_manager_endpoint


@dataclass(frozen=True)
class Target:
    subscription_id: str
    location: str
    vault_resource_group: str
    vault_name: str
    policy_name: str


@dataclass(frozen=True)
class FileShare:
    subscription_id: str
    resource_group: str
    storage_account: str
    storage_account_id: str
    location: str
    name: str
    protocol: str

    @property
    def account_key(self) -> tuple[str, str, str]:
        return (
            self.subscription_id.casefold(),
            self.resource_group.casefold(),
            self.storage_account.casefold(),
        )

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            *self.account_key,
            self.name.casefold(),
        )


@dataclass(frozen=True)
class ProtectedItem:
    subscription_id: str
    resource_group: str
    storage_account: str
    share_name: str
    vault_resource_group: str
    vault_name: str
    vault_subscription_id: str
    container_name: str
    item_name: str
    protection_state: str
    health: str
    soft_deleted: bool

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.subscription_id.casefold(),
            self.resource_group.casefold(),
            self.storage_account.casefold(),
            self.share_name.casefold(),
        )

    @property
    def active(self) -> bool:
        return (
            not self.soft_deleted
            and self.protection_state.casefold() == "protected"
            and self.health.casefold() == "healthy"
        )

    @property
    def configured(self) -> bool:
        return not self.soft_deleted and (
            self.active
            or (
                self.protection_state.casefold() == "irpending"
                and self.health.casefold() == "healthy"
            )
        )

    @property
    def resumable(self) -> bool:
        return not self.soft_deleted and self.protection_state.casefold() in {
            "backupsuspended",
            "backupssuspended",
            "paused",
            "protectionpaused",
            "protectionstopped",
            "stopped",
        }


@dataclass(frozen=True)
class BackupContainer:
    subscription_id: str
    resource_group: str
    storage_account: str
    vault_subscription_id: str
    vault_resource_group: str
    vault_name: str
    container_name: str
    registration_status: str

    @property
    def account_key(self) -> tuple[str, str, str]:
        return (
            self.subscription_id.casefold(),
            self.resource_group.casefold(),
            self.storage_account.casefold(),
        )


@dataclass(frozen=True)
class PlanEntry:
    status: str
    location: str
    storage_account: str
    share: str
    vault: str
    detail: str
    share_key: tuple[str, str, str, str]


def normalize_location(value: str | None) -> str:
    return (value or "").replace(" ", "").casefold()


def subscription_from_resource_id(resource_id: str | None) -> str:
    if not resource_id:
        return ""
    parts = [unquote(part) for part in resource_id.strip("/").split("/")]
    lowered = [part.casefold() for part in parts]
    try:
        return parts[lowered.index("subscriptions") + 1]
    except (ValueError, IndexError):
        return ""


def resource_group_from_resource_id(resource_id: str | None) -> str:
    if not resource_id:
        return ""
    parts = [unquote(part) for part in resource_id.strip("/").split("/")]
    lowered = [part.casefold() for part in parts]
    try:
        return parts[lowered.index("resourcegroups") + 1]
    except (ValueError, IndexError):
        return ""


def named_resource_from_id(resource_id: str | None, resource_type: str) -> str:
    if not resource_id:
        return ""
    parts = [unquote(part) for part in resource_id.strip("/").split("/")]
    lowered = [part.casefold() for part in parts]
    try:
        return parts[lowered.index(resource_type.casefold()) + 1]
    except (ValueError, IndexError):
        return ""


def resource_identity(resource_id: str | None) -> tuple[str, str, str]:
    """Return (subscription, resource group, storage account) from an ARM ID."""

    if not resource_id:
        return "", "", ""
    parts = [unquote(part) for part in resource_id.strip("/").split("/")]
    lowered = [part.casefold() for part in parts]
    try:
        subscription_id = parts[lowered.index("subscriptions") + 1]
        resource_group = parts[lowered.index("resourcegroups") + 1]
        storage_account = parts[lowered.index("storageaccounts") + 1]
    except (ValueError, IndexError):
        return "", "", ""
    return subscription_id, resource_group, storage_account


def resource_coordinates(resource_id: str | None) -> tuple[str, str]:
    """Return (resource group, storage account) from an ARM resource ID."""

    _, resource_group, storage_account = resource_identity(resource_id)
    return resource_group, storage_account


def protected_item_from_json(
    item: dict[str, Any],
    vault_subscription_id: str,
    vault_resource_group: str,
    vault_name: str,
) -> ProtectedItem | None:
    properties = item.get("properties") or {}
    subscription_id, resource_group, storage_account = resource_identity(
        properties.get("sourceResourceId")
    )

    container_name = properties.get("containerName") or ""
    item_name = item.get("name") or ""
    item_id_parts = [unquote(part) for part in (item.get("id") or "").split("/")]
    lowered = [part.casefold() for part in item_id_parts]
    try:
        if not container_name:
            container_name = item_id_parts[lowered.index("protectioncontainers") + 1]
        if not item_name:
            item_name = item_id_parts[lowered.index("protecteditems") + 1]
    except (ValueError, IndexError):
        pass

    share_name = properties.get("friendlyName") or ""
    # The protected-item resource name may end in an opaque hash, so it is not
    # a trustworthy fallback for the share name.  Missing sourceResourceId or
    # friendlyName is therefore a fail-closed parse failure.
    if not all((subscription_id, resource_group, storage_account, share_name)):
        return None

    extended_info = properties.get("extendedInfo") or {}
    deferred_delete = properties.get("isScheduledForDeferredDelete")
    soft_deleted = (
        deferred_delete is True
        or str(deferred_delete or "").casefold() == "true"
        or str(extended_info.get("resourceState") or "").casefold()
        == "softdeleted"
    )

    return ProtectedItem(
        subscription_id=subscription_id,
        resource_group=resource_group,
        storage_account=storage_account,
        share_name=share_name,
        vault_resource_group=vault_resource_group,
        vault_name=vault_name,
        vault_subscription_id=vault_subscription_id,
        container_name=container_name,
        item_name=item_name,
        protection_state=str(properties.get("protectionState") or "Unknown"),
        health=str(
            properties.get("protectionStatus")
            or properties.get("healthStatus")
            or "Unknown"
        ),
        soft_deleted=soft_deleted,
    )


def backup_container_from_json(
    container: dict[str, Any],
    vault_subscription_id: str,
    vault_resource_group: str,
    vault_name: str,
) -> BackupContainer | None:
    properties = container.get("properties") or {}
    subscription_id, resource_group, storage_account = resource_identity(
        properties.get("sourceResourceId")
    )
    container_name = container.get("name") or ""
    registration_status = str(properties.get("registrationStatus") or "Unknown")
    if not all(
        (
            subscription_id,
            resource_group,
            storage_account,
            container_name,
            registration_status,
        )
    ):
        return None
    return BackupContainer(
        subscription_id=subscription_id,
        resource_group=resource_group,
        storage_account=storage_account,
        vault_subscription_id=vault_subscription_id,
        vault_resource_group=vault_resource_group,
        vault_name=vault_name,
        container_name=container_name,
        registration_status=registration_status,
    )


def target_from_azure(
    cli: AzureCli,
    subscription_id: str,
    vault_resource_group: str,
    vault_name: str,
    policy_name: str,
) -> Target:
    vault = cli.json(
        "backup",
        "vault",
        "show",
        "--resource-group",
        vault_resource_group,
        "--name",
        vault_name,
    )
    policy = cli.json(
        "backup",
        "policy",
        "show",
        "--resource-group",
        vault_resource_group,
        "--vault-name",
        vault_name,
        "--name",
        policy_name,
    )
    properties = (policy or {}).get("properties") or {}
    management_type = str(properties.get("backupManagementType") or "")
    workload_type = str(
        properties.get("workLoadType") or properties.get("workloadType") or ""
    )
    if (
        management_type.casefold() != "azurestorage"
        or workload_type.casefold() != "azurefileshare"
    ):
        raise AzureCliError(
            f"backup policy {policy_name!r} must use "
            "backupManagementType=AzureStorage and workLoadType=AzureFileShare"
        )
    location = normalize_location((vault or {}).get("location"))
    if not location:
        raise AzureCliError(f"Recovery Services vault {vault_name!r} has no location")
    vault_subscription_id = subscription_from_resource_id((vault or {}).get("id"))
    if vault_subscription_id and vault_subscription_id.casefold() != subscription_id.casefold():
        raise AzureCliError(
            "target vault resolved outside the selected subscription; "
            "this script supports same-subscription reconciliation only"
        )
    return Target(
        subscription_id,
        location,
        vault_resource_group,
        vault_name,
        policy_name,
    )


def list_file_shares(
    cli: AzureCli,
    source_resource_group: str | None,
    source_location: str | None = None,
) -> list[FileShare]:
    arguments = ["storage", "account", "list"]
    if source_resource_group:
        arguments.extend(["--resource-group", source_resource_group])
    accounts = cli.json(*arguments) or []
    shares: list[FileShare] = []
    for account in accounts:
        # Blob-only account kinds expose no Azure Files endpoint and reject
        # `az storage share-rm list` with FeatureNotSupportedForAccount. The
        # endpoint is a capability signal that also avoids hard-coding the
        # current set of storage-account kinds.
        if not ((account.get("primaryEndpoints") or {}).get("file")):
            continue
        account_name = account.get("name") or ""
        account_id = account.get("id") or ""
        account_subscription, id_resource_group, _ = resource_identity(account_id)
        account_rg = account.get("resourceGroup") or id_resource_group
        location = normalize_location(account.get("location"))
        if not all(
            (account_subscription, account_name, account_id, account_rg, location)
        ):
            raise AzureCliError("storage account inventory returned an incomplete resource")
        if source_location and location != normalize_location(source_location):
            continue
        account_shares = cli.json(
            "storage",
            "share-rm",
            "list",
            "--resource-group",
            account_rg,
            "--storage-account",
            account_name,
        ) or []
        for share in account_shares:
            name = share.get("name") or ""
            if not name:
                raise AzureCliError(
                    f"file-share inventory for {account_name!r} returned an unnamed share"
                )
            # ARM returns null for the normal default SMB protocol.
            protocol = str(share.get("enabledProtocols") or "SMB").upper()
            if protocol not in {"SMB", "NFS"}:
                raise AzureCliError(
                    f"file share {account_name}/{name} returned unknown "
                    f"enabledProtocols={protocol!r}"
                )
            shares.append(
                FileShare(
                    subscription_id=account_subscription,
                    resource_group=account_rg,
                    storage_account=account_name,
                    storage_account_id=account_id,
                    location=location,
                    name=name,
                    protocol=protocol,
                )
            )
    return shares


def list_vaults(
    cli: AzureCli, only_target: Target | None = None
) -> list[tuple[str, str, str]]:
    if only_target:
        return [
            (
                only_target.subscription_id,
                only_target.vault_resource_group,
                only_target.vault_name,
            )
        ]

    result: list[tuple[str, str, str]] = []
    for vault in cli.json("backup", "vault", "list") or []:
        vault_name = vault.get("name") or ""
        vault_rg = vault.get("resourceGroup") or ""
        vault_subscription_id = subscription_from_resource_id(vault.get("id"))
        if not all((vault_subscription_id, vault_name, vault_rg)):
            raise AzureCliError(
                "Recovery Services vault inventory returned an incomplete resource"
            )
        result.append((vault_subscription_id, vault_rg, vault_name))
    return result


def list_protected_items(
    cli: AzureCli,
    only_target: Target | None = None,
    timeout_seconds: float | None = None,
) -> list[ProtectedItem]:
    vaults = list_vaults(cli, only_target)

    protected: list[ProtectedItem] = []
    for vault_subscription_id, vault_rg, vault_name in vaults:
        items = cli.json(
            "backup",
            "item",
            "list",
            "--resource-group",
            vault_rg,
            "--vault-name",
            vault_name,
            "--backup-management-type",
            "AzureStorage",
            "--workload-type",
            "AzureFileShare",
            timeout_seconds=timeout_seconds,
        ) or []
        for raw_item in items:
            parsed = protected_item_from_json(
                raw_item, vault_subscription_id, vault_rg, vault_name
            )
            if not parsed:
                identity = raw_item.get("id") or raw_item.get("name") or "<unknown>"
                raise AzureCliError(
                    f"could not correlate Azure Files protected item {identity!r}; "
                    "refusing to propose protection changes"
                )
            protected.append(parsed)
    return protected


def list_deleted_vault_containers(
    cli: AzureCli, source_locations: Iterable[str]
) -> list[BackupContainer]:
    containers: list[BackupContainer] = []
    for location in sorted({normalize_location(value) for value in source_locations}):
        deleted_vaults = cli.json(
            "backup", "deleted-vault", "list", "--location", location
        ) or []
        for deleted_vault in deleted_vaults:
            deleted_vault_id = deleted_vault.get("id") or ""
            properties = deleted_vault.get("properties") or {}
            original_vault_id = properties.get("vaultId") or ""
            vault_subscription_id = subscription_from_resource_id(
                original_vault_id or deleted_vault_id
            )
            vault_rg = resource_group_from_resource_id(original_vault_id)
            vault_name = named_resource_from_id(original_vault_id, "vaults")
            if not all(
                (deleted_vault_id, vault_subscription_id, vault_rg, vault_name)
            ):
                raise AzureCliError(
                    "soft-deleted Recovery Services vault inventory returned "
                    "an incomplete resource"
                )
            deleted_vault_prefix = f"{deleted_vault_id.rstrip('/')}/"
            query = (
                "recoveryservicesresources\n"
                "| where type =~ "
                "'microsoft.recoveryservices/locations/deletedvaults/"
                "backupfabrics/protectioncontainers/protecteditems'\n"
                "| where tostring(id) startswith "
                f"{json.dumps(deleted_vault_prefix)}\n"
                "| order by id asc\n"
                "| project id, type, name, location, resourceGroup, "
                "subscriptionId, properties, tags"
            )
            items = cli.resource_graph_values(query, vault_subscription_id)
            for item in items:
                item_properties = item.get("properties") or {}
                if str(
                    item_properties.get("backupManagementType") or ""
                ).casefold() != "azurestorage":
                    continue
                source_id = item_properties.get("sourceResourceId") or (
                    item_properties.get("dataSourceSetInfo") or {}
                ).get("resourceID")
                subscription_id, resource_group, storage_account = resource_identity(
                    source_id
                )
                container_name = item_properties.get("containerName") or ""
                if not all(
                    (
                        subscription_id,
                        resource_group,
                        storage_account,
                        container_name,
                    )
                ):
                    identity = item.get("id") or item.get("name") or "<unknown>"
                    raise AzureCliError(
                        f"could not correlate soft-deleted Azure Storage item "
                        f"{identity!r}; refusing to propose protection changes"
                    )
                containers.append(
                    BackupContainer(
                        subscription_id=subscription_id,
                        resource_group=resource_group,
                        storage_account=storage_account,
                        vault_subscription_id=vault_subscription_id,
                        vault_resource_group=vault_rg,
                        vault_name=vault_name,
                        container_name=container_name,
                        registration_status="SoftDeletedVault",
                    )
                )
    return containers


def list_backup_containers(
    cli: AzureCli, source_locations: Iterable[str] = ()
) -> list[BackupContainer]:
    containers: list[BackupContainer] = []
    resource_manager = cli.resource_manager_endpoint()
    for vault_subscription_id, vault_rg, vault_name in list_vaults(cli):
        base_url = (
            f"{resource_manager}/subscriptions/"
            f"{quote(vault_subscription_id, safe='')}/resourceGroups/"
            f"{quote(vault_rg, safe='')}/providers/Microsoft.RecoveryServices/"
            f"vaults/{quote(vault_name, safe='')}/"
        )
        raw_containers: list[tuple[dict[str, Any], bool]] = []
        for endpoint, api_version in (
            ("backupProtectionContainers", "2025-08-01"),
            ("backupDeletedProtectionContainers", "2026-01-01"),
        ):
            url = (
                f"{base_url}{endpoint}?api-version={api_version}&%24filter="
                "backupManagementType%20eq%20%27AzureStorage%27"
            )
            is_deleted_endpoint = endpoint == "backupDeletedProtectionContainers"
            raw_containers.extend(
                (raw_container, is_deleted_endpoint)
                for raw_container in cli.rest_values(url)
            )
        for raw_container, is_deleted_endpoint in raw_containers:
            properties = raw_container.get("properties") or {}
            management_type = str(
                properties.get("backupManagementType") or ""
            ).casefold()
            container_type = str(
                properties.get("containerType") or ""
            ).casefold()
            if (
                management_type != "azurestorage"
                and container_type != "storagecontainer"
            ):
                continue
            registration_status = str(
                properties.get("registrationStatus") or "Unknown"
            )
            if not is_deleted_endpoint and registration_status.casefold() in {
                "notregistered",
                "unregistered",
            }:
                continue
            parsed = backup_container_from_json(
                raw_container, vault_subscription_id, vault_rg, vault_name
            )
            if not parsed:
                identity = (
                    raw_container.get("id")
                    or raw_container.get("name")
                    or "<unknown>"
                )
                raise AzureCliError(
                    f"could not correlate Azure Storage backup container "
                    f"{identity!r}; refusing to propose protection changes"
                )
            containers.append(parsed)
    containers.extend(list_deleted_vault_containers(cli, source_locations))

    deduplicated: dict[
        tuple[str, str, str, str, str, str, str], BackupContainer
    ] = {}
    for container in containers:
        key = (
            *container.account_key,
            container.vault_subscription_id.casefold(),
            container.vault_resource_group.casefold(),
            container.vault_name.casefold(),
            container.registration_status.casefold(),
        )
        deduplicated[key] = container
    return list(deduplicated.values())


def is_target_vault(
    vault_subscription_id: str,
    vault_resource_group: str,
    vault_name: str,
    target: Target,
) -> bool:
    return (
        vault_subscription_id.casefold() == target.subscription_id.casefold()
        and vault_resource_group.casefold()
        == target.vault_resource_group.casefold()
        and vault_name.casefold() == target.vault_name.casefold()
    )


def build_plan(
    shares: Iterable[FileShare],
    protected_items: Iterable[ProtectedItem],
    target: Target,
    registered_containers: Iterable[BackupContainer] = (),
) -> list[PlanEntry]:
    grouped: dict[tuple[str, str, str, str], list[ProtectedItem]] = {}
    for item in protected_items:
        grouped.setdefault(item.key, []).append(item)

    containers_by_account: dict[
        tuple[str, str, str], list[BackupContainer]
    ] = {}
    for container in registered_containers:
        containers_by_account.setdefault(container.account_key, []).append(container)

    def rank(item: ProtectedItem) -> tuple[int, str, str, str]:
        item_is_target = is_target_vault(
            item.vault_subscription_id,
            item.vault_resource_group,
            item.vault_name,
            target,
        )
        if item.active:
            priority = 0
        elif item.configured:
            priority = 1
        elif item.soft_deleted:
            priority = 2
        elif item_is_target:
            priority = 3
        else:
            priority = 4
        return (
            priority,
            item.vault_subscription_id.casefold(),
            item.vault_resource_group.casefold(),
            item.vault_name.casefold(),
        )

    protected_by_share = {
        key: sorted(items, key=rank)[0] for key, items in grouped.items()
    }
    entries: list[PlanEntry] = []
    for share in sorted(
        shares,
        key=lambda value: (
            value.location,
            value.resource_group.casefold(),
            value.storage_account.casefold(),
            value.name.casefold(),
        ),
    ):
        protected = protected_by_share.get(share.key)
        account_containers = containers_by_account.get(share.account_key, [])
        vault = ""
        detail = ""
        if share.protocol == "NFS":
            status = "UNSUPPORTED_NFS"
            detail = "Azure Backup does not support NFS Azure file shares"
        elif protected and protected.active:
            status = "PROTECTED"
            vault = f"{protected.vault_resource_group}/{protected.vault_name}"
            detail = f"state={protected.protection_state}; health={protected.health}"
        elif protected and protected.configured:
            status = "INITIAL_RECOVERY_PENDING"
            vault = f"{protected.vault_resource_group}/{protected.vault_name}"
            detail = f"state={protected.protection_state}; health={protected.health}"
        elif protected and protected.soft_deleted:
            status = "SOFT_DELETED"
            vault = f"{protected.vault_resource_group}/{protected.vault_name}"
            detail = (
                "manual soft-delete recovery or reconfiguration is required; "
                "this script will not mutate it"
            )
        elif share.location != target.location:
            status = "NO_TARGET_FOR_REGION"
            detail = f"target vault is in {target.location}"
        elif protected and not is_target_vault(
            protected.vault_subscription_id,
            protected.vault_resource_group,
            protected.vault_name,
            target,
        ):
            status = "INACTIVE_OTHER_VAULT"
            vault = f"{protected.vault_resource_group}/{protected.vault_name}"
            detail = "stop/unregister from the other vault before moving protection"
        elif protected and protected.resumable:
            status = "RESUME"
            vault = f"{target.vault_resource_group}/{target.vault_name}"
            detail = f"state={protected.protection_state}; health={protected.health}"
        elif protected:
            status = "PROTECTION_ERROR"
            vault = f"{target.vault_resource_group}/{target.vault_name}"
            detail = f"state={protected.protection_state}; health={protected.health}"
        elif any(
            not is_target_vault(
                container.vault_subscription_id,
                container.vault_resource_group,
                container.vault_name,
                target,
            )
            for container in account_containers
        ):
            status = "REGISTERED_OTHER_VAULT"
            foreign = sorted(
                (
                    container
                    for container in account_containers
                    if not is_target_vault(
                        container.vault_subscription_id,
                        container.vault_resource_group,
                        container.vault_name,
                        target,
                    )
                ),
                key=lambda value: (
                    value.vault_subscription_id.casefold(),
                    value.vault_resource_group.casefold(),
                    value.vault_name.casefold(),
                ),
            )[0]
            vault = f"{foreign.vault_resource_group}/{foreign.vault_name}"
            detail = (
                "storage account is registered to another vault; "
                f"status={foreign.registration_status}"
            )
        elif any(
            container.registration_status.casefold() != "registered"
            for container in account_containers
        ):
            status = "CONTAINER_NOT_READY"
            vault = f"{target.vault_resource_group}/{target.vault_name}"
            states = sorted(
                {container.registration_status for container in account_containers}
            )
            detail = "target-vault container status=" + ",".join(states)
        else:
            status = "ENABLE"
            vault = f"{target.vault_resource_group}/{target.vault_name}"
            detail = f"policy={target.policy_name}"
        entries.append(
            PlanEntry(
                status=status,
                location=share.location,
                storage_account=share.storage_account,
                share=share.name,
                vault=vault,
                detail=detail,
                share_key=share.key,
            )
        )
    return entries


def print_plan(entries: list[PlanEntry], json_output: bool) -> None:
    if json_output:
        print(json.dumps([asdict(entry) for entry in entries], indent=2))
        sys.stdout.flush()
        return
    headers = ("STATUS", "LOCATION", "STORAGE ACCOUNT", "SHARE", "VAULT", "DETAIL")
    rows = [
        (
            entry.status,
            entry.location,
            entry.storage_account,
            entry.share,
            entry.vault,
            entry.detail,
        )
        for entry in entries
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headers[index])
        for index in range(len(headers))
    ]
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    sys.stdout.flush()


def result_document(
    mode: str,
    target: Target,
    initial: list[PlanEntry],
    final: list[PlanEntry] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mode": mode,
        "target": asdict(target),
        "initial": [asdict(entry) for entry in initial],
    }
    if final is not None:
        result["final"] = [asdict(entry) for entry in final]
    return result


def apply_plan(
    cli: AzureCli,
    shares: list[FileShare],
    protected_items: list[ProtectedItem],
    registered_containers: list[BackupContainer],
    entries: list[PlanEntry],
    target: Target,
    wait_seconds: int,
    poll_seconds: int,
    max_actions: int,
    max_new_registrations: int,
) -> None:
    shares_by_key = {share.key: share for share in shares}
    pending: set[tuple[str, str, str, str]] = set()

    blockers = {
        entry.status
        for entry in entries
        if entry.status
        in {
            "CONTAINER_NOT_READY",
            "INACTIVE_OTHER_VAULT",
            "NO_TARGET_FOR_REGION",
            "PROTECTION_ERROR",
            "REGISTERED_OTHER_VAULT",
            "SOFT_DELETED",
        }
    }
    if blockers:
        raise AzureCliError(
            "cannot achieve complete coverage: " + ", ".join(sorted(blockers))
        )

    actions = [entry for entry in entries if entry.status in {"ENABLE", "RESUME"}]
    if len(actions) > max_actions:
        raise AzureCliError(
            f"plan has {len(actions)} actions, above --max-actions={max_actions}; "
            "run smaller region/resource-group waves"
        )

    target_account_keys = {
        container.account_key
        for container in registered_containers
        if is_target_vault(
            container.vault_subscription_id,
            container.vault_resource_group,
            container.vault_name,
            target,
        )
        and container.registration_status.casefold() == "registered"
    }
    new_account_keys = {
        shares_by_key[entry.share_key].account_key
        for entry in actions
        if entry.status == "ENABLE"
        and shares_by_key[entry.share_key].account_key not in target_account_keys
    }
    if len(new_account_keys) > max_new_registrations:
        raise AzureCliError(
            f"plan needs {len(new_account_keys)} new storage-account registrations, "
            f"above --max-new-registrations={max_new_registrations}; run smaller waves"
        )
    if len(target_account_keys | new_account_keys) > 200:
        raise AzureCliError(
            "plan would exceed the 200 registered storage accounts per vault limit"
        )

    target_item_keys = {
        item.key
        for item in protected_items
        if is_target_vault(
            item.vault_subscription_id,
            item.vault_resource_group,
            item.vault_name,
            target,
        )
    }
    new_item_keys = {
        entry.share_key for entry in actions if entry.status == "ENABLE"
    }
    if len(target_item_keys | new_item_keys) > 2_000:
        raise AzureCliError(
            "plan would exceed the 2,000 protected shares per vault limit"
        )

    deadline = time.monotonic() + wait_seconds

    def remaining_command_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AzureCliError(
                "timed out before all Azure Files protection actions were submitted"
            )
        return remaining

    for entry in entries:
        if entry.status == "ENABLE":
            share = shares_by_key[entry.share_key]
            cli.json(
                "backup",
                "protection",
                "enable-for-azurefileshare",
                "--resource-group",
                target.vault_resource_group,
                "--vault-name",
                target.vault_name,
                "--policy-name",
                target.policy_name,
                "--storage-account",
                share.storage_account,
                "--azure-file-share",
                share.name,
                timeout_seconds=remaining_command_timeout(),
            )
            pending.add(entry.share_key)
        elif entry.status == "RESUME":
            candidates = [
                item
                for item in protected_items
                if item.key == entry.share_key
                and is_target_vault(
                    item.vault_subscription_id,
                    item.vault_resource_group,
                    item.vault_name,
                    target,
                )
            ]
            if not candidates:
                raise AzureCliError(
                    f"target-vault protected item disappeared for {entry.share!r}"
                )
            item = sorted(
                candidates,
                key=lambda value: (value.container_name, value.item_name),
            )[0]
            cli.json(
                "backup",
                "protection",
                "resume",
                "--resource-group",
                target.vault_resource_group,
                "--vault-name",
                target.vault_name,
                "--container-name",
                item.container_name or item.storage_account,
                "--item-name",
                item.item_name or item.share_name,
                "--backup-management-type",
                "AzureStorage",
                "--workload-type",
                "AzureFileShare",
                "--policy-name",
                target.policy_name,
                timeout_seconds=remaining_command_timeout(),
            )
            pending.add(entry.share_key)

    if not pending:
        return

    while True:
        current = {
            item.key: item
            for item in list_protected_items(
                cli, target, timeout_seconds=remaining_command_timeout()
            )
        }
        pending = {
            key for key in pending if key not in current or not current[key].configured
        }
        if not pending:
            return
        if time.monotonic() >= deadline:
            unresolved = ", ".join("/".join(key) for key in sorted(pending))
            raise AzureCliError(
                f"timed out waiting for configured Azure Files protection: {unresolved}"
            )
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Audit or enable Azure Backup for every SMB Azure file share in a "
            "subscription or resource group. Dry-run is the default."
        )
    )
    result.add_argument("--vault-resource-group", required=True)
    result.add_argument("--vault-name", required=True)
    result.add_argument("--policy-name", required=True)
    result.add_argument(
        "--source-resource-group",
        help="Limit source storage accounts to one resource group (default: subscription)",
    )
    result.add_argument(
        "--source-location",
        help="Limit source storage accounts to one normalized Azure region",
    )
    result.add_argument("--subscription", help="Azure subscription name or ID")
    result.add_argument(
        "--apply",
        action="store_true",
        help="Enable/resume protection; without this flag the script only reports",
    )
    result.add_argument("--wait-seconds", type=int, default=1800)
    result.add_argument("--poll-seconds", type=int, default=20)
    result.add_argument(
        "--command-timeout-seconds",
        type=int,
        default=300,
        help="Timeout for each Azure CLI call (default: 300)",
    )
    result.add_argument(
        "--max-actions",
        type=int,
        default=200,
        help="Refuse an apply wave above this many share actions (maximum: 200)",
    )
    result.add_argument(
        "--max-new-registrations",
        type=int,
        default=50,
        help=(
            "Refuse an apply wave above this many new storage-account "
            "registrations (maximum: 50)"
        ),
    )
    result.add_argument("--json", action="store_true", dest="json_output")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if min(
        arguments.wait_seconds,
        arguments.poll_seconds,
        arguments.command_timeout_seconds,
    ) < 1:
        print(
            "--wait-seconds, --poll-seconds, and --command-timeout-seconds "
            "must be positive",
            file=sys.stderr,
        )
        return 1
    if not 1 <= arguments.max_actions <= 200:
        print("--max-actions must be between 1 and 200", file=sys.stderr)
        return 1
    if not 1 <= arguments.max_new_registrations <= 50:
        print(
            "--max-new-registrations must be between 1 and 50", file=sys.stderr
        )
        return 1

    cli = AzureCli(arguments.subscription, arguments.command_timeout_seconds)
    try:
        account = cli.json("account", "show") or {}
        subscription_id = str(account.get("id") or "")
        if not subscription_id:
            raise AzureCliError("could not resolve the selected Azure subscription ID")
        target = target_from_azure(
            cli,
            subscription_id,
            arguments.vault_resource_group,
            arguments.vault_name,
            arguments.policy_name,
        )
        shares = list_file_shares(
            cli, arguments.source_resource_group, arguments.source_location
        )
        source_locations = {share.location for share in shares}
        protected_items = list_protected_items(cli)
        registered_containers = list_backup_containers(cli, source_locations)
        plan = build_plan(shares, protected_items, target, registered_containers)

        actions = [entry for entry in plan if entry.status in {"ENABLE", "RESUME"}]
        blockers = [
            entry
            for entry in plan
            if entry.status
            in {
                "CONTAINER_NOT_READY",
                "INACTIVE_OTHER_VAULT",
                "NO_TARGET_FOR_REGION",
                "PROTECTION_ERROR",
                "REGISTERED_OTHER_VAULT",
                "SOFT_DELETED",
            }
        ]
        exceptions = [entry for entry in plan if entry.status == "UNSUPPORTED_NFS"]
        if not arguments.apply:
            if arguments.json_output:
                print(json.dumps(result_document("dry-run", target, plan), indent=2))
            else:
                print_plan(plan, False)
            if actions or blockers or exceptions:
                print(
                    f"DRY RUN: {len(actions)} protection action(s), "
                    f"{len(blockers)} blocker(s), "
                    f"{len(exceptions)} unsupported NFS exception(s). "
                    "Re-run with --apply after reviewing the scope.",
                    file=sys.stderr,
                )
                return 2
            return 0

        apply_plan(
            cli,
            shares,
            protected_items,
            registered_containers,
            plan,
            target,
            arguments.wait_seconds,
            arguments.poll_seconds,
            arguments.max_actions,
            arguments.max_new_registrations,
        )
        final_items = list_protected_items(cli)
        final_containers = list_backup_containers(cli, source_locations)
        final_plan = build_plan(shares, final_items, target, final_containers)
        if arguments.json_output:
            print(
                json.dumps(
                    result_document("apply", target, plan, final_plan), indent=2
                )
            )
        elif actions:
            print_plan(plan, False)
            print("\nFinal state:")
            print_plan(final_plan, False)
        else:
            print_plan(final_plan, False)
        unresolved = [
            entry
            for entry in final_plan
            if entry.status
            not in {"INITIAL_RECOVERY_PENDING", "PROTECTED", "UNSUPPORTED_NFS"}
        ]
        if unresolved:
            raise AzureCliError(
                f"{len(unresolved)} eligible file share(s) remain without configured protection"
            )
        initial_recovery_pending = [
            entry
            for entry in final_plan
            if entry.status == "INITIAL_RECOVERY_PENDING"
        ]
        if initial_recovery_pending:
            print(
                f"NOTICE: {len(initial_recovery_pending)} share(s) have healthy "
                "configuration but no completed initial recovery recorded yet.",
                file=sys.stderr,
            )
        unsupported_nfs = [
            entry for entry in final_plan if entry.status == "UNSUPPORTED_NFS"
        ]
        if unsupported_nfs:
            print(
                f"ERROR: {len(unsupported_nfs)} NFS share(s) are unsupported by "
                "Azure Backup; eligible SMB reconciliation completed but full "
                "scope coverage is impossible.",
                file=sys.stderr,
            )
            return 3
        return 0
    except AzureCliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

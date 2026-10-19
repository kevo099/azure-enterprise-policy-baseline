import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "ensure_file_share_backup.py"
SPEC = importlib.util.spec_from_file_location("ensure_file_share_backup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeCli:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def json(
        self, *arguments, timeout_seconds=None, include_subscription=True
    ):
        self.calls.append(arguments)
        key = arguments[:3]
        response = self.responses.get(key)
        if callable(response):
            return response(arguments)
        if response is None and key not in self.responses:
            raise AssertionError(f"unexpected Azure CLI call: {arguments}")
        return response

    def rest_values(self, url):
        response = self.json("rest", "--method", "get", "--url", url)
        return response["value"]

    def resource_manager_endpoint(self):
        return "https://management.azure.com"

    def resource_graph_values(self, query, subscription_id):
        return MODULE.AzureCli.resource_graph_values(self, query, subscription_id)


def share(
    name="data", protocol="SMB", location="eastus2", storage_account="stsource"
):
    return MODULE.FileShare(
        subscription_id="sub",
        resource_group="rg-source",
        storage_account=storage_account,
        storage_account_id=(
            "/subscriptions/sub/resourceGroups/rg-source/providers/"
            f"Microsoft.Storage/storageAccounts/{storage_account}"
        ),
        location=location,
        name=name,
        protocol=protocol,
    )


def protected(
    name="data",
    state="Protected",
    vault="rsv-east",
    vault_resource_group="rg-backup",
    health="Healthy",
    source_subscription="sub",
    vault_subscription="sub",
    soft_deleted=False,
):
    return MODULE.ProtectedItem(
        subscription_id=source_subscription,
        resource_group="rg-source",
        storage_account="stsource",
        share_name=name,
        vault_resource_group=vault_resource_group,
        vault_name=vault,
        vault_subscription_id=vault_subscription,
        container_name="storagecontainer;Storage;rg-source;stsource",
        item_name=f"AzureFileShare;{name}",
        protection_state=state,
        health=health,
        soft_deleted=soft_deleted,
    )


TARGET = MODULE.Target("sub", "eastus2", "rg-backup", "rsv-east", "afs-daily")


class InventoryTests(unittest.TestCase):
    def test_json_command_timeout_is_reported(self):
        cli = MODULE.AzureCli(command_timeout_seconds=7)
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            side_effect=MODULE.subprocess.TimeoutExpired(["az"], 7),
        ):
            with self.assertRaisesRegex(MODULE.AzureCliError, "exceeded 7s"):
                cli.json("account", "show")

    def test_json_command_reports_missing_azure_cli(self):
        cli = MODULE.AzureCli()
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            side_effect=FileNotFoundError("az"),
        ):
            with self.assertRaisesRegex(MODULE.AzureCliError, "was not found"):
                cli.json("account", "show")

    def test_rest_values_validates_and_follows_next_link(self):
        cli = MODULE.AzureCli()
        with mock.patch.object(
            cli,
            "json",
            side_effect=[
                {"value": [{"name": "one"}], "nextLink": "https://next"},
                {"value": [{"name": "two"}]},
            ],
        ) as json_call:
            values = cli.rest_values("https://first")
        self.assertEqual([value["name"] for value in values], ["one", "two"])
        self.assertEqual(json_call.call_count, 2)

    def test_resource_graph_values_follows_skip_token(self):
        def graph_response(arguments):
            body = MODULE.json.loads(arguments[arguments.index("--body") + 1])
            options = body["options"]
            self.assertEqual(body["subscriptions"], ["sub"])
            self.assertEqual(options["$top"], 1000)
            if "$skipToken" not in options:
                return {
                    "count": 1,
                    "data": [{"id": "/items/one"}],
                    "$skipToken": "page-two",
                    "resultTruncated": "false",
                    "totalRecords": 2,
                }
            self.assertEqual(options["$skipToken"], "page-two")
            return {
                "count": 1,
                "data": [{"id": "/items/two"}],
                "resultTruncated": "false",
                "totalRecords": 2,
            }

        cli = FakeCli({("rest", "--method", "post"): graph_response})
        values = cli.resource_graph_values(
            "Resources | order by id asc | project id", "sub"
        )
        self.assertEqual([value["id"] for value in values], ["/items/one", "/items/two"])

    def test_resource_graph_values_rejects_truncated_page_without_token(self):
        cli = FakeCli(
            {
                ("rest", "--method", "post"): {
                    "count": 1,
                    "data": [{"id": "/items/one"}],
                    "resultTruncated": "true",
                    "totalRecords": 2,
                }
            }
        )
        with self.assertRaisesRegex(MODULE.AzureCliError, "incomplete"):
            cli.resource_graph_values(
                "Resources | order by id asc | project id", "sub"
            )

    def test_resource_manager_endpoint_uses_active_cloud_without_subscription(self):
        cli = MODULE.AzureCli("sub")
        with mock.patch.object(
            cli,
            "json",
            return_value={
                "endpoints": {"resourceManager": "https://management.example/"}
            },
        ) as json_call:
            self.assertEqual(
                cli.resource_manager_endpoint(), "https://management.example"
            )
        json_call.assert_called_once_with("cloud", "show", include_subscription=False)

    def test_resource_coordinates_accept_nested_share_id(self):
        resource_group, account = MODULE.resource_coordinates(
            "/subscriptions/sub/resourceGroups/RG-One/providers/Microsoft.Storage/"
            "storageAccounts/StOne/fileServices/default/shares/data"
        )
        self.assertEqual((resource_group, account), ("RG-One", "StOne"))

    def test_parse_protected_item_from_source_id(self):
        item = MODULE.protected_item_from_json(
            {
                "id": (
                    "/subscriptions/sub/resourceGroups/rg-backup/providers/"
                    "Microsoft.RecoveryServices/vaults/rsv/backupFabrics/Azure/"
                    "protectionContainers/storagecontainer%3BStorage%3Brg-source%3Bstsource/"
                    "protectedItems/AzureFileShare%3Bdata"
                ),
                "name": "AzureFileShare;data",
                "properties": {
                    "friendlyName": "data",
                    "sourceResourceId": (
                        "/subscriptions/sub/resourceGroups/rg-source/providers/"
                        "Microsoft.Storage/storageAccounts/stsource"
                    ),
                    "protectionState": "Protected",
                    "protectionStatus": "Healthy",
                },
            },
            "sub",
            "rg-backup",
            "rsv",
        )
        self.assertIsNotNone(item)
        self.assertEqual(item.key, ("sub", "rg-source", "stsource", "data"))
        self.assertTrue(item.active)

    def test_parse_protected_item_rejects_untrusted_resource_name_fallback(self):
        item = MODULE.protected_item_from_json(
            {
                "id": (
                    "/subscriptions/sub/resourceGroups/rg-backup/providers/"
                    "Microsoft.RecoveryServices/vaults/rsv/backupFabrics/Azure/"
                    "protectionContainers/storagecontainer%3BStorage%3Brg-source%3Bstsource/"
                    "protectedItems/AzureFileShare%3Bdata"
                ),
                "properties": {},
            },
            "sub",
            "rg-backup",
            "rsv",
        )
        self.assertIsNone(item)

    def test_parse_protected_item_honors_deferred_delete_flag(self):
        item = MODULE.protected_item_from_json(
            {
                "name": "AzureFileShare;opaque",
                "properties": {
                    "friendlyName": "data",
                    "sourceResourceId": (
                        "/subscriptions/sub/resourceGroups/rg-source/providers/"
                        "Microsoft.Storage/storageAccounts/stsource"
                    ),
                    "protectionState": "ProtectionStopped",
                    "protectionStatus": "Healthy",
                    "isScheduledForDeferredDelete": True,
                },
            },
            "sub",
            "rg-backup",
            "rsv",
        )
        self.assertIsNotNone(item)
        self.assertTrue(item.soft_deleted)
        self.assertFalse(item.resumable)

    def test_arm_null_protocol_is_treated_as_smb(self):
        cli = FakeCli(
            {
                ("storage", "account", "list"): [
                    {
                        "id": (
                            "/subscriptions/sub/resourceGroups/rg-source/providers/"
                            "Microsoft.Storage/storageAccounts/stsource"
                        ),
                        "name": "stsource",
                        "resourceGroup": "rg-source",
                        "location": "East US 2",
                        "primaryEndpoints": {
                            "file": "https://stsource.file.core.windows.net/"
                        },
                    }
                ],
                ("storage", "share-rm", "list"): [
                    {"name": "default-smb", "enabledProtocols": None},
                    {"name": "explicit-nfs", "enabledProtocols": "NFS"},
                ],
            }
        )
        shares = MODULE.list_file_shares(cli, "rg-source")
        self.assertEqual([entry.protocol for entry in shares], ["SMB", "NFS"])
        self.assertEqual({entry.location for entry in shares}, {"eastus2"})

    def test_source_location_filters_accounts_before_share_inventory(self):
        def list_shares(arguments):
            account = arguments[arguments.index("--storage-account") + 1]
            return [{"name": f"{account}-share", "enabledProtocols": "SMB"}]

        cli = FakeCli(
            {
                ("storage", "account", "list"): [
                    {
                        "id": "/subscriptions/sub/resourceGroups/rg-source/providers/Microsoft.Storage/storageAccounts/steast",
                        "name": "steast",
                        "resourceGroup": "rg-source",
                        "location": "eastus2",
                        "primaryEndpoints": {
                            "file": "https://steast.file.core.windows.net/"
                        },
                    },
                    {
                        "id": "/subscriptions/sub/resourceGroups/rg-source/providers/Microsoft.Storage/storageAccounts/stwest",
                        "name": "stwest",
                        "resourceGroup": "rg-source",
                        "location": "westus2",
                        "primaryEndpoints": {
                            "file": "https://stwest.file.core.windows.net/"
                        },
                    },
                ],
                ("storage", "share-rm", "list"): list_shares,
            }
        )
        shares = MODULE.list_file_shares(cli, None, "eastus2")
        self.assertEqual([entry.storage_account for entry in shares], ["steast"])

    def test_blob_only_accounts_are_skipped_before_share_inventory(self):
        def list_shares(arguments):
            account = arguments[arguments.index("--storage-account") + 1]
            self.assertEqual(account, "stfiles")
            return [{"name": "data", "enabledProtocols": "SMB"}]

        cli = FakeCli(
            {
                ("storage", "account", "list"): [
                    {
                        "id": "/subscriptions/sub/resourceGroups/rg-source/providers/Microsoft.Storage/storageAccounts/stfiles",
                        "name": "stfiles",
                        "resourceGroup": "rg-source",
                        "location": "eastus2",
                        "primaryEndpoints": {
                            "file": "https://stfiles.file.core.windows.net/"
                        },
                    },
                    {
                        "id": "/subscriptions/sub/resourceGroups/rg-source/providers/Microsoft.Storage/storageAccounts/stblob",
                        "name": "stblob",
                        "resourceGroup": "rg-source",
                        "location": "eastus2",
                        "kind": "BlockBlobStorage",
                        "primaryEndpoints": {"file": None},
                    },
                ],
                ("storage", "share-rm", "list"): list_shares,
            }
        )
        shares = MODULE.list_file_shares(cli, None)
        self.assertEqual([entry.storage_account for entry in shares], ["stfiles"])
        share_calls = [
            call for call in cli.calls if call[:3] == ("storage", "share-rm", "list")
        ]
        self.assertEqual(len(share_calls), 1)
        self.assertIn("stfiles", share_calls[0])
        self.assertNotIn("stblob", share_calls[0])

    def test_unknown_share_protocol_fails_closed(self):
        cli = FakeCli(
            {
                ("storage", "account", "list"): [
                    {
                        "id": (
                            "/subscriptions/sub/resourceGroups/rg-source/providers/"
                            "Microsoft.Storage/storageAccounts/stsource"
                        ),
                        "name": "stsource",
                        "resourceGroup": "rg-source",
                        "location": "eastus2",
                        "primaryEndpoints": {
                            "file": "https://stsource.file.core.windows.net/"
                        },
                    }
                ],
                ("storage", "share-rm", "list"): [
                    {"name": "future", "enabledProtocols": "FutureProtocol"}
                ],
            }
        )
        with self.assertRaises(MODULE.AzureCliError):
            MODULE.list_file_shares(cli, "rg-source")

    def test_unparseable_protected_item_fails_inventory(self):
        cli = FakeCli(
            {
                ("backup", "vault", "list"): [
                    {
                        "id": "/subscriptions/sub/resourceGroups/rg-backup/providers/Microsoft.RecoveryServices/vaults/rsv",
                        "name": "rsv",
                        "resourceGroup": "rg-backup",
                    }
                ],
                ("backup", "item", "list"): [
                    {"id": "/malformed/protected/item", "properties": {}}
                ],
            }
        )
        with self.assertRaises(MODULE.AzureCliError):
            MODULE.list_protected_items(cli)

    def test_share_keys_include_source_subscription(self):
        other = MODULE.FileShare(
            subscription_id="other-sub",
            resource_group="rg-source",
            storage_account="stsource",
            storage_account_id=(
                "/subscriptions/other-sub/resourceGroups/rg-source/providers/"
                "Microsoft.Storage/storageAccounts/stsource"
            ),
            location="eastus2",
            name="data",
            protocol="SMB",
        )
        self.assertNotEqual(share().key, other.key)

    def test_registered_container_inventory_keeps_soft_deleted_fail_closed(self):
        vault = {
            "id": "/subscriptions/sub/resourceGroups/rg-backup/providers/Microsoft.RecoveryServices/vaults/rsv-east",
            "name": "rsv-east",
            "resourceGroup": "rg-backup",
        }
        source_id = (
            "/subscriptions/sub/resourceGroups/rg-source/providers/"
            "Microsoft.Storage/storageAccounts/stsource"
        )

        def container_response(arguments):
            url = arguments[-1]
            if "backupDeletedProtectionContainers" in url:
                return {
                    "value": [
                        {
                            "name": "StorageContainer;Storage;rg-old;stold",
                            "properties": {
                                "backupManagementType": "AzureStorage",
                                "containerType": "StorageContainer",
                                "sourceResourceId": (
                                    "/subscriptions/sub/resourceGroups/rg-old/"
                                    "providers/Microsoft.Storage/storageAccounts/stold"
                                ),
                                "registrationStatus": "NotRegistered",
                            },
                        }
                    ]
                }
            return {
                "value": [
                    {
                        "name": "StorageContainer;Storage;rg-source;stsource",
                        "properties": {
                            "backupManagementType": "AzureStorage",
                            "containerType": "StorageContainer",
                            "sourceResourceId": source_id,
                            "registrationStatus": "Registered",
                        },
                    },
                    {
                        "name": "StorageContainer;Storage;rg-old;stinactive",
                        "properties": {
                            "backupManagementType": "AzureStorage",
                            "containerType": "StorageContainer",
                            "sourceResourceId": (
                                "/subscriptions/sub/resourceGroups/rg-old/"
                                "providers/Microsoft.Storage/storageAccounts/stinactive"
                            ),
                            "registrationStatus": "NotRegistered",
                        },
                    },
                ]
            }

        cli = FakeCli(
            {
                ("backup", "vault", "list"): [vault],
                ("rest", "--method", "get"): container_response,
            }
        )
        containers = MODULE.list_backup_containers(cli)
        self.assertEqual(len(containers), 2)
        self.assertEqual(containers[0].account_key, ("sub", "rg-source", "stsource"))
        self.assertEqual(containers[1].registration_status, "NotRegistered")
        rest_urls = [call[-1] for call in cli.calls if call[0] == "rest"]
        self.assertTrue(any("backupProtectionContainers" in url for url in rest_urls))
        self.assertTrue(
            any("backupDeletedProtectionContainers" in url for url in rest_urls)
        )

    def test_whole_deleted_vault_items_block_at_storage_account_level(self):
        deleted_vault_id = (
            "/subscriptions/sub/providers/Microsoft.RecoveryServices/locations/"
            "eastus2/deletedVaults/deleted-record"
        )
        original_vault_id = (
            "/subscriptions/sub/resourceGroups/rg-old-backup/providers/"
            "Microsoft.RecoveryServices/vaults/rsv-deleted"
        )
        cli = FakeCli(
            {
                ("backup", "vault", "list"): [],
                ("backup", "deleted-vault", "list"): [
                    {
                        "id": deleted_vault_id,
                        "properties": {"vaultId": original_vault_id},
                    }
                ],
                ("rest", "--method", "post"): {
                    "count": 1,
                    "data": [
                        {
                            "id": f"{deleted_vault_id}/backupFabrics/Azure/protectedItems/opaque",
                            "name": "AzureFileShare;opaque",
                            "properties": {
                                "backupManagementType": "AzureStorage",
                                "containerName": (
                                    "StorageContainer;storage;rg-source;stsource"
                                ),
                                "friendlyName": "old-share",
                                "sourceResourceId": (
                                    "/subscriptions/sub/resourceGroups/rg-source/"
                                    "providers/Microsoft.Storage/storageAccounts/stsource"
                                ),
                                "isScheduledForDeferredDelete": True,
                            },
                        }
                    ],
                    "resultTruncated": "false",
                    "totalRecords": 1,
                },
            }
        )
        containers = MODULE.list_backup_containers(cli, ["eastus2"])
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0].registration_status, "SoftDeletedVault")
        self.assertEqual(containers[0].vault_name, "rsv-deleted")
        plan = MODULE.build_plan([share("new")], [], TARGET, containers)
        self.assertEqual(plan[0].status, "REGISTERED_OTHER_VAULT")


class PlanTests(unittest.TestCase):
    def test_only_explicit_healthy_states_are_configured(self):
        active = protected(state="Protected")
        pending = protected(state="IRPending")
        self.assertTrue(active.active)
        self.assertTrue(active.configured)
        self.assertFalse(pending.active)
        self.assertTrue(pending.configured)

        for state in (
            "Invalid",
            "ProtectionError",
            "ProtectionPaused",
            "ProtectionStopped",
            "Unknown",
        ):
            with self.subTest(state=state):
                item = protected(state=state)
                self.assertFalse(item.active)
                self.assertFalse(item.configured)
        self.assertFalse(protected(state="Protected", health="Unknown").active)
        self.assertTrue(protected(state="BackupsSuspended").resumable)

    def test_plan_reports_every_meaningful_state(self):
        shares = [
            share("new"),
            share("good"),
            share("pending"),
            share("stopped"),
            share("errored"),
            share("foreign"),
            share("nfs", protocol="NFS"),
            share("west", location="westus2"),
            share("deleted"),
        ]
        items = [
            protected("good"),
            protected("pending", state="IRPending"),
            protected("stopped", state="ProtectionStopped"),
            protected("errored", state="ProtectionError"),
            protected("foreign", state="ProtectionStopped", vault="rsv-other"),
            protected("deleted", state="ProtectionStopped", soft_deleted=True),
        ]
        plan = MODULE.build_plan(shares, items, TARGET)
        statuses = {entry.share: entry.status for entry in plan}
        self.assertEqual(
            statuses,
            {
                "new": "ENABLE",
                "good": "PROTECTED",
                "pending": "INITIAL_RECOVERY_PENDING",
                "stopped": "RESUME",
                "errored": "PROTECTION_ERROR",
                "foreign": "INACTIVE_OTHER_VAULT",
                "nfs": "UNSUPPORTED_NFS",
                "west": "NO_TARGET_FOR_REGION",
                "deleted": "SOFT_DELETED",
            },
        )

    def test_foreign_registration_blocks_unprotected_share_at_account_level(self):
        registration = MODULE.BackupContainer(
            subscription_id="sub",
            resource_group="rg-source",
            storage_account="stsource",
            vault_subscription_id="sub",
            vault_resource_group="rg-other",
            vault_name="rsv-other",
            container_name="StorageContainer;Storage;rg-source;stsource",
            registration_status="Registered",
        )
        plan = MODULE.build_plan([share("new")], [], TARGET, [registration])
        self.assertEqual(plan[0].status, "REGISTERED_OTHER_VAULT")

    def test_target_registration_still_allows_new_share_enable(self):
        registration = MODULE.BackupContainer(
            subscription_id="sub",
            resource_group="rg-source",
            storage_account="stsource",
            vault_subscription_id="sub",
            vault_resource_group="rg-backup",
            vault_name="rsv-east",
            container_name="StorageContainer;Storage;rg-source;stsource",
            registration_status="Registered",
        )
        plan = MODULE.build_plan([share("new")], [], TARGET, [registration])
        self.assertEqual(plan[0].status, "ENABLE")

    def test_soft_deleted_target_container_blocks_enable(self):
        registration = MODULE.BackupContainer(
            subscription_id="sub",
            resource_group="rg-source",
            storage_account="stsource",
            vault_subscription_id="sub",
            vault_resource_group="rg-backup",
            vault_name="rsv-east",
            container_name="StorageContainer;Storage;rg-source;stsource",
            registration_status="SoftDeleted",
        )
        plan = MODULE.build_plan([share("new")], [], TARGET, [registration])
        self.assertEqual(plan[0].status, "CONTAINER_NOT_READY")

    def test_active_item_wins_if_multiple_vault_records_exist(self):
        plan = MODULE.build_plan(
            [share("data")],
            [
                protected("data", state="ProtectionStopped", vault="rsv-old"),
                protected("data", state="Protected", vault="rsv-current"),
            ],
            TARGET,
        )
        self.assertEqual(plan[0].status, "PROTECTED")
        self.assertTrue(plan[0].vault.endswith("/rsv-current"))

    def test_active_other_region_is_not_a_target_region_blocker(self):
        plan = MODULE.build_plan(
            [share("data", location="westus2")], [protected("data")], TARGET
        )
        self.assertEqual(plan[0].status, "PROTECTED")

    def test_target_inactive_item_wins_regardless_of_inventory_order(self):
        target_item = protected("data", state="ProtectionStopped")
        foreign_item = protected(
            "data", state="ProtectionStopped", vault="rsv-foreign"
        )
        for items in ([target_item, foreign_item], [foreign_item, target_item]):
            with self.subTest(items=items):
                plan = MODULE.build_plan([share("data")], items, TARGET)
                self.assertEqual(plan[0].status, "RESUME")

    def test_blocker_preflight_prevents_all_writes(self):
        shares = [share("new"), share("west", location="westus2")]
        plan = MODULE.build_plan(shares, [], TARGET)
        cli = FakeCli({})
        with self.assertRaises(MODULE.AzureCliError):
            MODULE.apply_plan(
                cli,
                shares,
                [],
                [],
                plan,
                TARGET,
                wait_seconds=1,
                poll_seconds=1,
                max_actions=200,
                max_new_registrations=50,
            )
        self.assertEqual(cli.calls, [])

    def test_apply_enables_and_waits_for_active_item(self):
        current_items = []

        def enable(_arguments):
            current_items.append(protected("new"))
            return {"status": "Accepted"}

        def list_items(_arguments):
            return [
                {
                    "name": item.item_name,
                    "properties": {
                        "containerName": item.container_name,
                        "friendlyName": item.share_name,
                        "sourceResourceId": (
                            "/subscriptions/sub/resourceGroups/rg-source/providers/"
                            "Microsoft.Storage/storageAccounts/stsource"
                        ),
                        "protectionState": item.protection_state,
                        "protectionStatus": item.health,
                    },
                }
                for item in current_items
            ]

        cli = FakeCli(
            {
                ("backup", "protection", "enable-for-azurefileshare"): enable,
                ("backup", "item", "list"): list_items,
            }
        )
        shares = [share("new")]
        plan = MODULE.build_plan(shares, [], TARGET)
        MODULE.apply_plan(
            cli,
            shares,
            [],
            [],
            plan,
            TARGET,
            wait_seconds=1,
            poll_seconds=1,
            max_actions=200,
            max_new_registrations=50,
        )
        enable_calls = [
            call for call in cli.calls if call[:3] == ("backup", "protection", "enable-for-azurefileshare")
        ]
        self.assertEqual(len(enable_calls), 1)
        self.assertIn("--azure-file-share", enable_calls[0])
        self.assertIn("new", enable_calls[0])

    def test_apply_resumes_target_item_and_accepts_ir_pending(self):
        current_items = []
        stopped = protected("stopped", state="ProtectionStopped")

        def resume(_arguments):
            current_items.append(protected("stopped", state="IRPending"))
            return {"status": "Accepted"}

        def list_items(_arguments):
            return [
                {
                    "name": item.item_name,
                    "properties": {
                        "containerName": item.container_name,
                        "friendlyName": item.share_name,
                        "sourceResourceId": (
                            "/subscriptions/sub/resourceGroups/rg-source/providers/"
                            "Microsoft.Storage/storageAccounts/stsource"
                        ),
                        "protectionState": item.protection_state,
                        "protectionStatus": item.health,
                    },
                }
                for item in current_items
            ]

        cli = FakeCli(
            {
                ("backup", "protection", "resume"): resume,
                ("backup", "item", "list"): list_items,
            }
        )
        shares = [share("stopped")]
        plan = MODULE.build_plan(shares, [stopped], TARGET)
        MODULE.apply_plan(
            cli,
            shares,
            [stopped],
            [],
            plan,
            TARGET,
            wait_seconds=1,
            poll_seconds=1,
            max_actions=200,
            max_new_registrations=50,
        )
        resume_calls = [
            call
            for call in cli.calls
            if call[:3] == ("backup", "protection", "resume")
        ]
        self.assertEqual(len(resume_calls), 1)
        self.assertIn(stopped.item_name, resume_calls[0])

    def test_apply_times_out_if_item_never_appears(self):
        cli = FakeCli(
            {
                ("backup", "protection", "enable-for-azurefileshare"): {},
                ("backup", "item", "list"): [],
            }
        )
        shares = [share("new")]
        plan = MODULE.build_plan(shares, [], TARGET)
        with mock.patch.object(
            MODULE.time, "monotonic", side_effect=[0, 0.1, 0.2, 2]
        ), mock.patch.object(MODULE.time, "sleep", return_value=None):
            with self.assertRaises(MODULE.AzureCliError):
                MODULE.apply_plan(
                    cli,
                    shares,
                    [],
                    [],
                    plan,
                    TARGET,
                    wait_seconds=1,
                    poll_seconds=1,
                    max_actions=200,
                    max_new_registrations=50,
                )
        self.assertTrue(
            any(
                call[:3] == ("backup", "protection", "enable-for-azurefileshare")
                for call in cli.calls
            )
        )

    def test_action_cap_is_checked_before_writes(self):
        shares = [share("one"), share("two")]
        plan = MODULE.build_plan(shares, [], TARGET)
        cli = FakeCli({})
        with self.assertRaises(MODULE.AzureCliError):
            MODULE.apply_plan(
                cli,
                shares,
                [],
                [],
                plan,
                TARGET,
                wait_seconds=1,
                poll_seconds=1,
                max_actions=1,
                max_new_registrations=50,
            )
        self.assertEqual(cli.calls, [])

    def test_new_registration_cap_is_checked_before_writes(self):
        shares = [
            share(storage_account=f"stsource{index}") for index in range(51)
        ]
        plan = MODULE.build_plan(shares, [], TARGET)
        cli = FakeCli({})
        with self.assertRaisesRegex(
            MODULE.AzureCliError, "new storage-account registrations"
        ):
            MODULE.apply_plan(
                cli,
                shares,
                [],
                [],
                plan,
                TARGET,
                wait_seconds=1,
                poll_seconds=1,
                max_actions=200,
                max_new_registrations=50,
            )
        self.assertEqual(cli.calls, [])

    def test_vault_account_limit_is_checked_before_writes(self):
        containers = [
            MODULE.BackupContainer(
                subscription_id="sub",
                resource_group="rg-source",
                storage_account=f"stexisting{index}",
                vault_subscription_id="sub",
                vault_resource_group="rg-backup",
                vault_name="rsv-east",
                container_name=(
                    f"StorageContainer;Storage;rg-source;stexisting{index}"
                ),
                registration_status="Registered",
            )
            for index in range(200)
        ]
        shares = [share("new", storage_account="stnew")]
        plan = MODULE.build_plan(shares, [], TARGET, containers)
        cli = FakeCli({})
        with self.assertRaisesRegex(MODULE.AzureCliError, "200 registered"):
            MODULE.apply_plan(
                cli,
                shares,
                [],
                containers,
                plan,
                TARGET,
                wait_seconds=1,
                poll_seconds=1,
                max_actions=200,
                max_new_registrations=50,
            )
        self.assertEqual(cli.calls, [])

    def test_vault_protected_item_limit_is_checked_before_writes(self):
        items = [protected(f"existing-{index}") for index in range(2_000)]
        shares = [share("new")]
        plan = MODULE.build_plan(shares, items, TARGET)
        cli = FakeCli({})
        with self.assertRaisesRegex(MODULE.AzureCliError, "2,000 protected"):
            MODULE.apply_plan(
                cli,
                shares,
                items,
                [],
                plan,
                TARGET,
                wait_seconds=1,
                poll_seconds=1,
                max_actions=200,
                max_new_registrations=50,
            )
        self.assertEqual(cli.calls, [])

    def test_json_result_is_one_parseable_envelope(self):
        initial = MODULE.build_plan([share("new")], [], TARGET)
        final = MODULE.build_plan([share("new")], [protected("new")], TARGET)
        encoded = MODULE.json.dumps(
            MODULE.result_document("apply", TARGET, initial, final)
        )
        decoded = MODULE.json.loads(encoded)
        self.assertEqual(decoded["mode"], "apply")
        self.assertEqual(len(decoded["initial"]), 1)
        self.assertEqual(decoded["final"][0]["status"], "PROTECTED")


class TargetTests(unittest.TestCase):
    def test_target_rejects_vm_backup_policy(self):
        cli = FakeCli(
            {
                ("backup", "vault", "show"): {"location": "eastus2"},
                ("backup", "policy", "show"): {
                    "properties": {
                        "backupManagementType": "AzureIaasVM",
                        "workLoadType": "VM",
                    }
                },
            }
        )
        with self.assertRaises(MODULE.AzureCliError):
            MODULE.target_from_azure(
                cli, "sub", "rg-backup", "rsv", "DefaultPolicy"
            )

    def test_target_rejects_vault_without_parseable_resource_id(self):
        cli = FakeCli(
            {
                ("backup", "vault", "show"): {"location": "eastus2"},
                ("backup", "policy", "show"): {
                    "properties": {
                        "backupManagementType": "AzureStorage",
                        "workLoadType": "AzureFileShare",
                    }
                },
            }
        )
        with self.assertRaisesRegex(MODULE.AzureCliError, "no parseable resource ID"):
            MODULE.target_from_azure(cli, "sub", "rg-backup", "rsv", "afs")

    def test_target_rejects_cross_subscription_vault(self):
        cli = FakeCli(
            {
                ("backup", "vault", "show"): {
                    "id": (
                        "/subscriptions/other/resourceGroups/rg-backup/providers/"
                        "Microsoft.RecoveryServices/vaults/rsv"
                    ),
                    "location": "eastus2",
                },
                ("backup", "policy", "show"): {
                    "properties": {
                        "backupManagementType": "AzureStorage",
                        "workLoadType": "AzureFileShare",
                    }
                },
            }
        )
        with self.assertRaisesRegex(MODULE.AzureCliError, "outside the selected"):
            MODULE.target_from_azure(cli, "sub", "rg-backup", "rsv", "afs")


if __name__ == "__main__":
    unittest.main()

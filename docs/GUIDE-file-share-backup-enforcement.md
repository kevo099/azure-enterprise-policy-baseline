# Guide: ensuring Azure file shares are protected

Start with Microsoft-managed Azure Policy built-ins and Microsoft-published
reference templates. Add repository-owned controls only when their distinct
behavior is required:

1. Microsoft's existing-vault `DeployIfNotExists` policy automatically
   protects eligible shares after Azure Policy evaluates them.
2. Microsoft's audit policy reports classic SMB shares without a correlated
   Azure Backup protected-item record.
3. Microsoft Quickstarts provide first-party deployment examples for one
   explicitly named share; they are references, not all-share enforcement.
4. `scripts/ensure_file_share_backup.py` inventories and protects existing
   classic SMB shares immediately, without waiting for Policy propagation or a
   remediation task. It can be run on a schedule as an independent
   reconciliation path.
5. The repository's custom audit policy is an optional pinned fallback when
   Preview adoption is prohibited or an organization-owned initiative
   reference is required. Do not assign it alongside Microsoft's audit twin.

Use the [replication
runbook](RUNBOOK-replicate-azure-files-backup.md) for a linear, disposable
create → report-only → promote → recovery-point → restore → teardown canary.
This guide is the design and operations reference behind that procedure.

## Microsoft built-ins and templates — recommended first

Use **`[Preview]: Configure backup for Azure Files Shares without a given tag
to an existing recovery services vault in the same location`**:

```text
e159e079-0ddd-4905-97c9-79a8fca6d880
```

As of 2026-08-25, the definition is `2.0.0-preview`. It accepts an existing
Azure Files backup-policy resource ID, keeps vault configuration under your
control, and can register storage accounts when `registerStorageAccount=true`.
Assign it once for each subscription/region/vault combination. Use Microsoft's
built-in audit policy as the separate coverage signal. Substitute—not add—the
baseline's custom audit only when you need a pinned definition.

Microsoft publishes five Azure Files backup definitions, all currently
Preview:

| Order | Purpose | Definition ID | Version |
|---|---|---|---|
| 1 | Existing vault, exclude a tag — recommended enforcement | `e159e079-0ddd-4905-97c9-79a8fca6d880` | `2.0.0-preview` |
| 2 | Audit unprotected SMB shares — recommended reporting | `cfc5190a-3b19-4a23-b563-a4c719b666e4` | `1.0.0-preview` |
| 3 | Existing vault, include a tag | `d8659d5a-a3bd-444d-98ea-570bceb568cf` | `2.0.0-preview` |
| 4 | Create a vault, exclude a tag | `9e47cad9-bcdb-42dd-8c9b-a81e15ca2842` | `1.0.0-preview` |
| 5 | Create a vault, include a tag | `f32ca068-2ada-4705-b5b5-84ce89422846` | `1.0.0-preview` |

See Microsoft's [Backup Policy
catalog](https://learn.microsoft.com/en-us/azure/backup/policy-reference) and
[Azure Files Policy automation
guide](https://learn.microsoft.com/en-us/azure/backup/backup-azure-files-policy-automation)
for the service-owned definitions and assignment prerequisites.

Microsoft also publishes [daily](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share)
and [hourly](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share-hourly)
Quickstarts plus an [Azure Verified Recovery Services vault
module](https://github.com/Azure/terraform-azurerm-avm-res-recoveryservices-vault).
These templates protect explicitly declared shares. Use them as first-party
deployment references, not as proof that every share is continuously covered.

The existing-vault v2 definitions support a Vault-Standard policy in another
subscription when all documented cross-subscription prerequisites are met.
They do not support management-group assignments. The command-line
reconciler in this repository intentionally supports one source/vault
subscription per run; use Policy or a purpose-built multi-subscription
orchestrator for central-vault designs.

The other three remediation built-ins are valid for tag-inclusion and
application-owned-vault patterns, but the "new vault" variants hard-code a
daily 08:00 UTC schedule, five days of snapshot retention, 30 days of vault
retention, and `publicNetworkAccess: Enabled`. That is usually too opinionated
for an enterprise baseline.

## Repository custom alternatives and complements

Use these after reviewing the Microsoft options above:

| Repository artifact | Role | When to use it |
|---|---|---|
| `scripts/ensure_file_share_backup.py` | Immediate and scheduled reconciliation | Backstop Policy latency and independently detect or repair coverage gaps |
| `audit-file-share-backup-protection` | Custom audit-policy fallback | Only when Preview adoption is prohibited or a pinned organization-owned definition is required; do not assign it with Microsoft's audit twin |
| `examples/azure-files-backup-policy.json` | Starter Azure Backup schedule and retention input | Bootstrap a reviewed backup policy; this is not an Azure governance policy |

## Prerequisites

- Azure CLI authenticated to the intended tenant/subscription and Python
  3.10 or newer for the reconciliation script (live-tested with Azure CLI
  2.89.0).
- A Recovery Services vault in the same Azure region as the storage accounts.
- An Azure Files policy in that vault. Prefer Vault-Standard when its support
  requirements fit the workload; snapshot-only protection remains in the
  source storage account.
- `Microsoft.Storage`, `Microsoft.RecoveryServices`, and
  `Microsoft.PolicyInsights` registered.
- For the reconciliation script, the caller needs Reader (or equivalent
  inventory permissions) across the selected subscription because it checks
  every Recovery Services vault, Storage Account Contributor on the source
  scope, and Backup Contributor on the **target vault resource group**.
- For `DeployIfNotExists`, grant the assignment identity Storage Account
  Contributor at the source assignment scope and Backup Contributor at the
  **target vault resource group**. The built-in creates a nested deployment in
  that resource group, so vault-only scope is too narrow. CLI/SDK assignment
  does **not** create these role assignments automatically; the deployer also
  needs `Microsoft.Authorization/policyAssignments/write` at the source scope
  (normally Resource Policy Contributor or Owner) and
  `Microsoft.Authorization/roleAssignments/write` at both role scopes (Owner,
  User Access Administrator, or RBAC Administrator at the relevant scopes).
- Cross-subscription vaulted backup also requires the documented provider,
  policy, identity, and networking prerequisites in both subscriptions.

The repository includes a simple daily 02:00 UTC/30-day **snapshot-tier**
starter policy. Recovery points created from this JSON remain in the source
storage account; it is not Vault-Standard and is not an offsite copy. Review
its schedule, retention, resilience, and cost before using it:

```bash
az backup policy create \
  --resource-group <backup-rg> \
  --vault-name <recovery-services-vault> \
  --name afs-daily \
  --backup-management-type AzureStorage \
  --workload-type AzureFileShare \
  --policy examples/azure-files-backup-policy.json
```

## Immediate audit and reconciliation

The script is dry-run by default and uses ARM inventory, not storage keys:

```bash
python3 scripts/ensure_file_share_backup.py \
  --source-resource-group <workload-rg> \
  --source-location eastus2 \
  --vault-resource-group <backup-rg> \
  --vault-name <recovery-services-vault> \
  --policy-name <azure-files-policy>
```

An exit status of `2` means the run found work or a coverage blocker. Review
the table, then apply the same plan:

```bash
python3 scripts/ensure_file_share_backup.py \
  --source-resource-group <workload-rg> \
  --source-location eastus2 \
  --vault-resource-group <backup-rg> \
  --vault-name <recovery-services-vault> \
  --policy-name <azure-files-policy> \
  --apply
```

`--apply` enables unprotected shares, resumes stopped items already associated
with the target vault, and waits up to 30 minutes for a healthy configuration
record. `INITIAL_RECOVERY_PENDING` means Azure accepted the configuration but
the first recovery has not completed; it is not the same as `PROTECTED` and is
not proof of a recovery point. Each Azure CLI call also has a five-minute
timeout by default.

Before any write, the script fails closed on another-vault account
registration, a soft-deleted item, an unhealthy/unknown item, a region
mismatch, or a target container that is not ready. Account-level container
inventory merges active containers, soft-deleted containers in active vaults,
and whole deleted-vault tombstones in each source region. It catches
registration conflicts even when that account has no active protected-share
record. Healthy protection in any vault/policy satisfies the coverage
invariant; the selected target policy is used only for uncovered shares.

Apply operations are separate Azure API calls, not a transaction. A service
failure can leave a partial but rerunnable wave. Safety caps default to 200
share actions and 50 new account registrations; lower them with
`--max-actions` and `--max-new-registrations` for smaller waves. These are
per-run caps, not quota reservations: the script cannot see Policy, manual, or
earlier-run consumption of the service's daily limits. Budget known same-day
activity and use smaller waves accordingly.

Omit `--source-resource-group` to inventory the entire current subscription.
In a multi-region subscription, run separate `--source-location` jobs against
the corresponding regional vaults. `--subscription`, `--json`,
`--wait-seconds`, `--poll-seconds`, and `--command-timeout-seconds` support
unattended execution. One `--subscription` selects both source inventory and
target vault; the script does not support or claim cross-subscription
reconciliation.

Do not use this script against a source account whose intended vault is in a
different subscription. Its inventory is deliberately same-subscription and
cannot safely prove foreign-vault registration. Use the v2 built-in or a
purpose-built multi-subscription orchestrator instead.

Dry-run exit `2` means work, a blocker, or an unsupported NFS exception was
found. Apply exit `0` means all eligible classic SMB shares are protected or
have healthy configuration with initial recovery pending. Apply exit `3`
means eligible SMB reconciliation completed but NFS shares remain unsupported.

## Continuous enforcement with Azure Policy

The built-in remains Preview. Start at a disposable resource-group scope and
stage DINE in `DoNotEnforce`; do not create remediation yet. `DoNotEnforce`
still evaluates compliance but does not run the effect. A manually started
remediation can run even in this mode, which is why remediation belongs only
after explicit promotion. The excerpts assume a dedicated Bash shell with
strict failure handling; use the replication runbook for the full machine
gates and restartable state file.

```bash
set -euo pipefail
SUB=$(az account show --query id -o tsv)
SCOPE="/subscriptions/$SUB/resourceGroups/<canary-rg>"
POLICY_ID="/providers/Microsoft.Authorization/policyDefinitions/e159e079-0ddd-4905-97c9-79a8fca6d880"
BACKUP_POLICY_ID="/subscriptions/$SUB/resourceGroups/<backup-rg>/providers/Microsoft.RecoveryServices/vaults/<vault>/backupPolicies/<azure-files-policy>"
VAULT_RG_SCOPE="/subscriptions/$SUB/resourceGroups/<backup-rg>"

jq --arg location eastus2 --arg policyId "$BACKUP_POLICY_ID" \
  '.vaultLocation.value = $location | .backupPolicyId.value = $policyId' \
  examples/azure-files-backup-assignment.example.json \
  > azure-files-backup-assignment.json

ASSIGNMENT_ID=$(az policy assignment create \
  --name azure-files-backup-eus2 \
  --display-name "Protect Azure Files in East US 2" \
  --scope "$SCOPE" \
  --policy "$POLICY_ID" \
  --definition-version '2.0.*' \
  --location eastus2 \
  --mi-system-assigned \
  --enforcement-mode DoNotEnforce \
  --params @azure-files-backup-assignment.json \
  --query id -o tsv)

PRINCIPAL_ID=$(az policy assignment show \
  --name azure-files-backup-eus2 \
  --scope "$SCOPE" \
  --query identity.principalId -o tsv)
```

Trigger a scan and require at least one canary state record. Empty output is
**inconclusive**, never proof of compliance:

```bash
az policy state trigger-scan --resource-group <canary-rg>
az policy state list \
  --resource-group <canary-rg> \
  --policy-assignment azure-files-backup-eus2 \
  --query '[].{resource:resourceId,state:complianceState}' -o table
```

Grant and verify **Storage Account Contributor** at the source scope and
**Backup Contributor** at the target vault's **resource group**. CLI/SDK does
not create these grants automatically:

```bash
STORAGE_ROLE_ASSIGNMENT_ID=$(az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role 17d1049b-9a84-46fb-8f53-869881c3d3ab \
  --scope "$SCOPE" \
  --query id -o tsv)

BACKUP_ROLE_ASSIGNMENT_ID=$(az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role 5e467623-bb1f-42f4-a55d-6e525e11384b \
  --scope "$VAULT_RG_SCOPE" \
  --query id -o tsv)

test -n "$STORAGE_ROLE_ASSIGNMENT_ID"
test -n "$BACKUP_ROLE_ASSIGNMENT_ID"

az role assignment list --assignee-object-id "$PRINCIPAL_ID" --all \
  --query '[].{role:roleDefinitionName,scope:scope,id:id}' -o table
```

For a cross-subscription Vault-Standard design, make that second assignment in
the vault subscription and complete Microsoft's additional prerequisites.
The two GUIDs in the example are those exact roles; retry only the role-create
commands if new-principal propagation is still in progress. The
cross-subscription topology is Preview and was not live-tested by this
project.

After review, explicitly promote and use fresh resource discovery for shares
that existed before the assignment:

```bash
az policy assignment update \
  --name azure-files-backup-eus2 \
  --scope "$SCOPE" \
  --enforcement-mode Default

az policy remediation create \
  --name azure-files-backup-eus2-initial \
  --policy-assignment "$ASSIGNMENT_ID" \
  --resource-group <canary-rg> \
  --resource-discovery-mode ReEvaluateCompliance

az policy remediation show \
  --name azure-files-backup-eus2-initial \
  --resource-group <canary-rg> \
  --query '{state:provisioningState,deployments:deploymentStatus}' -o jsonc

az policy remediation deployment list \
  --name azure-files-backup-eus2-initial \
  --resource-group <canary-rg> -o table
```

New ARM-created shares are evaluated asynchronously after creation. This is
not synchronous request-time enforcement and does not prove that a scheduled
backup succeeded. Verify a completed `ConfigureBackup` job, healthy protected
item, completed on-demand backup, listed recovery point, and alternate restore.
The [replication runbook](RUNBOOK-replicate-azure-files-backup.md) contains
the exact proof and teardown commands.

The existing-vault built-ins support subscription and resource-group scope,
not management-group assignment. Use one assignment per source
subscription/location/target-policy combination. The sample intentionally
keeps the source, vault, and policy in the same subscription.

## Important boundaries

- Azure Backup does not support NFS Azure file shares. Microsoft's audit
  built-in filters to SMB, but its current four DINE definitions do not carry
  that protocol predicate. Do not broadly remediate a scope containing NFS
  shares; use canary assignments, exclusions/exemptions, and the reconciliation
  report.
- Do not conflate release states: Azure Files Vault-Standard is generally
  available, while the five Azure Files Policy definitions and
  cross-subscription backup/restore remain Preview as of this review.
- Snapshot and Vault-Standard are different protection tiers. Snapshot
  supports a broader account/restore matrix but keeps recovery points in the
  source account. Vault-Standard transfers data to vault storage and adds
  cost, regional/account/scale constraints, and different restore behavior.
  The checked-in starter JSON is Snapshot.
- A storage account can be registered to only one Recovery Services vault.
- Vault and storage account must share a region. Microsoft advises no more
  than 200 shares per remediation wave. The service also limits a vault to 50
  new storage-account registrations and 200 newly protected shares per day,
  200 registered storage accounts total, and 2,000 protected shares total.
- Vaulted protection requires storage-account key access and the documented
  network prerequisites. Validate restricted accounts against the Azure Files
  [backup support
  matrix](https://learn.microsoft.com/en-us/azure/backup/azure-file-share-support-matrix)
  before rollout. Storage-account private endpoints are supported under the
  documented conditions, but a Recovery Services vault private endpoint does
  not apply to Azure Files like it does to other workloads.
- FileREST-created shares do not generate the same ARM create event. Keep a
  scheduled reconciliation/Policy scan even after DINE is assigned.
- Policy compliance is an existence check. It is not proof of backup health,
  recovery-point freshness, retention compliance, or restoreability.
- Removing Policy, remediation, or its RBAC grants does not stop existing
  protection. Retained recovery points can continue to incur cost. Stopping
  protection with backup-data deletion is a separate destructive operation;
  share soft delete and vault/item soft delete are separate layers, and
  service-retained tombstones can remain after active resources are removed.
- Choose vault redundancy, cross-region restore, immutability, and multi-user
  authorization deliberately before production protection. Locked
  immutability is irreversible and can prevent deletion or retention
  reduction. Those vault controls protect Vault-Standard data, not operational
  Snapshot-tier recovery points that remain in the source storage account.
- The reconciler inventories classic
  `Microsoft.Storage/storageAccounts/fileServices/shares` resources. New
  top-level `Microsoft.FileShares/fileShares` resources are currently NFS-only,
  unsupported by Azure Backup, and outside this control's success claim.
- The reconciler is same-subscription only and cannot discover a source
  account registered to a vault in another subscription. Do not run it against
  a central-vault topology; use the v2 built-in or multi-subscription
  orchestration instead.
- Copying an Azure Files **backup policy resource** into many vaults is a
  separate IaC/custom-DINE pattern. First replicate and verify the regional
  `Microsoft.RecoveryServices/vaults/backupPolicies` resources, then target
  each real resource ID from the share-enforcement assignment. The five
  Microsoft share built-ins do not generically clone an arbitrary policy.

## Repository and live-test evidence

The custom audit definition was tested side-by-side with Microsoft's audit
built-in on 2026-07-28; both shares transitioned from `NonCompliant` to
`Compliant` after protection. See
`docs/LIVE-TEST-2026-07-28-file-share-backup.md`.

A fresh 2026-08-25 test exercised the reconciliation path, completed an
on-demand backup for all three fixtures, and restored one file byte-for-byte.
It also verified zero active test delta after cleanup. Azure Policy returned no
state records during that window, so Policy evaluation was inconclusive, and
the test identity could not validate DINE managed-identity role grants. See
`docs/LIVE-TEST-2026-08-25-file-share-backup-enforcement.md`.

## GitHub implementation audit (2026-08-25)

- [`Azure/azure-policy`](https://github.com/Azure/azure-policy/tree/master/built-in-policies/policyDefinitions/Backup)
  is the authoritative source for the five built-ins. All five are Preview.
  Its public Jest workflow validates `samples/`, not `built-in-policies/`, so a
  green repository check is not policy-specific live-test evidence.
- Microsoft's
  [daily](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share)
  and
  [hourly](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share-hourly)
  Quickstarts protect one explicitly named share; they do not discover all
  shares. Their public deployment badges reported `fail` on their latest runs
  inspected on 2026-08-25.
- [`Azure/azure-powershell`](https://github.com/Azure/azure-powershell/tree/main/src/RecoveryServices/RecoveryServices.Backup.Test)
  contains Recovery Services backup scenarios. The 2026-08-25 inspection
  found Azure Files enable/backup primitives but not an all-share control-loop
  test; treat that as dated upstream evidence, not this project's live proof.
- The Microsoft Community Policy repository contained no separate Azure Files
  backup alternative. Azure Verified Terraform supports explicit protected-
  share maps, not automatic discovery of every share.

That evidence is why this project recommends Microsoft's service-owned
built-ins first, retains the live-tested custom audit only as an optional
fallback, and adds an independently tested inventory/reconciliation backstop
rather than claiming that repository presence alone proves end-to-end
enforcement.

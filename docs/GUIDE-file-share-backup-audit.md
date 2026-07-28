# Guide: auditing Azure file share backup with `audit-file-share-backup-protection`

Step-by-step instructions for deploying, assigning, and reading results from
the baseline's file-share backup audit policy — standalone or as part of the
`enterprise-baseline` initiative.

## What this policy does

`policies/operations/audit-file-share-backup-protection.json` flags every
**SMB Azure file share** that has no Azure Backup protection. A share is
compliant when a backup-protected item (`AzureFileShareProtectedItem`) exists
for it in a **Recovery Services vault**. The policy:

- targets `Microsoft.Storage/storageAccounts/fileServices/shares` (the share
  child resource, so each share gets its own compliance record — a storage
  account with three shares can be two-thirds compliant);
- uses `mode: All`, because file shares are non-indexed child resources
  without tags or location — an `Indexed` policy would never evaluate them;
- only evaluates shares with `enabledProtocols == SMB`. NFS shares are
  excluded on purpose: Azure Backup does not support NFS Azure file shares,
  so including them would produce permanent, unfixable non-compliance noise;
- uses `AuditIfNotExists` with `details.type:
  Microsoft.RecoveryServices/backupprotecteditems` and **no
  existenceCondition** — see "How the existence check actually works" below.

## Relationship to Microsoft's built-in policies

Microsoft ships a built-in audit policy with the same rule:
**`[Preview]: Azure Backup should be enabled on Azure file shares`**
(`cfc5190a-3b19-4a23-b563-a4c719b666e4`, `1.0.0-preview`, added Feb 2025).
This custom policy is its non-preview twin, kept in the baseline so that:

- the baseline does not depend on a *preview* definition Microsoft may change
  or withdraw (the built-in is in no built-in initiative and has no
  regulatory-compliance mapping);
- the effect is wired into the initiative's parameter surface like every
  other baseline policy (`effectFileShareBackup`);
- the definition is versioned and testable in this repo.

If you prefer the built-in, assign `cfc5190a-3b19-4a23-b563-a4c719b666e4`
instead — the compliance results are identical.

To **remediate** (not just audit), pair this policy with Microsoft's built-in
DeployIfNotExists policies, which enable backup automatically:

| Built-in GUID | What it does |
|---|---|
| `e159e079-0ddd-4905-97c9-79a8fca6d880` | Configure backup for shares **without** a given tag → **existing** vault, same location |
| `d8659d5a-a3bd-444d-98ea-570bceb568cf` | Configure backup for shares **with** a given tag → **existing** vault, same location |
| `9e47cad9-bcdb-42dd-8c9b-a81e15ca2842` | Configure backup for shares **without** a given tag → **new** vault + new policy |
| `f32ca068-2ada-4705-b5b5-84ce89422846` | Configure backup for shares **with** a given tag → **new** vault + new policy |

(All four are also Preview; each accepts one location per assignment and is
not supported at management-group scope.)

## Prerequisites

- Azure CLI logged in with rights to create policy definitions and
  assignments at your target scope (Owner, or Contributor +
  Resource Policy Contributor).
- `Microsoft.Storage`, `Microsoft.RecoveryServices`, and
  `Microsoft.PolicyInsights` resource providers registered.
- `jq` if you use the repo's `scripts/deploy.sh`.

## Option A — deploy the whole baseline

The policy ships as #13 in the initiative, so the standard repo flow picks it
up automatically:

```bash
# 1. Publish all 16 definitions + the initiative at subscription scope
./scripts/deploy.sh

# 2. Assign the initiative (see examples/assignment-params.example.json)
./scripts/assign.sh <scope> my-params.json
```

`effectFileShareBackup` defaults to `AuditIfNotExists`; set it to `Disabled`
in your params file to switch the policy off without touching the initiative.

## Option B — deploy just this policy standalone

```bash
SUB=$(az account show --query id -o tsv)

# 1. Create the definition at subscription scope
az policy definition create \
  --name audit-file-share-backup-protection \
  --display-name "[Baseline] Azure file shares should be protected by Azure Backup" \
  --description "Audits SMB Azure file shares that are not protected by Azure Backup." \
  --mode All \
  --rules "$(jq -c .properties.policyRule policies/operations/audit-file-share-backup-protection.json)" \
  --params "$(jq -c .properties.parameters policies/operations/audit-file-share-backup-protection.json)"

# 2. Assign it (example: one resource group)
az policy assignment create \
  --name fileshare-backup-audit \
  --policy audit-file-share-backup-protection \
  --scope "/subscriptions/$SUB/resourceGroups/<your-rg>"
```

No managed identity or role grants are needed — `AuditIfNotExists` only
reads.

## Step-by-step: verify it works

1. **Create a storage account and an SMB share** (or use an existing one):

   ```bash
   az storage account create -n <account> -g <your-rg> -l <region> --sku Standard_LRS
   az storage share-rm create --storage-account <account> -n data01
   ```

2. **Trigger an on-demand compliance scan** (otherwise you wait for the
   ~24-hour cycle):

   ```bash
   az policy state trigger-scan -g <your-rg>   # takes several minutes
   ```

3. **Read the verdict:**

   ```bash
   az policy state list -g <your-rg> \
     --filter "policyDefinitionName eq 'audit-file-share-backup-protection'" \
     --query "[].{resource:resourceId, state:complianceState}" -o table
   ```

   The unprotected share shows `NonCompliant`. It also appears in
   Portal → Policy → Compliance, and in Azure Business Continuity Center.

4. **Protect the share** (manually here; use the DINE built-ins at scale):

   ```bash
   az backup vault create -n <vault> -g <your-rg> -l <region>
   az backup protection enable-for-azurefileshare \
     --vault-name <vault> -g <your-rg> \
     --storage-account <account> --azure-file-share data01 --policy-name DefaultPolicy
   ```

5. **Re-scan and confirm `Compliant`** — repeat steps 2–3. Enabling backup is
   a Backup-RP operation, not a write to the share, so the flip only shows up
   after a scan (or the daily cycle).

## How the existence check actually works

`Microsoft.RecoveryServices/backupprotecteditems` is not a real ARM type —
the real protected-item type is
`Microsoft.RecoveryServices/vaults/backupFabrics/protectionContainers/protectedItems`.
It is a pseudo-type the Policy engine special-cases: it correlates the
evaluated resource (share or VM) to its protected item across resource
groups, something a normal `AuditIfNotExists` lookup cannot do. Three rules
follow:

1. **Don't add an `existenceCondition`.** The pseudo-type has no aliases;
   Microsoft's own built-ins ship without one. Any correlated protected item
   satisfies the check.
2. **Don't re-target it at the storage account.** Only the pairings Microsoft
   ships are supported (VM → backupprotecteditems, share →
   backupprotecteditems; blob protection uses a different pseudo-type,
   `Microsoft.DataProtection/backupInstances`, at account level).
3. **Vaulted vs snapshot-only protection is not distinguishable.** A share
   with snapshot-tier-only protection counts as protected.

## Caveats

- **Evaluation latency.** ARM-created/updated shares evaluate roughly 15
  minutes after the write (`AuditIfNotExists` has a default evaluation delay
  of `PT10M`). Shares created purely through the data plane (SMB/FileREST)
  produce no ARM write event and are only picked up by the daily compliance
  cycle or an on-demand `az policy state trigger-scan`.
- **After enabling backup**, the share stays `NonCompliant` until the next
  scan — protection is not a write to the share.
- **NFS shares are invisible** to this policy by design (see above).
- **Snapshot-only protection counts as compliant** — you cannot audit
  "vaulted backup specifically" with this mechanism.
- **The built-in twin is Preview** (`1.0.0-preview` since Feb 2025). This
  custom definition freezes the same rule at `1.0.0` under your control.
- **Recovery Services vault, not Backup vault.** Azure Files backup
  (including vaulted backup, GA March 2025) lives in Recovery Services
  vaults. The newer `Microsoft.DataProtection` Backup vaults are a different
  product surface and never satisfy this check.
- **Scale note for the DINE remediation built-ins:** one location per
  assignment, no management-group scope, and Microsoft advises staying under
  ~200 shares per assignment.

## Live validation

See `docs/LIVE-TEST-2026-07-28-file-share-backup.md` for the dated record of
this policy's end-to-end test (deploy → NonCompliant → enable backup →
Compliant → teardown) on a disposable subscription. The 2026-07-16 fifteen-
policy live test (`docs/LIVE-TEST-2026-07-16.md`) predates this policy.

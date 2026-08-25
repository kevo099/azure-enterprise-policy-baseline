# Guide: auditing Azure file share backup with `audit-file-share-backup-protection`

This is the custom-fallback guide. Start with Microsoft-managed built-in
policies and Microsoft-published templates; use the repository definition only
when you deliberately need a pinned, organization-owned rule.

## Recommended starting point

For report-only coverage, prefer Microsoft's Preview audit definition:

```text
cfc5190a-3b19-4a23-b563-a4c719b666e4
```

Assign the built-in directly; it needs no managed identity or role grants:

```bash
SUB=$(az account show --query id -o tsv)
az policy assignment create \
  --name azure-files-backup-audit \
  --display-name "Audit Azure Files backup coverage" \
  --policy "/providers/Microsoft.Authorization/policyDefinitions/cfc5190a-3b19-4a23-b563-a4c719b666e4" \
  --scope "/subscriptions/$SUB/resourceGroups/<canary-rg>"
```

For automatic protection, prefer the existing-vault, tag-exclusion
`DeployIfNotExists` definition:

```text
e159e079-0ddd-4905-97c9-79a8fca6d880
```

Microsoft's audit definition has the same rule as this custom policy. Do not
assign both: that creates duplicate compliance records without increasing
coverage. Use the custom instructions below only when Preview adoption is
prohibited, a stable organization-owned definition ID is required, or the
rule must remain inside the repository's custom initiative.

Microsoft's [daily](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share)
and [hourly](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share-hourly)
Quickstarts are first-party reference templates for one explicitly named
share. They are not subscription-wide audit or enforcement controls.

## What this policy does

`policies/operations/audit-file-share-backup-protection.json` flags every
**SMB Azure file share** that has no centrally managed Azure Backup
protected-item record. A share is compliant when a backup-protected item
(`AzureFileShareProtectedItem`) exists for it in a **Recovery Services
vault**. The policy:

- targets `Microsoft.Storage/storageAccounts/fileServices/shares` (the share
  child resource, so each share gets its own compliance record — a storage
  account with three shares can be two-thirds compliant);
- uses `mode: All`, because file shares are non-indexed child resources
  without tags or location — an `Indexed` policy would never evaluate them;
- only evaluates shares with `enabledProtocols == SMB`. NFS shares are
  excluded on purpose: Azure Backup does not support NFS Azure file shares,
  so including them would produce permanent, unfixable non-compliance noise.
  Premium (SSD/FileStorage) SMB shares are deliberately **included** — they
  are supported by Azure Backup;
- uses `AuditIfNotExists` with `details.type:
  Microsoft.RecoveryServices/backupprotecteditems` and **no
  existenceCondition** — see "How the existence check actually works" below.

**This is an existence-only control.** Compliant means Azure Policy correlated
the share with an Azure Backup protected-item record — nothing more. It does
not assess backup health, freshness, retention, recovery-point age, vault
redundancy, or restore success (protected items in `stopped`, `paused`, or
`error` states still count). Manual share snapshots and third-party backup
products do **not** satisfy it; snapshot-tier-only Azure Backup **does**.

## Relationship to Microsoft's built-in policies

Microsoft ships a built-in audit policy with the same rule:
**`[Preview]: Azure Backup should be enabled on Azure file shares`**
(`cfc5190a-3b19-4a23-b563-a4c719b666e4`, `1.0.0-preview`, added Feb 2025).
The Microsoft built-in is the default recommendation. This custom policy is a
fallback clone pinned to the built-in's `1.0.0-preview` rule JSON, retained so
that organizations can choose to:

- avoid depending on a *preview* definition Microsoft may change or withdraw
  (the built-in is in no built-in initiative and has no regulatory-compliance
  mapping);
- wire the effect into the initiative's parameter surface like every other
  baseline policy (`effectFileShareBackup`); or
- version and test the definition in this repository.

Pinning the JSON pins only the rule text — the Policy engine's pseudo-type
correlation behavior stays platform-side either way. Unless one of the custom
requirements above applies, assign
`cfc5190a-3b19-4a23-b563-a4c719b666e4`; the compliance results are identical.

To **remediate** rather than only audit, use Microsoft's built-in
DeployIfNotExists policies. The existing-vault, tag-exclusion option is the
recommended default; the remaining definitions support opt-in and
application-owned-vault designs:

| Built-in GUID | What it does |
|---|---|
| `e159e079-0ddd-4905-97c9-79a8fca6d880` | Configure backup for shares **without** a given tag → **existing** vault, same location |
| `d8659d5a-a3bd-444d-98ea-570bceb568cf` | Configure backup for shares **with** a given tag → **existing** vault, same location |
| `9e47cad9-bcdb-42dd-8c9b-a81e15ca2842` | Configure backup for shares **without** a given tag → **new** vault + new policy |
| `f32ca068-2ada-4705-b5b5-84ce89422846` | Configure backup for shares **with** a given tag → **new** vault + new policy |

(All four are also Preview; each accepts one location per assignment and is
not supported at management-group scope.)

## Prerequisites

- Azure CLI and `jq` (both deployment options below use them).
- `Microsoft.Storage`, `Microsoft.RecoveryServices`, and
  `Microsoft.PolicyInsights` resource providers registered.
- Permissions:
  - **Option B (standalone policy):** rights to create policy definitions and
    assignments at the target scope — Owner, or Contributor + Resource
    Policy Contributor. No managed identity or role grants are involved;
    `AuditIfNotExists` only reads.
  - **Option A (full baseline):** additionally requires
    `Microsoft.Authorization/roleAssignments/write` (e.g. Owner, or User
    Access Administrator / Role Based Access Control Administrator) at the
    assignment scope **and** at the Log Analytics workspace scope, because
    `scripts/assign.sh` grants the initiative's managed identity three roles
    (the diagnostics DINE policy needs them; this audit policy does not).

## Option A — deploy the whole baseline

The policy ships as #13 in the initiative, so the standard repo flow picks it
up automatically:

```bash
# 1. Publish all 16 definitions + the initiative at subscription scope
./scripts/deploy.sh

# 2. Assign the initiative (start from examples/assignment-params.example.json)
./scripts/assign.sh --params my-params.json \
  --scope "/subscriptions/<sub-id>"          # omit --scope for current sub
```

`effectFileShareBackup` defaults to `AuditIfNotExists`; set it to `Disabled`
in your params file to switch the policy off without touching the initiative.

## Option B — deploy just this policy standalone

```bash
SUB=$(az account show --query id -o tsv)
POLICY=policies/operations/audit-file-share-backup-protection.json

# 1. Create the definition at subscription scope (fields read from the JSON
#    so this recipe cannot drift from the definition)
az policy definition create \
  --name "$(jq -r .name $POLICY)" \
  --display-name "$(jq -r .properties.displayName $POLICY)" \
  --description "$(jq -r .properties.description $POLICY)" \
  --mode "$(jq -r .properties.mode $POLICY)" \
  --metadata "$(jq -c .properties.metadata "$POLICY")" \
  --rules "$(jq -c .properties.policyRule $POLICY)" \
  --params "$(jq -c .properties.parameters $POLICY)"

# 2. Assign it (example: one resource group)
az policy assignment create \
  --name fileshare-backup-audit \
  --policy audit-file-share-backup-protection \
  --scope "/subscriptions/$SUB/resourceGroups/<your-rg>"
```

## Step-by-step: verify it works

1. **Create a storage account and an SMB share** (or use an existing one):

   ```bash
   az storage account create -n <account> -g <your-rg> -l <region> --sku Standard_LRS
   az storage share-rm create --storage-account <account> -g <your-rg> -n data01
   ```

2. **Wait for the new assignment to propagate** (a fresh policy assignment
   can take several minutes to become visible to scans), then **trigger an
   on-demand compliance scan** — otherwise you wait for the ~24-hour cycle:

   ```bash
   az policy state trigger-scan -g <your-rg>   # blocks; takes several minutes
   ```

3. **Read the verdict:**

   ```bash
   az policy state list -g <your-rg> \
     --filter "policyDefinitionName eq 'audit-file-share-backup-protection'" \
     --query "[].{resource:resourceId, state:complianceState}" -o table
   ```

   The unprotected share shows `NonCompliant`. The same result appears in
   Portal → Policy → Compliance. (The share may also show up as a
   protectable resource in the protection-inventory views under **Resiliency
   in Azure**, the successor to Azure Business Continuity Center — that is a
   separate observation, not this policy's compliance result.)

4. **Protect the share.** A fresh Recovery Services vault has no Azure
   Files (AzureStorage-workload) backup policy, so create one first —
   `DefaultPolicy` in a new vault is the *VM* policy and will not work here:

   ```bash
   az backup vault create -n <vault> -g <your-rg> -l <region>

   # Create an AzureStorage-workload backup policy (daily, 30-day retention)
   cat > afs-policy.json <<'EOF'
   {
     "properties": {
       "backupManagementType": "AzureStorage",
       "workloadType": "AzureFileShare",
       "schedulePolicy": {
         "schedulePolicyType": "SimpleSchedulePolicy",
         "scheduleRunFrequency": "Daily",
         "scheduleRunTimes": ["2026-01-01T02:00:00Z"]
       },
       "retentionPolicy": {
         "retentionPolicyType": "LongTermRetentionPolicy",
         "dailySchedule": {
           "retentionTimes": ["2026-01-01T02:00:00Z"],
           "retentionDuration": { "count": 30, "durationType": "Days" }
         }
       },
       "timeZone": "UTC"
     }
   }
   EOF
   az backup policy create --vault-name <vault> -g <your-rg> \
     --name afs-daily --backup-management-type AzureStorage --policy afs-policy.json

   az backup protection enable-for-azurefileshare \
     --vault-name <vault> -g <your-rg> \
     --storage-account <account> --azure-file-share data01 --policy-name afs-daily
   ```

   Configure-backup is **asynchronous** — confirm the job finished before
   rescanning:

   ```bash
   az backup job list --vault-name <vault> -g <your-rg> \
     --query "[].{op:properties.operation, status:properties.status}" -o table
   # wait until ConfigureBackup shows Completed
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
3. **Don't restrict the vault location in the rule.** Cross-resource-group
   vaults are handled by the pseudo-type correlation; Azure Backup's own
   region/subscription constraints already govern where protection can live.

## Caveats

- **Existence-only.** See "What this policy does" — this is not a
  backup-health, freshness, retention, or restore-test control, and
  snapshot-only Azure Backup protection counts as compliant.
- **Evaluation latency.** New policy assignments take several minutes to
  propagate before scans see them. ARM-created/updated shares evaluate
  roughly 15 minutes after the write (`AuditIfNotExists` has a default
  evaluation delay of `PT10M`). Shares created without an ARM write — e.g.
  via the FileREST Create Share API with a storage key/SAS — are only picked
  up by the daily compliance cycle or an on-demand
  `az policy state trigger-scan`.
- **After enabling backup**, the share stays `NonCompliant` until the next
  scan — protection is not a write to the share. Wait for the asynchronous
  ConfigureBackup job to complete before rescanning.
- **NFS shares are invisible** to this policy by design (see above).
- **Backup eligibility is not evaluated.** The policy audits every SMB share
  even when a given Azure Backup tier cannot currently protect it — e.g.
  vault-standard (vaulted) backup has size/object limits (currently 10 TiB
  and 10 million files/folders per share), same-region vault requirements,
  and storage-account networking/key-access requirements. See the
  [Azure Files backup support matrix](https://learn.microsoft.com/en-us/azure/backup/azure-file-share-support-matrix).
  Very large shares may only be protectable at snapshot tier.
- **Vault throughput limits** (often misquoted as policy limits): up to 200
  file shares can be *configured* for protection per vault per day, and up
  to 2,000 protected shares can be associated with one vault — per the
  support matrix above. The DINE remediation built-ins additionally accept
  one location per assignment and do not support management-group scope.
- **The built-in twin is Preview** (`1.0.0-preview` since Feb 2025). This
  custom definition freezes the same rule text at `1.0.0` under your
  control; the engine-side correlation behavior remains Microsoft's.
- **Recovery Services vault, not Backup vault.** Azure Files backup
  (including vaulted backup, GA March 2025) lives in Recovery Services
  vaults. The newer `Microsoft.DataProtection` Backup vaults are a different
  product surface and never satisfy this check.

## Live validation

See
[docs/LIVE-TEST-2026-07-28-file-share-backup.md](LIVE-TEST-2026-07-28-file-share-backup.md)
for the dated record of this policy's end-to-end test (deploy →
NonCompliant → enable backup → Compliant → teardown) on a disposable
subscription. The
[2026-07-16 15-policy live test](LIVE-TEST-2026-07-16.md) predates this
policy.

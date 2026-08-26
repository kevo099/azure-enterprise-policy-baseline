# Runbook: replicate and prove Azure Files backup enforcement

This runbook builds a disposable, same-subscription canary and proves the
whole chain:

1. an eligible SMB share exists;
2. Microsoft's existing-vault Preview policy evaluates it without changing it;
3. the assignment identity has the two required role grants;
4. promotion and remediation configure Azure Backup;
5. an on-demand backup creates a recovery point; and
6. an alternate-share restore reproduces a file byte-for-byte.

Here, **replicate** means reproduce and validate the deployment procedure. It
does not mean geo-replicate backup data or propagate one backup-policy resource
to many vaults; both are separate architecture decisions.

The canary uses the repository's **snapshot-tier** starter policy because it
is small and reproducible. That is not a production tier recommendation.
Choose Snapshot or Vault-Standard from the workload's recovery, retention,
regional-resilience, and cost requirements before protecting production data.

## Read this before running anything

- The five Microsoft Azure Files Azure Policy definitions are **Preview**.
  Azure Files Vault-Standard backup itself is generally available; the Policy
  automation and cross-subscription backup/restore have separate lifecycle
  states.
- These commands create billable Azure Backup and storage resources. Use a
  budget, a disposable subscription when possible, and the tiny canary scope
  shown here. An `ExpiresAfter` tag is bookkeeping, not an automatic TTL.
- The flow supports classic
  `Microsoft.Storage/storageAccounts/fileServices/shares` **SMB** shares. NFS
  is unsupported by Azure Backup. Microsoft's four current DINE definitions
  do not filter NFS even though its audit definition does, so never broadly
  remediate a mixed SMB/NFS scope.
- `DoNotEnforce` stops the assignment effect, but Azure permits an operator to
  start a remediation task manually. Do not create remediation until the
  explicit promotion step.
- Do not run the reconciler with `--apply` while a DINE remediation is active.
  Pick one writer for a wave and use the other as a dry-run coverage check.
- Empty Policy output is **inconclusive**, not compliant. Stop if the canary
  does not produce a state record.
- Policy compliance and `IRPending` are not recovery proof. Complete the
  backup, recovery-point, restore, and byte-comparison steps.
- Deleting Policy/RBAC objects does not stop an already configured backup and
  does not stop its retention costs. Backup-data cleanup is a separate,
  destructive decision.

The repository's caller-driven reconciler, backup job, recovery point, and
alternate restore were live-tested on 2026-08-25. That test identity could not
grant the Preview DINE assignment identity's roles, and Policy produced no
state during that test window, so this project does **not** describe DINE as
live-proven. See the [qualified live-test
record](LIVE-TEST-2026-08-25-file-share-backup-enforcement.md).

## 1. Clone a reproducible version and check tools

Use Bash in Azure Cloud Shell, Linux, or WSL. Release `v1.1.0` is the first
tag that contains this enforcement runbook and reconciler.

```bash
git clone https://github.com/kevo099/azure-enterprise-policy-baseline.git
cd azure-enterprise-policy-baseline
git checkout v1.1.0

az version --query '"azure-cli"' -o tsv
python3 --version
jq --version
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

The automation requires Python 3.10 or newer and was live-tested with Azure
CLI 2.89.0. Confirm that `az backup deleted-vault`, `az storage share-rm`, and
`az policy remediation` exist in the installed CLI before continuing.

## 2. Select and prove the Azure context

Replace only the first three values. All later names are isolated and derived
from them.

```bash
set -euo pipefail
umask 077

SUBSCRIPTION_ID="<subscription-id>"
LOCATION="eastus2"
EXPIRES_AFTER="<yyyy-mm-dd>"

az account set --subscription "$SUBSCRIPTION_ID"
az account show \
  --query '{subscription:name, subscriptionId:id, tenantId:tenantId, user:user.name}' \
  -o table
test "$(az account show --query id -o tsv)" = "$SUBSCRIPTION_ID"

RUN_SUFFIX="$(date -u +%Y%m%d%H%M%S)"
SUB_TOKEN="$(printf '%s' "$SUBSCRIPTION_ID" | tr -d '-' | cut -c1-8)"
SOURCE_RG="rg-afs-canary-$RUN_SUFFIX"
BACKUP_RG="rg-afs-backup-$RUN_SUFFIX"
STORAGE_ACCOUNT="stafs${SUB_TOKEN}$(date -u +%H%M%S)"
SOURCE_SHARE="source-data"
RESTORE_SHARE="restore-check"
VAULT="rsv-afs-$RUN_SUFFIX"
BACKUP_POLICY="afs-snapshot-daily"
ASSIGNMENT="afs-backup-$RUN_SUFFIX"
REMEDIATION="afs-backup-initial-$RUN_SUFFIX"

SOURCE_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$SOURCE_RG"
VAULT_RG_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$BACKUP_RG"
DINE_POLICY_NAME="e159e079-0ddd-4905-97c9-79a8fca6d880"
DINE_POLICY_ID="/providers/Microsoft.Authorization/policyDefinitions/$DINE_POLICY_NAME"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_nonempty() {
  [ -n "$1" ] || fail "$2"
}

require_equal() {
  [ "$1" = "$2" ] || fail "$3 (expected '$2', got '$1')"
}

require_equal_ci() {
  [ "${1,,}" = "${2,,}" ] || fail "$3 (expected '$2', got '$1')"
}

save_state() {
  printf '%s=%q\n' "$1" "${!1}" >> "$CANARY_STATE_FILE"
}

CANARY_STATE_FILE="$PWD/azure-files-canary-state.env"
{
  printf 'set -euo pipefail\n'
  declare -f fail require_nonempty require_equal require_equal_ci save_state
  for CANARY_VARIABLE in \
    CANARY_STATE_FILE SUBSCRIPTION_ID LOCATION EXPIRES_AFTER RUN_SUFFIX \
    SOURCE_RG BACKUP_RG STORAGE_ACCOUNT SOURCE_SHARE RESTORE_SHARE VAULT \
    BACKUP_POLICY ASSIGNMENT REMEDIATION SOURCE_SCOPE VAULT_RG_SCOPE \
    DINE_POLICY_NAME DINE_POLICY_ID; do
    printf '%s=%q\n' "$CANARY_VARIABLE" "${!CANARY_VARIABLE}"
  done
} > "$CANARY_STATE_FILE"
printf 'Saved nonsecret recovery context to %s\n' "$CANARY_STATE_FILE"
```

Run the canary in a dedicated shell. If a fail-closed gate ends that shell,
return to the repository and run
`source azure-files-canary-state.env` before starting teardown. The state file
contains resource names/IDs, not credentials, and is gitignored.

The operator needs permission to create the storage/vault resources, plus:

| Actor | Required permission/scope |
|---|---|
| Assignment creator | `Microsoft.Authorization/policyAssignments/write` at the source scope, normally Resource Policy Contributor or Owner |
| Role grantor | `Microsoft.Authorization/roleAssignments/write` at both scopes, normally Owner, User Access Administrator, or Role Based Access Control Administrator |
| Assignment managed identity | Storage Account Contributor at the source scope; Backup Contributor at the target vault **resource group** |
| Reconciler identity, if used | Reader for inventory, Storage Account Contributor at source, and Backup Contributor at the target vault resource group |

Register and wait for the providers used by this flow:

```bash
az provider register --namespace Microsoft.Storage --wait
az provider register --namespace Microsoft.RecoveryServices --wait
az provider register --namespace Microsoft.PolicyInsights --wait

az provider show --namespace Microsoft.Storage --query registrationState -o tsv
az provider show --namespace Microsoft.RecoveryServices --query registrationState -o tsv
az provider show --namespace Microsoft.PolicyInsights --query registrationState -o tsv
```

The reconciler also reads Azure Resource Graph. Its provider is normally
registered by default; prove read access without changing provider state:

```bash
ARM_ENDPOINT="$(az cloud show --query endpoints.resourceManager -o tsv)"
GRAPH_RESULT_COUNT="$(az rest \
  --method post \
  --url "${ARM_ENDPOINT%/}/providers/Microsoft.ResourceGraph/resources?api-version=2024-04-01" \
  --body "$(jq -n --arg subscription "$SUBSCRIPTION_ID" '{subscriptions:[$subscription],query:"Resources | take 1",options:{resultFormat:"objectArray"}}')" \
  --query totalRecords -o tsv)"
require_nonempty "$GRAPH_RESULT_COUNT" "Azure Resource Graph preflight returned no count"
```

Finally, inspect Microsoft's **current** Preview contract instead of assuming
that the checked date/version is permanent:

```bash
DINE_VERSION="$(az policy definition show --name "$DINE_POLICY_NAME" \
  --query metadata.version -o tsv)"
case "$DINE_VERSION" in
  2.0.*) ;;
  *) fail "review the live built-in version before continuing: $DINE_VERSION" ;;
esac

az policy definition show --name "$DINE_POLICY_NAME" \
  --query '{name:name, displayName:displayName, version:metadata.version, parameters:parameters, roles:policyRule.then.details.roleDefinitionIds}' \
  -o jsonc
```

At the time of this runbook, it is `2.0.0-preview` and declares Storage
Account Contributor (`17d1049b-9a84-46fb-8f53-869881c3d3ab`) and Backup
Contributor (`5e467623-bb1f-42f4-a55d-6e525e11384b`). Stop and review this
runbook if the live contract differs.

## 3. Create the isolated source and fixture

```bash
az group create --name "$SOURCE_RG" --location "$LOCATION" \
  --tags Purpose=AzureFilesBackupCanary ExpiresAfter="$EXPIRES_AFTER"

az group create --name "$BACKUP_RG" --location "$LOCATION" \
  --tags Purpose=AzureFilesBackupCanary ExpiresAfter="$EXPIRES_AFTER"

az storage account create \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$SOURCE_RG" \
  --location "$LOCATION" \
  --kind StorageV2 \
  --sku Standard_LRS \
  --https-only true \
  --min-tls-version TLS1_2 \
  --allow-shared-key-access true \
  --tags Purpose=AzureFilesBackupCanary ExpiresAfter="$EXPIRES_AFTER"

SOURCE_SHARE_ID="$(az storage share-rm create \
  --resource-group "$SOURCE_RG" \
  --storage-account "$STORAGE_ACCOUNT" \
  --name "$SOURCE_SHARE" \
  --enabled-protocols SMB \
  --quota 5 \
  --query id -o tsv)"
require_nonempty "$SOURCE_SHARE_ID" "source share creation returned no resource ID"
save_state SOURCE_SHARE_ID
```

The canary deliberately enables account-key access because the current Azure
Files backup support matrix requires it for backup and restore. A restricted
storage firewall must also allow the documented trusted-service path. Validate
private-only production designs against the current matrix; a Recovery
Services vault private endpoint does not apply to Azure Files in the same way
as it does to other workloads.

Upload a unique file without printing the storage key:

```bash
CANARY_DIR="$(mktemp -d)"
CANARY_FILE="$CANARY_DIR/canary.txt"
RESTORED_FILE="$CANARY_DIR/restored-canary.txt"
printf 'Azure Files backup canary %s\n' "$RUN_SUFFIX" > "$CANARY_FILE"

AZURE_STORAGE_KEY="$(az storage account keys list \
  --resource-group "$SOURCE_RG" \
  --account-name "$STORAGE_ACCOUNT" \
  --query '[0].value' -o tsv)"
export AZURE_STORAGE_KEY
az storage file upload \
  --account-name "$STORAGE_ACCOUNT" \
  --share-name "$SOURCE_SHARE" \
  --path canary.txt \
  --source "$CANARY_FILE" \
  --no-progress \
  --only-show-errors
unset AZURE_STORAGE_KEY
sha256sum "$CANARY_FILE"
```

## 4. Create the vault and choose the backup tier

Create a Recovery Services vault in the **same region** as the storage
account. Choose vault redundancy and cross-region-restore posture before the
first protected item; later changes can be constrained. Locally redundant
storage is used here only to keep a disposable canary simple.

```bash
az backup vault create \
  --name "$VAULT" \
  --resource-group "$BACKUP_RG" \
  --location "$LOCATION" \
  --job-failure-alerts Enable \
  --tags Purpose=AzureFilesBackupCanary ExpiresAfter="$EXPIRES_AFTER"

az backup vault backup-properties set \
  --name "$VAULT" \
  --resource-group "$BACKUP_RG" \
  --backup-storage-redundancy LocallyRedundant \
  --job-failure-alerts Enable

az backup policy create \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --name "$BACKUP_POLICY" \
  --backup-management-type AzureStorage \
  --workload-type AzureFileShare \
  --policy examples/azure-files-backup-policy.json

BACKUP_POLICY_ID="$(az backup policy show \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --name "$BACKUP_POLICY" \
  --query id -o tsv)"
require_nonempty "$BACKUP_POLICY_ID" "backup policy returned no resource ID"
save_state BACKUP_POLICY_ID

az backup policy show \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --name "$BACKUP_POLICY" \
  --query '{management:properties.backupManagementType, workload:properties.workLoadType, schedule:properties.schedulePolicy.scheduleRunFrequency, snapshotRetention:properties.retentionPolicy.dailySchedule.retentionDuration, vaultRetention:properties.vaultRetentionPolicy}' \
  -o jsonc
```

The checked-in policy is a daily 02:00 UTC, 30-day **Snapshot** policy. Its
recovery points remain in the source storage account; it is not an offsite
copy. For production, review Microsoft's current Snapshot versus
Vault-Standard guidance. Vault-Standard adds vault retention/storage cost,
has a narrower account/region/size matrix, and currently supports full-share
rather than item-level vaulted restore. Do not call the checked-in JSON
Vault-Standard merely because it is attached to a Recovery Services vault.
Recovery Services vault immutability, isolated vault storage, and
cross-region restore protect Vault-Standard data; they do **not** protect this
operational Snapshot tier. Snapshot protection instead depends on the source
storage account's resilience and Azure Files share soft-delete controls.

## 5. Prove the initial gap with the dry-run reconciler

Use the exact resource group and region. Subscription-wide apply is possible
when `--source-resource-group` is omitted, so never omit it casually.

```bash
if python3 scripts/ensure_file_share_backup.py \
  --subscription "$SUBSCRIPTION_ID" \
  --source-resource-group "$SOURCE_RG" \
  --source-location "$LOCATION" \
  --vault-resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --policy-name "$BACKUP_POLICY" \
  --max-actions 5 \
  --max-new-registrations 2; then
  echo "Expected dry-run work, but the reconciler returned no gap" >&2
  exit 1
else
  RECONCILER_RC=$?
  require_equal "$RECONCILER_RC" 2 "initial reconciler result was unexpected"
fi
```

Exit `2` is expected here because an eligible unprotected share exists. In an
unattended job, use `--json` and distinguish planned work, blockers, and
unsupported NFS rather than treating every exit `2` identically.

## 6. Create the Preview assignment in report-only mode

Generate a local parameter file from the checked-in example:

```bash
jq \
  --arg location "$LOCATION" \
  --arg policyId "$BACKUP_POLICY_ID" \
  '.vaultLocation.value = $location | .backupPolicyId.value = $policyId' \
  examples/azure-files-backup-assignment.example.json \
  > azure-files-backup-assignment.json

jq empty azure-files-backup-assignment.json
```

`SkipAzureFilesBackup=true` is a **storage-account tag** exclusion convention;
shares do not have ARM tags. `registerStorageAccount=true` lets the built-in
register an unregistered account. Setting it false is safe only when the
account is already registered to the intended vault.

Create the system identity but prevent DINE from running:

```bash
ASSIGNMENT_ID="$(az policy assignment create \
  --name "$ASSIGNMENT" \
  --display-name "Protect Azure Files canary in $LOCATION" \
  --description "Preview Azure Files DINE canary; promote only after review" \
  --scope "$SOURCE_SCOPE" \
  --policy "$DINE_POLICY_ID" \
  --definition-version '2.0.*' \
  --location "$LOCATION" \
  --mi-system-assigned \
  --enforcement-mode DoNotEnforce \
  --params @azure-files-backup-assignment.json \
  --query id -o tsv)"
require_nonempty "$ASSIGNMENT_ID" "policy assignment returned no resource ID"
save_state ASSIGNMENT_ID

PRINCIPAL_ID="$(az policy assignment show \
  --name "$ASSIGNMENT" \
  --scope "$SOURCE_SCOPE" \
  --query identity.principalId -o tsv)"
require_nonempty "$PRINCIPAL_ID" "policy assignment returned no principal ID"
save_state PRINCIPAL_ID

ASSIGNMENT_JSON="$(az policy assignment show \
  --name "$ASSIGNMENT" \
  --scope "$SOURCE_SCOPE" -o json)"
require_equal "$(printf '%s' "$ASSIGNMENT_JSON" | jq -r .enforcementMode)" \
  DoNotEnforce "assignment is not report-only"
require_equal_ci "$(printf '%s' "$ASSIGNMENT_JSON" | jq -r .policyDefinitionId)" \
  "$DINE_POLICY_ID" "assignment references the wrong policy"
require_equal_ci "$(printf '%s' "$ASSIGNMENT_JSON" | jq -r .parameters.backupPolicyId.value)" \
  "$BACKUP_POLICY_ID" "assignment references the wrong backup policy"
require_equal "$(printf '%s' "$ASSIGNMENT_JSON" | jq -r .parameters.effect.value)" \
  DeployIfNotExists "assignment effect is not DINE"
ASSIGNED_DEFINITION_VERSION="$(printf '%s' "$ASSIGNMENT_JSON" | jq -r '.definitionVersion // empty')"
case "$ASSIGNED_DEFINITION_VERSION" in
  2.0.*) ;;
  *) fail "assignment did not retain the reviewed 2.0.* definition line" ;;
esac

az policy state trigger-scan --resource-group "$SOURCE_RG"
POLICY_STATES="$(az policy state list \
  --resource-group "$SOURCE_RG" \
  --policy-assignment "$ASSIGNMENT" \
  --query '[].{resource:resourceId,state:complianceState,timestamp:timestamp}' \
  -o json)"
printf '%s\n' "$POLICY_STATES" | jq .

if [ "$(printf '%s' "$POLICY_STATES" | jq 'length')" -eq 0 ]; then
  fail "INCONCLUSIVE: no Policy state record; do not promote"
fi

INITIAL_SOURCE_STATE="$(az policy state list \
  --resource "$SOURCE_SHARE_ID" \
  --policy-assignment "$ASSIGNMENT" \
  --query '[0].complianceState' -o tsv)"
require_nonempty "$INITIAL_SOURCE_STATE" \
  "INCONCLUSIVE: source share has no Policy state; do not promote"
require_equal "$INITIAL_SOURCE_STATE" NonCompliant \
  "source share did not evaluate NonCompliant before promotion"
```

The exact source share is now machine-gated as `NonCompliant`. That is a Policy
signal only; it is not yet a backup-health signal.

## 7. Grant exact roles, verify them, then promote

The CLI does not grant DINE roles automatically. New identity propagation can
briefly delay role creation; if it does, retry only the failed role command.

```bash
STORAGE_ROLE_ASSIGNMENT_ID="$(az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role 17d1049b-9a84-46fb-8f53-869881c3d3ab \
  --scope "$SOURCE_SCOPE" \
  --query id -o tsv)"

BACKUP_ROLE_ASSIGNMENT_ID="$(az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role 5e467623-bb1f-42f4-a55d-6e525e11384b \
  --scope "$VAULT_RG_SCOPE" \
  --query id -o tsv)"

require_nonempty "$STORAGE_ROLE_ASSIGNMENT_ID" \
  "Storage Account Contributor grant returned no ID"
require_nonempty "$BACKUP_ROLE_ASSIGNMENT_ID" \
  "Backup Contributor grant returned no ID"
save_state STORAGE_ROLE_ASSIGNMENT_ID
save_state BACKUP_ROLE_ASSIGNMENT_ID

STORAGE_GRANT_JSON="$(az role assignment list \
  --assignee-object-id "$PRINCIPAL_ID" --all \
  --query "[?id=='$STORAGE_ROLE_ASSIGNMENT_ID'] | [0]" -o json)"
BACKUP_GRANT_JSON="$(az role assignment list \
  --assignee-object-id "$PRINCIPAL_ID" --all \
  --query "[?id=='$BACKUP_ROLE_ASSIGNMENT_ID'] | [0]" -o json)"

require_equal_ci "$(printf '%s' "$STORAGE_GRANT_JSON" | jq -r '.scope // empty')" \
  "$SOURCE_SCOPE" "Storage Account Contributor has the wrong scope"
require_equal_ci "$(printf '%s' "$STORAGE_GRANT_JSON" | jq -r '.roleDefinitionId // empty')" \
  "/subscriptions/$SUBSCRIPTION_ID/providers/Microsoft.Authorization/roleDefinitions/17d1049b-9a84-46fb-8f53-869881c3d3ab" \
  "source grant has the wrong role"
require_equal_ci "$(printf '%s' "$BACKUP_GRANT_JSON" | jq -r '.scope // empty')" \
  "$VAULT_RG_SCOPE" "Backup Contributor has the wrong scope"
require_equal_ci "$(printf '%s' "$BACKUP_GRANT_JSON" | jq -r '.roleDefinitionId // empty')" \
  "/subscriptions/$SUBSCRIPTION_ID/providers/Microsoft.Authorization/roleDefinitions/5e467623-bb1f-42f4-a55d-6e525e11384b" \
  "target grant has the wrong role"

az role assignment list \
  --assignee-object-id "$PRINCIPAL_ID" \
  --all \
  --query '[].{role:roleDefinitionName,scope:scope,id:id}' \
  -o table

az policy assignment update \
  --name "$ASSIGNMENT" \
  --scope "$SOURCE_SCOPE" \
  --enforcement-mode Default

require_equal "$(az policy assignment show \
  --name "$ASSIGNMENT" --scope "$SOURCE_SCOPE" \
  --query enforcementMode -o tsv)" Default \
  "assignment promotion did not persist"
```

Only after that promotion, create remediation for existing shares. Reevaluate
the scope so a fresh assignment does not silently use an empty/stale
noncompliance set:

```bash
az policy remediation create \
  --name "$REMEDIATION" \
  --resource-group "$SOURCE_RG" \
  --policy-assignment "$ASSIGNMENT_ID" \
  --resource-discovery-mode ReEvaluateCompliance

REMEDIATION_STATE=""
for REMEDIATION_ATTEMPT in {1..60}; do
  REMEDIATION_STATE="$(az policy remediation show \
    --name "$REMEDIATION" \
    --resource-group "$SOURCE_RG" \
    --query provisioningState -o tsv)"
  case "$REMEDIATION_STATE" in
    Complete|Succeeded) break ;;
    Failed|Canceled|Cancelled|Cancelling)
      fail "remediation ended in $REMEDIATION_STATE"
      ;;
  esac
  sleep 30
done
case "$REMEDIATION_STATE" in
  Complete|Succeeded) ;;
  *) fail "remediation did not succeed within 30 minutes" ;;
esac

REMEDIATION_DEPLOYMENTS="$(az policy remediation deployment list \
  --name "$REMEDIATION" \
  --resource-group "$SOURCE_RG" -o json)"
printf '%s\n' "$REMEDIATION_DEPLOYMENTS" | jq \
  '[.[] | {deployment:.deploymentId,status:.status,error:.error}]'
[ "$(printf '%s' "$REMEDIATION_DEPLOYMENTS" | jq 'length')" -gt 0 ] || \
  fail "remediation reported no deployments"
require_equal "$(printf '%s' "$REMEDIATION_DEPLOYMENTS" | \
  jq '[.[] | select(.status != "Succeeded")] | length')" 0 \
  "one or more remediation deployments failed"
```

The gate waits up to 30 minutes and rejects empty or failed deployments. DINE
is asynchronous; it is not request-time enforcement.

## 8. Verify configuration before claiming backup

```bash
CONTAINER_NAME=""
ITEM_NAME=""
ITEM_ID=""
ITEM_JSON='{}'
CONFIGURE_STATUS=""
PROTECTION_STATE=""
PROTECTION_HEALTH=""

for CONFIGURE_ATTEMPT in {1..60}; do
  CONFIGURE_STATUS="$(az backup job list \
    --resource-group "$BACKUP_RG" \
    --vault-name "$VAULT" \
    --operation ConfigureBackup \
    --query "sort_by([?properties.entityFriendlyName=='$SOURCE_SHARE'], &properties.startTime)[-1].properties.status" \
    -o tsv)"
  case "$CONFIGURE_STATUS" in
    Failed|Canceled|Cancelled|CompletedWithWarnings)
      fail "ConfigureBackup ended in $CONFIGURE_STATUS"
      ;;
  esac

  CONTAINER_NAME="$(az backup container list \
    --resource-group "$BACKUP_RG" \
    --vault-name "$VAULT" \
    --backup-management-type AzureStorage \
    --query "[?properties.friendlyName=='$STORAGE_ACCOUNT'].name | [0]" \
    -o tsv)"

  if [ -n "$CONTAINER_NAME" ]; then
    ITEM_NAME="$(az backup item list \
      --resource-group "$BACKUP_RG" \
      --vault-name "$VAULT" \
      --container-name "$CONTAINER_NAME" \
      --backup-management-type AzureStorage \
      --workload-type AzureFileShare \
      --query "[?properties.friendlyName=='$SOURCE_SHARE'].name | [0]" \
      -o tsv)"
  fi

  if [ -n "$ITEM_NAME" ]; then
    ITEM_JSON="$(az backup item show \
      --resource-group "$BACKUP_RG" \
      --vault-name "$VAULT" \
      --container-name "$CONTAINER_NAME" \
      --name "$ITEM_NAME" \
      --backup-management-type AzureStorage \
      --workload-type AzureFileShare -o json)"
    ITEM_ID="$(printf '%s' "$ITEM_JSON" | jq -r '.id // empty')"
    PROTECTION_STATE="$(printf '%s' "$ITEM_JSON" | jq -r '.properties.protectionState // empty')"
    PROTECTION_HEALTH="$(printf '%s' "$ITEM_JSON" | jq -r '.properties.protectionStatus // empty')"
  fi

  if [ "$CONFIGURE_STATUS" = Completed ] && \
     [ "$PROTECTION_HEALTH" = Healthy ] && \
     { [ "$PROTECTION_STATE" = IRPending ] || [ "$PROTECTION_STATE" = Protected ]; }; then
    break
  fi
  sleep 30
done

require_equal "$CONFIGURE_STATUS" Completed \
  "ConfigureBackup did not complete within 30 minutes"
require_nonempty "$CONTAINER_NAME" "backup container did not materialize"
require_nonempty "$ITEM_NAME" "protected-item name did not materialize"
require_nonempty "$ITEM_ID" "protected-item ID did not materialize"
require_equal "$PROTECTION_HEALTH" Healthy "protected item is not healthy"
case "$PROTECTION_STATE" in
  IRPending|Protected) ;;
  *) fail "protected item has unexpected state: $PROTECTION_STATE" ;;
esac
save_state CONTAINER_NAME
save_state ITEM_NAME
save_state ITEM_ID

az backup job list \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --operation ConfigureBackup \
  --query "[?properties.entityFriendlyName=='$SOURCE_SHARE'].{item:properties.entityFriendlyName,status:properties.status,started:properties.startTime,ended:properties.endTime}" \
  -o table

printf '%s\n' "$ITEM_JSON" | jq \
  '{state:.properties.protectionState,health:.properties.protectionStatus,lastRecoveryPoint:.properties.lastRecoveryPoint}'
```

`IRPending` means configuration was accepted and the initial recovery is
pending. It is not proof that a usable recovery point exists.

The independent reconciler should now return `0` for the exact canary scope
(it can accept healthy `IRPending` configuration). Keep it dry-run here:

```bash
python3 scripts/ensure_file_share_backup.py \
  --subscription "$SUBSCRIPTION_ID" \
  --source-resource-group "$SOURCE_RG" \
  --source-location "$LOCATION" \
  --vault-resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --policy-name "$BACKUP_POLICY" \
  --max-actions 5 \
  --max-new-registrations 2
```

## 9. Prove a recovery point and alternate restore

Start an on-demand backup, wait, and assert that the terminal state is
`Completed`:

```bash
BACKUP_JOB_NAME="$(az backup protection backup-now \
  --ids "$ITEM_ID" \
  --query name -o tsv)"
require_nonempty "$BACKUP_JOB_NAME" "on-demand backup returned no job name"
save_state BACKUP_JOB_NAME

az backup job wait \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --name "$BACKUP_JOB_NAME" \
  --timeout 3600

BACKUP_STATUS="$(az backup job show \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --name "$BACKUP_JOB_NAME" \
  --query properties.status -o tsv)"
test "$BACKUP_STATUS" = "Completed"

RECOVERY_POINT="$(az backup recoverypoint list \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --container-name "$CONTAINER_NAME" \
  --item-name "$ITEM_NAME" \
  --backup-management-type AzureStorage \
  --workload-type AzureFileShare \
  --query 'sort_by(@,&properties.recoveryPointTime)[-1].name' \
  -o tsv)"
require_nonempty "$RECOVERY_POINT" "completed backup produced no recovery point"
save_state RECOVERY_POINT

az backup recoverypoint list \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --container-name "$CONTAINER_NAME" \
  --item-name "$ITEM_NAME" \
  --backup-management-type AzureStorage \
  --workload-type AzureFileShare \
  --query '[].{name:name,time:properties.recoveryPointTime,type:properties.recoveryPointType,tier:properties.recoveryPointTierDetails}' \
  -o table
```

Pause DINE before creating the restore target. Otherwise that ARM-created
share is eligible for asynchronous auto-protection and can race the restore:

```bash
az policy assignment update \
  --name "$ASSIGNMENT" \
  --scope "$SOURCE_SCOPE" \
  --enforcement-mode DoNotEnforce

require_equal "$(az policy assignment show \
  --name "$ASSIGNMENT" --scope "$SOURCE_SCOPE" \
  --query enforcementMode -o tsv)" DoNotEnforce \
  "DINE did not pause before restore-target creation"
```

Now create an empty destination share, restore to it, and compare the bytes:

```bash
az storage share-rm create \
  --resource-group "$SOURCE_RG" \
  --storage-account "$STORAGE_ACCOUNT" \
  --name "$RESTORE_SHARE" \
  --enabled-protocols SMB \
  --quota 5

RESTORE_JOB_NAME="$(az backup restore restore-azurefileshare \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --container-name "$CONTAINER_NAME" \
  --item-name "$ITEM_NAME" \
  --rp-name "$RECOVERY_POINT" \
  --restore-mode AlternateLocation \
  --resolve-conflict Overwrite \
  --target-resource-group-name "$SOURCE_RG" \
  --target-storage-account "$STORAGE_ACCOUNT" \
  --target-file-share "$RESTORE_SHARE" \
  --query name -o tsv)"
require_nonempty "$RESTORE_JOB_NAME" "alternate restore returned no job name"
save_state RESTORE_JOB_NAME

az backup job wait \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --name "$RESTORE_JOB_NAME" \
  --timeout 3600

RESTORE_STATUS="$(az backup job show \
  --resource-group "$BACKUP_RG" \
  --vault-name "$VAULT" \
  --name "$RESTORE_JOB_NAME" \
  --query properties.status -o tsv)"
require_equal "$RESTORE_STATUS" Completed "alternate restore did not complete"

AZURE_STORAGE_KEY="$(az storage account keys list \
  --resource-group "$SOURCE_RG" \
  --account-name "$STORAGE_ACCOUNT" \
  --query '[0].value' -o tsv)"
export AZURE_STORAGE_KEY
az storage file download \
  --account-name "$STORAGE_ACCOUNT" \
  --share-name "$RESTORE_SHARE" \
  --path canary.txt \
  --dest "$RESTORED_FILE" \
  --no-progress \
  --only-show-errors
unset AZURE_STORAGE_KEY

sha256sum "$CANARY_FILE" "$RESTORED_FILE"
cmp "$CANARY_FILE" "$RESTORED_FILE"
echo "PASS: recovery point restored the fixture byte-for-byte"

az policy state trigger-scan --resource-group "$SOURCE_RG"
FINAL_SOURCE_STATE="$(az policy state list \
  --resource "$SOURCE_SHARE_ID" \
  --policy-assignment "$ASSIGNMENT" \
  --query '[0].complianceState' -o tsv)"
require_nonempty "$FINAL_SOURCE_STATE" \
  "INCONCLUSIVE: final source Policy state is empty"
require_equal "$FINAL_SOURCE_STATE" Compliant \
  "source share did not evaluate Compliant after recovery proof"
```

The proof ladder is now complete: Policy state, remediation deployment,
ConfigureBackup job, healthy protected item, completed backup job, listed
recovery point, completed alternate restore, and content comparison.

## 10. Operate it continuously

- Assign once per source subscription/region/target policy. The existing-vault
  definitions support resource-group or subscription scope, not management
  groups.
- FileREST-created shares do not emit the same ARM create/update event. Run
  periodic Policy scans and a serialized reconciler dry run even with DINE.
- Use a managed/workload identity for scheduling, explicitly pass
  `--subscription`, keep the exact source RG/region during rollout, retain
  `--json` evidence, and alert on exits `1`, `2`, or `3`. Parse status details;
  exit `2` can mean planned work or a blocker, and `3` means eligible SMB work
  succeeded while unsupported NFS remains.
- Never overlap an `--apply` run with Policy remediation or a manual
  protection wave. The Azure service operations are rerunnable, not atomic.
- Alert on failed backup jobs and stale recovery points. Schedule restore
  drills; Policy compliance alone never proves RPO, retention, or recovery.
- Replicating the **backup policy resource** to many regional vaults is a
  separate IaC/custom-DINE problem. First create and verify each reviewed
  `Microsoft.RecoveryServices/vaults/backupPolicies` resource, then point one
  share-enforcement assignment at that real policy ID. The five Microsoft
  built-ins do not generically clone an arbitrary organization policy.
- Cross-subscription Vault-Standard is an advanced Preview topology with
  same-tenant, same-region, provider, RBAC, and networking prerequisites. It
  was not live-tested by this project. Never use this repository's
  same-subscription reconciler against a central vault in another
  subscription; it cannot prove foreign-vault registration safely.

## 11. Teardown: governance and backup data are different

### A. Stop governance writes and remove its exact grants

Capture `PRINCIPAL_ID` and both role-assignment IDs before deleting the policy
assignment; its system identity disappears with the assignment.

```bash
STORAGE_ROLE_ASSIGNMENT_ID="${STORAGE_ROLE_ASSIGNMENT_ID:-}"
BACKUP_ROLE_ASSIGNMENT_ID="${BACKUP_ROLE_ASSIGNMENT_ID:-}"

if az policy assignment show \
  --name "$ASSIGNMENT" --scope "$SOURCE_SCOPE" >/dev/null 2>&1; then
  PRINCIPAL_ID="${PRINCIPAL_ID:-$(az policy assignment show \
    --name "$ASSIGNMENT" --scope "$SOURCE_SCOPE" \
    --query identity.principalId -o tsv)}"

  if [ -n "$PRINCIPAL_ID" ]; then
    STORAGE_ROLE_ASSIGNMENT_ID="${STORAGE_ROLE_ASSIGNMENT_ID:-$(az role assignment list \
      --assignee-object-id "$PRINCIPAL_ID" \
      --role 17d1049b-9a84-46fb-8f53-869881c3d3ab \
      --scope "$SOURCE_SCOPE" --query '[0].id' -o tsv)}"
    BACKUP_ROLE_ASSIGNMENT_ID="${BACKUP_ROLE_ASSIGNMENT_ID:-$(az role assignment list \
      --assignee-object-id "$PRINCIPAL_ID" \
      --role 5e467623-bb1f-42f4-a55d-6e525e11384b \
      --scope "$VAULT_RG_SCOPE" --query '[0].id' -o tsv)}"
  fi

  az policy assignment update \
    --name "$ASSIGNMENT" \
    --scope "$SOURCE_SCOPE" \
    --enforcement-mode DoNotEnforce

  if az policy remediation show \
    --name "$REMEDIATION" --resource-group "$SOURCE_RG" >/dev/null 2>&1; then
    az policy remediation delete \
      --name "$REMEDIATION" \
      --resource-group "$SOURCE_RG"
  fi
else
  echo "Policy assignment does not exist; skipping governance cleanup"
fi

if [ -n "$STORAGE_ROLE_ASSIGNMENT_ID" ]; then
  az role assignment delete --ids "$STORAGE_ROLE_ASSIGNMENT_ID"
fi
if [ -n "$BACKUP_ROLE_ASSIGNMENT_ID" ]; then
  az role assignment delete --ids "$BACKUP_ROLE_ASSIGNMENT_ID"
fi

if az policy assignment show \
  --name "$ASSIGNMENT" --scope "$SOURCE_SCOPE" >/dev/null 2>&1; then
  az policy assignment delete \
    --name "$ASSIGNMENT" \
    --scope "$SOURCE_SCOPE"
fi
```

Deleting a remediation does not reverse deployments it already completed.
Deleting the assignment and role grants leaves the protected item and its
recovery points in place.

### B. Make a separate backup-data retention decision

For a real workload, stop here and obtain explicit owner/retention approval.
`--delete-backup-data false` stops future protection but retains chargeable
recovery points. `--delete-backup-data true` is destructive, although Azure
soft delete can retain recoverable tombstones. The following is appropriate
only for the disposable canary created by this runbook:

```bash
if az backup vault show \
  --resource-group "$BACKUP_RG" --name "$VAULT" >/dev/null 2>&1; then
  CANARY_ITEMS_JSON="$(az backup item list \
    --resource-group "$BACKUP_RG" \
    --vault-name "$VAULT" \
    --backup-management-type AzureStorage \
    --workload-type AzureFileShare \
    -o json)"
  printf '%s\n' "$CANARY_ITEMS_JSON" | jq \
    '[.[] | {name:.name,share:.properties.friendlyName,state:.properties.protectionState,id:.id}]'

  ACTIVE_CANARY_ITEM_COUNT="$(printf '%s' "$CANARY_ITEMS_JSON" | jq \
    '[.[] | select(.properties.protectionState == "Protected" or .properties.protectionState == "IRPending")] | length')"

  case "$ACTIVE_CANARY_ITEM_COUNT" in
    0)
      echo "Vault has no active canary item; skipping backup-data deletion"
      ;;
    1)
      ACTIVE_ITEM_JSON="$(printf '%s' "$CANARY_ITEMS_JSON" | jq \
        '[.[] | select(.properties.protectionState == "Protected" or .properties.protectionState == "IRPending")] | first')"
      ACTIVE_CANARY_SHARE="$(printf '%s' "$ACTIVE_ITEM_JSON" | jq -r \
        '.properties.friendlyName // empty')"
      require_equal "$ACTIVE_CANARY_SHARE" "$SOURCE_SHARE" \
        "the only active item is not the expected source canary share"
      EXPECTED_SOURCE_ACCOUNT_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$SOURCE_RG/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT"
      ACTIVE_SOURCE_ACCOUNT_ID="$(printf '%s' "$ACTIVE_ITEM_JSON" | jq -r \
        '.properties.sourceResourceId // empty')"
      require_equal_ci "$ACTIVE_SOURCE_ACCOUNT_ID" "$EXPECTED_SOURCE_ACCOUNT_ID" \
        "the only active item belongs to an unexpected storage account"
      DISCOVERED_ITEM_ID="$(printf '%s' "$ACTIVE_ITEM_JSON" | jq -r \
        '.id // empty')"
      require_nonempty "$DISCOVERED_ITEM_ID" \
        "source canary protected item did not expose an ID"
      SAVED_ITEM_ID="${ITEM_ID:-}"
      if [ -n "$SAVED_ITEM_ID" ]; then
        require_equal_ci "$DISCOVERED_ITEM_ID" "$SAVED_ITEM_ID" \
          "rediscovered protected item does not match the saved item ID"
      fi
      ITEM_ID="$DISCOVERED_ITEM_ID"

      DELETE_JOB_NAME="$(az backup protection disable \
        --ids "$ITEM_ID" \
        --delete-backup-data true \
        --yes \
        --query name -o tsv)"
      require_nonempty "$DELETE_JOB_NAME" "backup-data deletion returned no job name"

      az backup job wait \
        --resource-group "$BACKUP_RG" \
        --vault-name "$VAULT" \
        --name "$DELETE_JOB_NAME" \
        --timeout 3600

      DELETE_STATUS="$(az backup job show \
        --resource-group "$BACKUP_RG" \
        --vault-name "$VAULT" \
        --name "$DELETE_JOB_NAME" \
        --query properties.status -o tsv)"
      require_equal "$DELETE_STATUS" Completed \
        "backup-data deletion did not complete"
      ;;
    *)
      fail "isolated vault contains more than one active item; refusing automatic deletion"
      ;;
  esac

  POST_DELETE_ITEMS="$(az backup item list \
    --resource-group "$BACKUP_RG" \
    --vault-name "$VAULT" \
    --backup-management-type AzureStorage \
    --workload-type AzureFileShare -o json)"
  require_equal "$(printf '%s' "$POST_DELETE_ITEMS" | jq \
    '[.[] | select(.properties.protectionState == "Protected" or .properties.protectionState == "IRPending")] | length')" 0 \
    "active protected items remain after deletion"

  CONTAINERS_JSON="$(az backup container list \
    --resource-group "$BACKUP_RG" \
    --vault-name "$VAULT" \
    --backup-management-type AzureStorage \
    -o json)"
  MATCHING_CONTAINER_COUNT="$(printf '%s' "$CONTAINERS_JSON" | jq \
    --arg account "$STORAGE_ACCOUNT" \
    '[.[] | select((.properties.friendlyName // "" | ascii_downcase) == ($account | ascii_downcase))] | length')"

  case "$MATCHING_CONTAINER_COUNT" in
    0)
      echo "Storage account is not registered to the vault; skipping container cleanup"
      ;;
    1)
      CONTAINER_NAME="$(printf '%s' "$CONTAINERS_JSON" | jq -r \
        --arg account "$STORAGE_ACCOUNT" \
        '[.[] | select((.properties.friendlyName // "" | ascii_downcase) == ($account | ascii_downcase))] | first | .name // empty')"
      require_nonempty "$CONTAINER_NAME" \
        "matching storage container did not expose a name"
      CONTAINER_REGISTRATION="$(printf '%s' "$CONTAINERS_JSON" | jq -r \
        --arg account "$STORAGE_ACCOUNT" \
        '[.[] | select((.properties.friendlyName // "" | ascii_downcase) == ($account | ascii_downcase))] | first | .properties.registrationStatus // empty')"

      if [ "$CONTAINER_REGISTRATION" = Registered ]; then
        az backup container unregister \
          --resource-group "$BACKUP_RG" \
          --vault-name "$VAULT" \
          --container-name "$CONTAINER_NAME" \
          --backup-management-type AzureStorage \
          --yes
      fi

      CONTAINER_REGISTRATION="$(az backup container list \
        --resource-group "$BACKUP_RG" \
        --vault-name "$VAULT" \
        --backup-management-type AzureStorage \
        --query "[?name=='$CONTAINER_NAME'].properties.registrationStatus | [0]" \
        -o tsv)"
      [ "$CONTAINER_REGISTRATION" != Registered ] || \
        fail "storage account remains actively registered to the vault"
      ;;
    *)
      fail "multiple vault containers match the canary storage account"
      ;;
  esac
else
  echo "Recovery Services vault does not exist; skipping backup-data cleanup"
fi
```

Because DINE was paused before creating the restore target, a completed canary
has exactly one active item. Teardown also supports an earlier fail-closed exit:
zero active items or a missing vault is a safe skip. More than one active item,
an unexpected share, or multiple matching storage containers fails closed. The
container is rediscovered by the storage account's friendly name rather than
assuming the configure step saved `CONTAINER_NAME`.

Azure Files share soft delete on the storage account and Backup vault/item
soft delete are separate controls. Soft-deleted items or a deleted-vault
record can remain recoverable and can block teardown or re-registration. The current
CLI does not provide every purge operation; do not disable platform safeguards
merely to make a test appear cleaner.

### C. Remove only the tagged canary resource groups

Prove ownership before deleting either group:

```bash
if [ "$(az group exists --name "$SOURCE_RG")" = true ]; then
  require_equal "$(az group show --name "$SOURCE_RG" --query tags.Purpose -o tsv)" \
    AzureFilesBackupCanary "source resource-group ownership guard failed"
  az group delete --name "$SOURCE_RG" --yes
else
  echo "Source resource group does not exist; skipping its deletion"
fi

if [ "$(az group exists --name "$BACKUP_RG")" = true ]; then
  require_equal "$(az group show --name "$BACKUP_RG" --query tags.Purpose -o tsv)" \
    AzureFilesBackupCanary "backup resource-group ownership guard failed"
  az group delete --name "$BACKUP_RG" --yes
else
  echo "Backup resource group does not exist; skipping its deletion"
fi

require_equal "$(az group exists --name "$SOURCE_RG")" false \
  "source resource group still exists"
require_equal "$(az group exists --name "$BACKUP_RG")" false \
  "backup resource group still exists"

echo "Active canary resource groups are gone. Service-retained deleted-vault records, if any:"
az backup deleted-vault list --location "$LOCATION" -o table
```

Vault deletion can fail while protected or soft-deleted items remain. Follow
Microsoft's vault-deletion workflow rather than weakening retention,
immutability, multi-user authorization, or soft-delete controls. Final
verification must distinguish **zero active resources** from service-retained
soft-delete data.

## Production gotchas checklist

| Check | Why it matters |
|---|---|
| Snapshot vs Vault-Standard | Snapshot recovery points remain in the source account; Vault-Standard adds an isolated vault tier, cost, limits, and different restore behavior |
| Vault redundancy, CRR, immutability, MUA | These protect Vault-Standard data, not operational Snapshot-tier data; some settings become constrained or irreversible and locked immutability is WORM |
| SMB only | NFS Azure Files is unsupported, while current DINE policy JSON lacks the audit policy's SMB predicate |
| Account kind and scale | Snapshot and Vault-Standard have different GPv1/GPv2/FileStorage, share-size, and object-count support |
| Tier changes | Treat Snapshot-to-Vault-Standard as a reviewed reconfiguration/one-way protection change; do not assume a reversible in-place toggle |
| Vaulted restore | Vault-Standard currently restores a full share rather than individual files and the target needs enough capacity/headroom |
| Smart Tier | Azure Files Smart Tier is not currently a supported backup target; recheck the support matrix before rollout |
| Failover and CRR | Customer-managed storage-account failover, paired-region restore, and reprotection have workload-specific limits; test the exact regional design |
| Same region | Source account and vault must be compatible by region; one assignment targets one location/policy design |
| Key and network access | Current backup/restore requires account key access and documented firewall/trusted-service reachability |
| Registration ownership | A storage account can be registered to only one Recovery Services vault; foreign or soft-deleted associations are blockers |
| Daily/vault limits | Budget the current registration/protection quotas and use small remediation waves; caps in the script are not quota reservations |
| FileREST creation | Data-plane share creation can miss the ARM event path; periodic scan/reconciliation is still required |
| Policy state | No state is inconclusive; compliant is only an existence correlation, not health/freshness/restoreability |
| Cleanup and cost | Removing Policy does not stop backup; retained or soft-deleted recovery points can continue to exist and may incur cost |

## Official references

- [Automate Azure Files backup with Azure Policy](https://learn.microsoft.com/en-us/azure/backup/backup-azure-files-policy-automation)
- [Azure Files backup overview](https://learn.microsoft.com/en-us/azure/backup/azure-file-share-backup-overview)
- [Azure Files backup support matrix](https://learn.microsoft.com/en-us/azure/backup/azure-file-share-support-matrix)
- [Safe deployment of DeployIfNotExists](https://learn.microsoft.com/en-us/azure/governance/policy/how-to/policy-safe-deployment-practices)
- [Remediate noncompliant resources](https://learn.microsoft.com/en-us/azure/governance/policy/how-to/remediate-resources)
- [Manage Azure Files backup with Azure CLI](https://learn.microsoft.com/en-us/azure/backup/manage-afs-backup-cli)
- [Manage Azure Files backup tiers and policies](https://learn.microsoft.com/en-us/azure/backup/manage-afs-backup)
- [Restore Azure file shares](https://learn.microsoft.com/en-us/azure/backup/restore-afs)
- [Immutable vault behavior and workload scope](https://learn.microsoft.com/en-us/azure/backup/backup-azure-immutable-vault-concept)
- [Azure Backup pricing](https://azure.microsoft.com/en-us/pricing/details/backup/)
- [Delete a Recovery Services vault](https://learn.microsoft.com/en-us/azure/backup/backup-azure-delete-vault)
- [Microsoft's current existing-vault/no-tag policy JSON](https://github.com/Azure/azure-policy/blob/master/built-in-policies/policyDefinitions/Backup/FileShare_EnableAzureBackupWithoutTag_DINE.json)

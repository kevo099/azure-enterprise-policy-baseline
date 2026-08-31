# Replicate the Azure Policy + Automation showcase

This runbook creates the same low-cost, retained demonstration shape used to
qualify the two public projects together: one resource-group-scoped Policy
assignment, a compliant infrastructure fixture, an intentionally unprotected
empty file share, and an Azure Automation runbook that changes one empty
Recovery Services vault policy and then proves idempotence.

The result is meant to be left alive for portal inspection. It contains no VM,
public IP, SQL database, protected backup item, customer data, or Automation
schedule. Azure control-plane objects are generally inexpensive, but Storage,
Key Vault, Log Analytics, Automation, and Backup operations remain billable.
Check current regional pricing and your own budget before deployment.

This is a canary procedure, not authorization to assign the initiative across
an existing subscription. Keep the Policy assignment, custom-role assignments,
and Automation write window at the new resource group.

## What this creates

```text
Azure subscription
├── 16 custom Policy definitions + enterprise-baseline initiative
└── one new showcase resource group
    ├── enterprise-baseline assignment (system-assigned identity)
    │   ├── Contributor + Monitoring Contributor at this RG
    │   └── Log Analytics Contributor at this RG's workspace
    ├── Log Analytics workspace (30-day retention, 0.1 GB/day cap)
    ├── secure StorageV2 account + empty, intentionally unprotected SMB share
    ├── purge-protected Key Vault + policy-deployed diagnostics
    ├── VNet + TCP/443-only inbound NSG + private-only NIC
    └── Automation Account (separate system-assigned identity)
        ├── Published PowerShell 7.4 runbook
        ├── two empty Recovery Services vaults and two empty canary policies
        ├── retained RG-scoped discovery reader
        └── temporary RG-scoped policy writer, removed after qualification
```

The Policy identity and Automation identity are different principals. Never
reuse one principal ID for both role sets.

## Qualification boundary and source pins

The last joint live qualification used these public sources:

| Component | Reviewed source |
|---|---|
| Policy definitions, initiative, and lifecycle scripts | `kevo099/azure-enterprise-policy-baseline` commit `d07fe194b46a4b1df9f20e04f6454dc1f3f81148` |
| Automation runbook and fixture used in the live qualification | `kevo099/azure-backup-smart-tiering-automation` commit `67ecfe0f5a3bebbac472fc629aa1911846180056` |
| Propagation-safe replication helpers used by this guide | `kevo099/azure-backup-smart-tiering-automation` commit `1abbdcc066d58d9fb765d78fff3763ee34acf97a` |
| Published Automation runbook | SHA-256 `2cef45acc81b04a6bbcd62582db6f974102ae98f2de79231a90907f49a7dd555` |

The Policy showcase Bicep in this guide was sanitized from the live fixture. It
does not change the qualified Policy JSON or deployment scripts. If either
repository has advanced, review its diff, record the full commit SHA, and run
all offline checks before Azure writes.

This workflow reproduces Policy evaluation, Modify remediation, Key Vault
`DeployIfNotExists`, audit signals, Automation audit/apply/idempotence, and
least-privilege cleanup. Empty canary policies prove the policy-control path;
they do **not** prove archive movement or restores of real backup data. The
unprotected share proves existence detection only, not backup health. Deny
outcomes come from the historical sanitized live-test matrix; this safer
replication path does not ship or execute the negative probe templates.

## Privacy rules

Do not commit, attach to a public issue, or paste into a pull request:

- tenant/subscription IDs, principal IDs, role-assignment IDs, ARM resource IDs;
- real resource names, Automation job IDs, request/correlation IDs;
- Policy state, RBAC, deployment, What-If, Activity Log, or job-stream exports;
- workspace/customer IDs, portal deep links, screenshots, HAR files, or spend;
- passwords, client secrets, tokens, certificates, storage keys, SAS values, or
  connection strings.

The commands below derive IDs locally. Rendered parameters and before/after
JSON go into a `mktemp` directory, never either Git worktree. Do not run with
`set -x` or Azure CLI `--debug`. Public evidence should contain only source
commits/hashes, counts, semantic outcomes, and stated limitations.

## 1. Prerequisites

You need Bash 4 or newer, Git, `curl`, `jq`, GNU coreutils (`sha256sum` and
`sort -V`), Azure CLI 2.75.0 or newer with Bicep, Python 3, and optionally
PowerShell 7.4 for the Automation behavioral harness. The procedure pins the
experimental `automation` extension to the qualified `1.0.0b2` command
surface.

The operator needs:

- permission to register the listed Azure providers, or a platform owner must
  register them first;
- Resource Policy Contributor-equivalent rights at the subscription for the
  custom definitions and initiative;
- Contributor-equivalent resource rights at the new resource group; and
- `Microsoft.Authorization/roleAssignments/write` and
  `Microsoft.Authorization/roleDefinitions/write` at only the new resource
  group for the two managed identities' roles.

Using subscription Owner is simpler but broader than this canary requires.
Use temporary elevation and remove it after the role assignments exist.

Run every shell block in this document in **one Bash session**. Start fail-fast
and make every locally rendered file private, then clone both repositories into
a disposable working directory:

```bash
set -euo pipefail
umask 077

WORK_ROOT="$(mktemp -d)"
POLICY_DIR="$WORK_ROOT/azure-enterprise-policy-baseline"
AUTOMATION_DIR="$WORK_ROOT/azure-backup-smart-tiering-automation"
QUALIFIED_POLICY_CORE="d07fe194b46a4b1df9f20e04f6454dc1f3f81148"
AUTOMATION_REF="1abbdcc066d58d9fb765d78fff3763ee34acf97a"
EXPECTED_RUNBOOK_SHA="2cef45acc81b04a6bbcd62582db6f974102ae98f2de79231a90907f49a7dd555"
EXPECTED_POLICY_FIXTURE_SHA="ee6a382443881993fea49ef25f9c6c89ecaff38addb1faa04180b22fc5f0eaad"

git clone https://github.com/kevo099/azure-enterprise-policy-baseline.git "$POLICY_DIR"
git clone https://github.com/kevo099/azure-backup-smart-tiering-automation.git "$AUTOMATION_DIR"

POLICY_REF="$(git -C "$POLICY_DIR" rev-parse HEAD)"
git -C "$AUTOMATION_DIR" checkout --detach "$AUTOMATION_REF"
test "$(git -C "$AUTOMATION_DIR" rev-parse HEAD)" = "$AUTOMATION_REF"

# The guide/fixture may postdate the live qualification, but the deployable
# Policy core must still equal the qualified commit unless separately reviewed.
git -C "$POLICY_DIR" diff --exit-code "$QUALIFIED_POLICY_CORE" -- \
  policies initiatives scripts/deploy.sh scripts/assign.sh \
  scripts/unassign.sh scripts/undeploy.sh
ACTUAL_POLICY_FIXTURE_SHA="$(sha256sum "$POLICY_DIR/infra/policy-showcase.bicep" | cut -d' ' -f1)"
test "$ACTUAL_POLICY_FIXTURE_SHA" = "$EXPECTED_POLICY_FIXTURE_SHA"
printf 'Policy guide ref: %s\nAutomation ref: %s\n' "$POLICY_REF" "$AUTOMATION_REF"
```

Validate before signing in to Azure:

```bash
cd "$POLICY_DIR"
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
bash -n scripts/*.sh
jq empty examples/*.json
az bicep build --file infra/policy-showcase.bicep --stdout >/dev/null

cd "$AUTOMATION_DIR"
pwsh -NonInteractive -NoProfile -File tests/StaticValidation.ps1
pwsh -NonInteractive -NoProfile -File tests/BehaviorHarness.ps1
bash -n scripts/*.sh
az bicep build --file infra/test-environment.bicep --stdout >/dev/null
ACTUAL_RUNBOOK_SHA="$(sha256sum src/Enable-SmartTiering.ps1 | cut -d' ' -f1)"
test "$ACTUAL_RUNBOOK_SHA" = "$EXPECTED_RUNBOOK_SHA"
```

If PowerShell is unavailable, record that the behavioral harness was not run;
do not silently call the validation complete.

## 2. Sign in and choose fresh names

Use interactive or federated Azure CLI authentication. No credential belongs
in these repositories.

```bash
az login
AZ_CLI_VERSION="$(az version --query '"azure-cli"' -o tsv)"
test "$(printf '%s\n' 2.75.0 "$AZ_CLI_VERSION" | sort -V | head -1)" = "2.75.0"
az extension add --name automation --version 1.0.0b2 --upgrade --yes
test "$(az extension show --name automation --query version -o tsv)" = "1.0.0b2"
SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
az account set --subscription "$SUBSCRIPTION_ID"

PRIMARY_LOCATION="eastus2"
AUTOMATION_LOCATION="centralus"
DEPLOYMENT_SUFFIX="$(printf '%08x' "$(((RANDOM << 16) | RANDOM))")"

RESOURCE_GROUP="rg-policy-automation-$DEPLOYMENT_SUFFIX"
WORKSPACE_NAME="law-showcase-$DEPLOYMENT_SUFFIX"
STORAGE_ACCOUNT_NAME="stshowcase$DEPLOYMENT_SUFFIX"
KEY_VAULT_NAME="kv-showcase-$DEPLOYMENT_SUFFIX"
VIRTUAL_NETWORK_NAME="vnet-policy-$DEPLOYMENT_SUFFIX"
NETWORK_SECURITY_GROUP_NAME="nsg-policy-$DEPLOYMENT_SUFFIX"
NETWORK_INTERFACE_NAME="nic-policy-$DEPLOYMENT_SUFFIX"
POLICY_ASSIGNMENT="enterprise-baseline-showcase"

AUTOMATION_ACCOUNT="aa-showcase-$DEPLOYMENT_SUFFIX"
RG_VAULT="rsv-rg-showcase-$DEPLOYMENT_SUFFIX"
SUBSCRIPTION_VAULT="rsv-sub-showcase-$DEPLOYMENT_SUFFIX"
BACKUP_POLICY="smart-tiering-showcase-canary"

POLICY_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
PRIVATE_WORK="$(mktemp -d)"
```

`AUTOMATION_LOCATION` must be allowed by Policy before the Automation fixture
is created. If Automation Account quota forces a different region, change the
variable and regenerate/reapply the Policy parameters before enforcement.

Register providers. This changes subscription provider state but creates no
workload resource:

```bash
for provider in \
  Microsoft.Authorization \
  Microsoft.PolicyInsights \
  Microsoft.OperationalInsights \
  Microsoft.Insights \
  Microsoft.Storage \
  Microsoft.Network \
  Microsoft.KeyVault \
  Microsoft.Automation \
  Microsoft.RecoveryServices
do
  az provider register --namespace "$provider" --wait --only-show-errors
done
```

## 3. Create the owned scope and capped workspace

The resource-group tag is the source used by the initiative's inheritance
policy. Keep the workspace in this RG so every remediation grant remains
RG-scoped.

```bash
test "$(az group exists --subscription "$SUBSCRIPTION_ID" --name "$RESOURCE_GROUP")" = "false"

az group create \
  --subscription "$SUBSCRIPTION_ID" \
  --name "$RESOURCE_GROUP" \
  --location "$PRIMARY_LOCATION" \
  --tags CostCenter=PolicyAutomationDemo Purpose=AzurePolicyAutomationShowcase \
  --output none

test "$(az group show \
  --subscription "$SUBSCRIPTION_ID" \
  --name "$RESOURCE_GROUP" \
  --query id -o tsv | tr '[:upper:]' '[:lower:]')" = \
  "$(printf '%s' "$POLICY_SCOPE" | tr '[:upper:]' '[:lower:]')"

az monitor log-analytics workspace create \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "$WORKSPACE_NAME" \
  --location "$PRIMARY_LOCATION" \
  --sku PerGB2018 \
  --tags Purpose=AzurePolicyAutomationShowcase Lifecycle=RetainForInspection \
  --output none

az monitor log-analytics workspace update \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "$WORKSPACE_NAME" \
  --retention-time 30 \
  --quota 0.1 \
  --output none

WORKSPACE_ID="$(az monitor log-analytics workspace show \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "$WORKSPACE_NAME" \
  --query id -o tsv)"
```

## 4. Publish and assign the Policy baseline in report-only mode

Definitions and the initiative live at subscription scope; the assignment is
limited to the new RG. This replication path is deliberately **fresh only**:
it refuses to overwrite any same-named subscription artifact. If the baseline
already exists, stop and perform a separately reviewed adoption/diff exercise.

```bash
cd "$POLICY_DIR"

POLICY_DEFINITION_COUNT="$(find policies -type f -name '*.json' -exec jq -er '.name' {} \; | wc -l)"
test "$POLICY_DEFINITION_COUNT" -eq 16
while IFS= read -r policy_file; do
  definition_name="$(jq -er '.name' "$policy_file")"
  if az policy definition show \
    --subscription "$SUBSCRIPTION_ID" \
    --name "$definition_name" \
    --only-show-errors \
    --output none 2>"$PRIVATE_WORK/policy-definition-probe.err"; then
    printf 'Policy definition collision; stop before deployment\n' >&2
    exit 1
  else
    definition_probe_status=$?
    if [ "$definition_probe_status" -ne 3 ]; then
      printf 'Could not prove Policy definition absence; stop before deployment\n' >&2
      exit 1
    fi
  fi
done < <(find policies -type f -name '*.json' | sort)

if az policy set-definition show \
  --subscription "$SUBSCRIPTION_ID" \
  --name enterprise-baseline \
  --only-show-errors \
  --output none 2>"$PRIVATE_WORK/policy-initiative-probe.err"; then
  printf 'Policy initiative collision; stop before deployment\n' >&2
  exit 1
else
  initiative_probe_status=$?
  if [ "$initiative_probe_status" -ne 3 ]; then
    printf 'Could not prove Policy initiative absence; stop before deployment\n' >&2
    exit 1
  fi
fi

./scripts/deploy.sh --subscription "$SUBSCRIPTION_ID"

POLICY_PARAMS="$PRIVATE_WORK/assignment-params.json"
jq \
  --arg workspace "$WORKSPACE_ID" \
  --arg primary "$PRIMARY_LOCATION" \
  --arg automation "$AUTOMATION_LOCATION" \
  '.logAnalyticsWorkspaceId.value = $workspace
   | .allowedLocations.value = ([$primary, $automation] | unique)
   | .requiredTagName.value = "CostCenter"' \
  examples/assignment-params.example.json >"$POLICY_PARAMS"

./scripts/assign.sh \
  --params "$POLICY_PARAMS" \
  --scope "$POLICY_SCOPE" \
  --definition-scope "/subscriptions/$SUBSCRIPTION_ID" \
  --name "$POLICY_ASSIGNMENT" \
  --location "$PRIMARY_LOCATION" \
  --dry-run
```

`--dry-run` still creates a real assignment, system-assigned identity, and
three remediation-role assignments. It changes only enforcement mode to
`DoNotEnforce`; it is not a local preview. Keep its raw output private because
it includes scope and principal identifiers.

Deploy the sanitized Policy fixture while the assignment is report-only:

```bash
az deployment group create \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "policy-showcase-$DEPLOYMENT_SUFFIX" \
  --template-file infra/policy-showcase.bicep \
  --parameters \
    location="$PRIMARY_LOCATION" \
    storageAccountName="$STORAGE_ACCOUNT_NAME" \
    keyVaultName="$KEY_VAULT_NAME" \
    virtualNetworkName="$VIRTUAL_NETWORK_NAME" \
    networkSecurityGroupName="$NETWORK_SECURITY_GROUP_NAME" \
    networkInterfaceName="$NETWORK_INTERFACE_NAME" \
    retainForInspection=true \
  --output none
```

This template intentionally omits `CostCenter` from resource tags, omits the
Key Vault diagnostic setting, and leaves one empty SMB share unprotected. Its
storage account and Key Vault disable public network access; the NIC has no
public IP; the NSG allows inbound 443 only.

Trigger evaluation, then wait for Policy Insights to return nonempty state:

```bash
wait_for_report_only_policy_state() {
  local state_file="$PRIVATE_WORK/policy-report-only.json"
  for _ in $(seq 1 40); do
    az policy state list \
      --subscription "$SUBSCRIPTION_ID" \
      --resource-group "$RESOURCE_GROUP" \
      --policy-assignment "$POLICY_ASSIGNMENT" \
      --output json >"$state_file"
    if jq -e '
      length > 0 and
      any(.[]; .policyDefinitionReferenceId == "inherit-tag-from-resource-group" and .complianceState == "NonCompliant") and
      any(.[]; .policyDefinitionReferenceId == "deploy-keyvault-diagnostics" and .complianceState == "NonCompliant") and
      any(.[]; .policyDefinitionReferenceId == "audit-file-share-backup-protection" and .complianceState == "NonCompliant")
    ' "$state_file" >/dev/null; then
      return 0
    fi
    sleep 30
  done
  printf 'Policy report-only signals did not converge\n' >&2
  return 1
}

az policy state trigger-scan \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --no-wait
wait_for_report_only_policy_state
```

Evaluation commonly takes several minutes. Zero state is **inconclusive**, not
compliant. The bounded poll refuses promotion until state is nonempty and the
three intended report-only signals are present: missing inherited tags,
missing Key Vault diagnostics, and the unprotected share.

## 5. Promote, remediate, and independently verify Policy

Reapply the same assignment without `--dry-run`:

```bash
cd "$POLICY_DIR"
./scripts/assign.sh \
  --params "$POLICY_PARAMS" \
  --scope "$POLICY_SCOPE" \
  --definition-scope "/subscriptions/$SUBSCRIPTION_ID" \
  --name "$POLICY_ASSIGNMENT" \
  --location "$PRIMARY_LOCATION"

test "$(az policy assignment show \
  --subscription "$SUBSCRIPTION_ID" \
  --name "$POLICY_ASSIGNMENT" \
  --scope "$POLICY_SCOPE" \
  --query enforcementMode -o tsv)" = "Default"
```

The script owns the assignment display name and does not preserve a custom
description on an upsert. Apply custom portal metadata only after the final
scripted assignment, or use a separately reviewed metadata update.

Remediate the two effects that can repair existing resources, then require
terminal success, at least one deployment, and zero failed deployments:

```bash
TAG_REMEDIATION="inherit-tags-$DEPLOYMENT_SUFFIX"
DIAGNOSTIC_REMEDIATION="keyvault-diagnostics-$DEPLOYMENT_SUFFIX"

az policy remediation create \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$TAG_REMEDIATION" \
  --policy-assignment "$POLICY_ASSIGNMENT" \
  --definition-reference-id inherit-tag-from-resource-group \
  --resource-discovery-mode ReEvaluateCompliance \
  --output none

az policy remediation create \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DIAGNOSTIC_REMEDIATION" \
  --policy-assignment "$POLICY_ASSIGNMENT" \
  --definition-reference-id deploy-keyvault-diagnostics \
  --resource-discovery-mode ReEvaluateCompliance \
  --output none

wait_for_remediation() {
  local name="$1"
  local result_file="$PRIVATE_WORK/remediation-$name.json"
  local state=""
  local total=0
  local succeeded=0
  local failed=0
  for _ in $(seq 1 120); do
    az policy remediation show \
      --subscription "$SUBSCRIPTION_ID" \
      --resource-group "$RESOURCE_GROUP" \
      --name "$name" \
      --output json >"$result_file"
    state="$(jq -r '.provisioningState // empty' "$result_file")"
    case "$state" in
      Succeeded)
        total="$(jq -r '.deploymentStatus.totalDeployments // 0' "$result_file")"
        succeeded="$(jq -r '.deploymentStatus.successfulDeployments // 0' "$result_file")"
        failed="$(jq -r '.deploymentStatus.failedDeployments // 0' "$result_file")"
        test "$total" -gt 0
        test "$failed" -eq 0
        test "$succeeded" -eq "$total"
        return 0
        ;;
      Failed|Canceled|Cancelled)
        printf 'Remediation %s ended %s\n' "$name" "$state" >&2
        return 1
        ;;
    esac
    sleep 15
  done
  printf 'Timed out waiting for remediation %s\n' "$name" >&2
  return 1
}

wait_for_remediation "$TAG_REMEDIATION"
wait_for_remediation "$DIAGNOSTIC_REMEDIATION"
```

Trigger another scoped scan, poll for the exact final Policy signals, and then
verify resource state independently of Policy's summary:

```bash
wait_for_final_policy_state() {
  local state_file="$PRIVATE_WORK/policy-final.json"
  for _ in $(seq 1 40); do
    az policy state list \
      --subscription "$SUBSCRIPTION_ID" \
      --resource-group "$RESOURCE_GROUP" \
      --policy-assignment "$POLICY_ASSIGNMENT" \
      --output json >"$state_file"
    if jq -e '
      ([.[] | select(.policyDefinitionReferenceId == "inherit-tag-from-resource-group")] | length) > 0 and
      ([.[] | select(.policyDefinitionReferenceId == "inherit-tag-from-resource-group" and .complianceState != "Compliant")] | length) == 0 and
      ([.[] | select(.policyDefinitionReferenceId == "deploy-keyvault-diagnostics" and .complianceState == "Compliant")] | length) == 1 and
      ([.[] | select(.policyDefinitionReferenceId == "audit-file-share-backup-protection" and .complianceState == "NonCompliant")] | length) == 1
    ' "$state_file" >/dev/null; then
      return 0
    fi
    sleep 30
  done
  printf 'Final Policy state did not converge\n' >&2
  return 1
}

az policy state trigger-scan \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --no-wait
wait_for_final_policy_state

assert_cost_center() {
  local type="$1"
  local name="$2"
  test "$(az resource show \
    --subscription "$SUBSCRIPTION_ID" \
    --resource-group "$RESOURCE_GROUP" \
    --resource-type "$type" \
    --name "$name" \
    --query 'tags.CostCenter' -o tsv)" = "PolicyAutomationDemo"
}

assert_cost_center Microsoft.OperationalInsights/workspaces "$WORKSPACE_NAME"
assert_cost_center Microsoft.Storage/storageAccounts "$STORAGE_ACCOUNT_NAME"
assert_cost_center Microsoft.KeyVault/vaults "$KEY_VAULT_NAME"
assert_cost_center Microsoft.Network/virtualNetworks "$VIRTUAL_NETWORK_NAME"
assert_cost_center Microsoft.Network/networkSecurityGroups "$NETWORK_SECURITY_GROUP_NAME"
assert_cost_center Microsoft.Network/networkInterfaces "$NETWORK_INTERFACE_NAME"

KEY_VAULT_ID="$(az keyvault show \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$KEY_VAULT_NAME" \
  --query id -o tsv)"

az monitor diagnostic-settings list \
  --resource "$KEY_VAULT_ID" \
  --output json >"$PRIVATE_WORK/keyvault-diagnostics.json"

jq -e --arg workspace "$WORKSPACE_ID" '
  [.[] | select(
    .name == "enterprise-baseline-diagnostics" and
    ((.workspaceId | ascii_downcase) == ($workspace | ascii_downcase)) and
    any(.logs[]; .categoryGroup == "audit" and .enabled == true) and
    any(.metrics[]; .category == "AllMetrics" and .enabled == true)
  )] | length == 1
' "$PRIVATE_WORK/keyvault-diagnostics.json" >/dev/null
```

The assertions require all six exact retained Policy resources to carry the
inherited tag, the diagnostic setting to target the exact workspace with audit
logs and all metrics enabled, and exactly one intentional file-share finding.

The original qualification also used validation-only negative requests for
the Deny definitions. Those easily mis-deployed templates are intentionally
not published here: an operator typo could create an insecure storage account,
public IP, open management rule, SQL server, or legacy VM. The sanitized live
records document the Deny outcomes; use your organization's normal Policy
test framework if you need to repeat them.

## 6. Deploy the fresh Automation canary once

The Automation repository's Bicep is safe for a **new** scope. Do not use it as
an update mechanism against a long-lived vault because Azure service defaults
can produce unrelated What-If drift.

Use a current UTC schedule timestamp and deploy both empty policies in their
safe `TierRecommended` state. After reader RBAC, seed only the exact RG policy
with a sanitized full-document PUT; never re-run the whole retained fixture to
create a mutation canary.

```bash
SCHEDULE_TIME="$(python3 -c 'from datetime import datetime, timedelta, timezone; d=(datetime.now(timezone.utc)+timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0); print(d.strftime("%Y-%m-%dT%H:%M:%SZ"))')"

cd "$AUTOMATION_DIR"
az deployment group create \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "automation-showcase-$DEPLOYMENT_SUFFIX" \
  --template-file infra/test-environment.bicep \
  --parameters \
    location="$AUTOMATION_LOCATION" \
    resourceGroupScopeVaultName="$RG_VAULT" \
    subscriptionScopeVaultName="$SUBSCRIPTION_VAULT" \
    automationAccountName="$AUTOMATION_ACCOUNT" \
    backupPolicyName="$BACKUP_POLICY" \
    testPolicyTieringMode=TierRecommended \
    retainForInspection=true \
    scheduleRunTime="$SCHEDULE_TIME" \
  --output none

AUTOMATION_PRINCIPAL="$(az automation account show \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$AUTOMATION_ACCOUNT" \
  --query identity.principalId -o tsv)"
test -n "$AUTOMATION_PRINCIPAL"
```

Publish and fetch back the runbook. The helper discovers the Automation
Account's actual region unless `LOCATION` is explicitly supplied:

```bash
cd "$AUTOMATION_DIR"
SUBSCRIPTION_ID="$SUBSCRIPTION_ID" \
RESOURCE_GROUP="$RESOURCE_GROUP" \
AUTOMATION_ACCOUNT="$AUTOMATION_ACCOUNT" \
LOCATION="$AUTOMATION_LOCATION" \
scripts/publish-runbook.sh
```

Require `Published`, `PowerShell74`, and equal local/remote SHA-256 output.
The explicit location is also compatible with the earlier qualified helper;
the pinned helper independently discovers and verifies the account region.

An optional audit job before reader RBAC should fail before the runbook's
result phase. Depending on identity visibility, that can be token/context
initialization or the first vault-list authorization. **No `SUMMARY` is
expected**, and the policy must remain `TierRecommended`. This is safe but not needed
for the positive qualification.

Define the exact ARM surfaces, then grant the Automation identity only the
RG-scoped discovery reader:

```bash
ARM="https://management.azure.com"
AUTOMATION_BASE="$ARM/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Automation/automationAccounts/$AUTOMATION_ACCOUNT"
POLICY_URL="$ARM/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.RecoveryServices/vaults/$RG_VAULT/backupPolicies/$BACKUP_POLICY?api-version=2025-08-01"
SUBSCRIPTION_POLICY_URL="$ARM/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.RecoveryServices/vaults/$SUBSCRIPTION_VAULT/backupPolicies/$BACKUP_POLICY?api-version=2025-08-01"

scripts/discovery-role.sh grant \
  "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$AUTOMATION_PRINCIPAL"
```

Do not guess when RBAC has propagated. Run a bounded, read-only exact-name
audit against the still-compliant `TierRecommended` policy until the managed
identity proves it can perform the actual discovery path. A failed read-only
job is safe to retry; an unexpected Completed result is not:

```bash
READER_READY=false
READER_DEADLINE="$(( $(date +%s) + 900 ))"
while [ "$(date +%s)" -lt "$READER_DEADLINE" ]; do
  READER_JOB="$(az automation runbook start \
    --subscription "$SUBSCRIPTION_ID" \
    --resource-group "$RESOURCE_GROUP" \
    --automation-account-name "$AUTOMATION_ACCOUNT" \
    --name Enable-SmartTiering \
    --parameters \
      SubscriptionId="$SUBSCRIPTION_ID" \
      ScopeType=ResourceGroup \
      ResourceGroupName="$RESOURCE_GROUP" \
      VaultName="$RG_VAULT" \
      PolicyName="$BACKUP_POLICY" \
      Apply=false \
      ExpectedMatches=1 \
    --query name -o tsv)"

  READER_STATUS=""
  while [ "$(date +%s)" -lt "$READER_DEADLINE" ]; do
    READER_STATUS="$(az rest --method get \
      --url "$AUTOMATION_BASE/jobs/$READER_JOB?api-version=2024-10-23" \
      --query properties.status -o tsv)"
    case "$READER_STATUS" in
      Completed|Failed|Stopped|Suspended) break ;;
    esac
    sleep 5
  done

  if [ "$READER_STATUS" = "Completed" ]; then
    az rest --method get \
      --url "$AUTOMATION_BASE/jobs/$READER_JOB/output?api-version=2024-10-23" \
      --output tsv >"$PRIVATE_WORK/reader-readiness-output.txt"
    test "$(grep -c '^SUMMARY ' "$PRIVATE_WORK/reader-readiness-output.txt")" -eq 1
    test "$(grep -c '"action":"AlreadyCompliant"' \
      "$PRIVATE_WORK/reader-readiness-output.txt")" -eq 1
    sed -n 's/^SUMMARY //p' "$PRIVATE_WORK/reader-readiness-output.txt" | jq -e '
      .policiesMatched == 1 and
      .candidates == 0 and
      .writesSubmitted == 0 and
      .policiesWritten == 0 and
      .writesFailed == 0 and
      .writesUnknown == 0 and
      .writesSkipped == 0 and
      .errors == 0 and
      .abortReason == null
    ' >/dev/null
    READER_READY=true
    break
  fi
  case "$READER_STATUS" in
    Failed) sleep 10 ;;
    *) printf 'Reader readiness job did not reach a retryable state\n' >&2; exit 1 ;;
  esac
done
test "$READER_READY" = true
```

Now fetch the exact empty RG policy, require its safe starting state and zero
protected items, and construct a PUT payload without read-only response
members. This operator seed changes only `ArchivedRP` to `DoNotTier`; it does
not grant the Automation identity writer access and does not touch the second
vault.

```bash
az rest --method get --url "$POLICY_URL" >"$PRIVATE_WORK/policy-before.json"
jq -e '
  .properties.protectedItemsCount == 0 and
  .properties.tieringPolicy.ArchivedRP.tieringMode == "TierRecommended"
' "$PRIVATE_WORK/policy-before.json" >/dev/null

jq '
  del(.id, .name, .type, .systemData, .eTag)
  | .properties |= del(.protectedItemsCount, .resourceGuardOperationRequests)
  | .properties.tieringPolicy.ArchivedRP |= (
      del(.tieringMode, .duration, .durationType)
      + {tieringMode: "DoNotTier"}
    )
' "$PRIVATE_WORK/policy-before.json" >"$PRIVATE_WORK/policy-seed.json"

POLICY_ETAG="$(jq -r '.eTag // empty' "$PRIVATE_WORK/policy-before.json")"
SEED_TOKEN="$(az account get-access-token --resource "$ARM/" \
  --query accessToken -o tsv)"
SEED_CURL=(
  --silent --show-error
  --proto '=https'
  --request PUT
  --url "$POLICY_URL"
  --header "Content-Type: application/json"
  --data-binary "@$PRIVATE_WORK/policy-seed.json"
  --dump-header "$PRIVATE_WORK/seed-headers.txt"
  --output "$PRIVATE_WORK/seed-response.json"
  --write-out '%{http_code}'
)
if [ -n "$POLICY_ETAG" ]; then
  SEED_CURL+=(--header "If-Match: $POLICY_ETAG")
fi
SEED_HTTP_STATUS="$(
  printf 'header = "Authorization: Bearer %s"\n' "$SEED_TOKEN" \
    | curl --config - "${SEED_CURL[@]}"
)"

case "$SEED_HTTP_STATUS" in
  200)
    ;;
  202)
    SEED_OPERATION_URL="$(awk '
      tolower($1) == "azure-asyncoperation:" {
        sub(/^[^:]+:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit
      }
    ' "$PRIVATE_WORK/seed-headers.txt")"
    SEED_LOCATION_URL="$(awk '
      tolower($1) == "location:" {
        sub(/^[^:]+:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit
      }
    ' "$PRIVATE_WORK/seed-headers.txt")"
    test -n "$SEED_OPERATION_URL" || test -n "$SEED_LOCATION_URL"
    if [ -n "$SEED_OPERATION_URL" ]; then
      case "$SEED_OPERATION_URL" in
        "$ARM"/*) ;;
        *) printf 'Untrusted policy-operation URL\n' >&2; exit 1 ;;
      esac
    fi
    if [ -n "$SEED_LOCATION_URL" ]; then
      case "$SEED_LOCATION_URL" in
        "$ARM"/*) ;;
        *) printf 'Untrusted policy-result URL\n' >&2; exit 1 ;;
      esac
    fi
    SEED_RETRY_AFTER="$(awk '
      tolower($1) == "retry-after:" {gsub(/\r/, "", $2); print $2; exit}
    ' "$PRIVATE_WORK/seed-headers.txt")"
    case "$SEED_RETRY_AFTER" in
      ''|*[!0-9]*) SEED_RETRY_AFTER=5 ;;
    esac
    if [ "$SEED_RETRY_AFTER" -gt 60 ]; then
      SEED_RETRY_AFTER=60
    fi
    SEED_DEADLINE="$(( $(date +%s) + 900 ))"
    if [ -n "$SEED_OPERATION_URL" ]; then
      SEED_OPERATION_STATUS=""
      while [ "$(date +%s)" -lt "$SEED_DEADLINE" ]; do
        sleep "$SEED_RETRY_AFTER"
        az rest --method get --url "$SEED_OPERATION_URL" \
          >"$PRIVATE_WORK/seed-operation.json"
        SEED_OPERATION_STATUS="$(jq -r '.status // .properties.status // empty' \
          "$PRIVATE_WORK/seed-operation.json")"
        case "${SEED_OPERATION_STATUS,,}" in
          succeeded) break ;;
          failed|canceled|cancelled)
            printf 'Exact policy seed operation failed\n' >&2
            exit 1
            ;;
        esac
      done
      test "${SEED_OPERATION_STATUS,,}" = "succeeded"
    else
      SEED_LOCATION_STATUS=""
      SEED_LOCATION_CURL=(
        --silent --show-error
        --proto '=https'
        --request GET
        --url "$SEED_LOCATION_URL"
        --output "$PRIVATE_WORK/seed-location.json"
        --write-out '%{http_code}'
      )
      while [ "$(date +%s)" -lt "$SEED_DEADLINE" ]; do
        sleep "$SEED_RETRY_AFTER"
        SEED_LOCATION_STATUS="$(
          printf 'header = "Authorization: Bearer %s"\n' "$SEED_TOKEN" \
            | curl --config - "${SEED_LOCATION_CURL[@]}"
        )"
        case "$SEED_LOCATION_STATUS" in
          200|201|204) break ;;
          202) ;;
          *)
            printf 'Exact policy seed result poll returned HTTP %s\n' \
              "$SEED_LOCATION_STATUS" >&2
            exit 1
            ;;
        esac
      done
      case "$SEED_LOCATION_STATUS" in
        200|201|204) ;;
        *) exit 1 ;;
      esac
      unset SEED_LOCATION_CURL
    fi
    ;;
  *)
    printf 'Exact policy seed returned HTTP %s\n' "$SEED_HTTP_STATUS" >&2
    exit 1
    ;;
esac
unset SEED_TOKEN SEED_CURL

wait_for_seeded_policy() {
  for _ in $(seq 1 60); do
    az rest --method get --url "$POLICY_URL" >"$PRIVATE_WORK/policy-seeded.json"
    if jq -e '
      .properties.protectedItemsCount == 0 and
      .properties.tieringPolicy.ArchivedRP.tieringMode == "DoNotTier"
    ' "$PRIVATE_WORK/policy-seeded.json" >/dev/null; then
      return 0
    fi
    sleep 5
  done
  printf 'Timed out waiting for the exact policy seed\n' >&2
  return 1
}
wait_for_seeded_policy
test "$(az rest --method get --url "$SUBSCRIPTION_POLICY_URL" \
  --query properties.tieringPolicy.ArchivedRP.tieringMode -o tsv)" = "TierRecommended"
```

## 7. Run audit, bounded apply, and idempotence

The helpers below keep job output local. Raw output contains tenant-specific
resource and job identifiers; never publish it.

```bash
wait_for_automation_job() {
  local job_name="$1"
  local expected_status="$2"
  local deadline="${3:-$(( $(date +%s) + 450 ))}"
  local status=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    status="$(az rest --method get \
      --url "$AUTOMATION_BASE/jobs/$job_name?api-version=2024-10-23" \
      --query properties.status -o tsv)"
    case "$status" in
      Completed|Failed|Stopped|Suspended)
        if [ "$status" != "$expected_status" ]; then
          printf 'Job %s ended %s; expected %s\n' "$job_name" "$status" "$expected_status" >&2
          return 1
        fi
        return 0
        ;;
    esac
    sleep 5
  done
  printf 'Timed out waiting for the Automation job\n' >&2
  return 1
}

automation_job_output() {
  az rest --method get \
    --url "$AUTOMATION_BASE/jobs/$1/output?api-version=2024-10-23" \
    --output tsv
}

require_job_result() {
  local output_file="$1"
  local expected_action="$2"
  local candidates="$3"
  local submitted="$4"
  local written="$5"
  local summary=""
  test "$(grep -c "\"action\":\"$expected_action\"" "$output_file")" -eq 1
  test "$(grep -c '^SUMMARY ' "$output_file")" -eq 1
  summary="$(sed -n 's/^SUMMARY //p' "$output_file")"
  test -n "$summary"
  printf '%s\n' "$summary" | jq -e \
    --argjson candidates "$candidates" \
    --argjson submitted "$submitted" \
    --argjson written "$written" '
      .policiesMatched == 1 and
      .candidates == $candidates and
      .writesSubmitted == $submitted and
      .policiesWritten == $written and
      .writesFailed == 0 and
      .writesUnknown == 0 and
      .writesSkipped == 0 and
      .errors == 0 and
      .abortReason == null
    ' >/dev/null
}

WRITER_GRANTED=false
revoke_writer_on_exit() {
  local original_status=$?
  trap - EXIT
  if [ "$WRITER_GRANTED" = true ]; then
    if ! "$AUTOMATION_DIR/scripts/ring-role.sh" revoke \
      "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$AUTOMATION_PRINCIPAL"; then
      printf 'Automatic writer-role revocation failed; revoke it manually now\n' >&2
      exit 1
    fi
  fi
  exit "$original_status"
}
trap revoke_writer_on_exit EXIT
```

Capture the exact empty policy before mutation, then start an exact-name audit:

```bash
AUDIT_JOB="$(az automation runbook start \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --automation-account-name "$AUTOMATION_ACCOUNT" \
  --name Enable-SmartTiering \
  --parameters \
    SubscriptionId="$SUBSCRIPTION_ID" \
    ScopeType=ResourceGroup \
    ResourceGroupName="$RESOURCE_GROUP" \
    VaultName="$RG_VAULT" \
    PolicyName="$BACKUP_POLICY" \
    Apply=false \
    ExpectedMatches=1 \
  --query name -o tsv)"

wait_for_automation_job "$AUDIT_JOB" Completed
automation_job_output "$AUDIT_JOB" >"$PRIVATE_WORK/audit-output.txt"
require_job_result "$PRIVATE_WORK/audit-output.txt" WouldEnableTierRecommended 1 0 0
```

Expected audit result: Completed, one `WouldEnableTierRecommended`, and
candidate/submitted/written counters `1/0/0` with zero errors.

Grant the temporary writer only for this ring. The helper transactionally
removes artifacts it created if the grant cannot finish; activate the outer
rollback trap immediately after a successful grant. Then retry the intended
exact apply only when the failed job proves the sole failure was temporary
write authorization and a direct GET proves the policy is still unchanged:

```bash
scripts/ring-role.sh grant \
  "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$AUTOMATION_PRINCIPAL"
WRITER_GRANTED=true

APPLY_SUCCEEDED=false
APPLY_DEADLINE="$(( $(date +%s) + 900 ))"
while [ "$(date +%s)" -lt "$APPLY_DEADLINE" ]; do
  APPLY_JOB="$(az automation runbook start \
    --subscription "$SUBSCRIPTION_ID" \
    --resource-group "$RESOURCE_GROUP" \
    --automation-account-name "$AUTOMATION_ACCOUNT" \
    --name Enable-SmartTiering \
    --parameters \
      SubscriptionId="$SUBSCRIPTION_ID" \
      ScopeType=ResourceGroup \
      ResourceGroupName="$RESOURCE_GROUP" \
      VaultName="$RG_VAULT" \
      PolicyName="$BACKUP_POLICY" \
      Apply=true \
      ExpectedMatches=1 \
      MaxChanges=1 \
      MaxProtectedItemsPerPolicy=0 \
    --query name -o tsv)"

  APPLY_STATUS=""
  while [ "$(date +%s)" -lt "$APPLY_DEADLINE" ]; do
    APPLY_STATUS="$(az rest --method get \
      --url "$AUTOMATION_BASE/jobs/$APPLY_JOB?api-version=2024-10-23" \
      --query properties.status -o tsv)"
    case "$APPLY_STATUS" in
      Completed|Failed|Stopped|Suspended) break ;;
    esac
    sleep 5
  done
  automation_job_output "$APPLY_JOB" >"$PRIVATE_WORK/apply-output.txt"

  if [ "$APPLY_STATUS" = "Completed" ]; then
    require_job_result "$PRIVATE_WORK/apply-output.txt" EnabledAndVerified 1 1 1
    APPLY_SUCCEEDED=true
    break
  fi

  test "$APPLY_STATUS" = "Failed"
  test "$(grep -c '^SUMMARY ' "$PRIVATE_WORK/apply-output.txt")" -eq 1
  sed -n 's/^SUMMARY //p' "$PRIVATE_WORK/apply-output.txt" | jq -e '
    .policiesMatched == 1 and
    .candidates == 1 and
    .writesSubmitted == 1 and
    .policiesWritten == 0 and
    .writesFailed == 1 and
    .writesUnknown == 0 and
    .writesSkipped == 0 and
    .errors == 1 and
    .abortReason == null
  ' >/dev/null
  sed -n '/"action":"Error"/p' "$PRIVATE_WORK/apply-output.txt" | jq -se '
    length == 1 and
    .[0].stage == "Write" and
    (.[0].message | test("403|forbidden|authorizationfailed|does not have authorization"; "i"))
  ' >/dev/null
  az rest --method get --url "$POLICY_URL" | jq -e '
    .properties.protectedItemsCount == 0 and
    .properties.tieringPolicy.ArchivedRP.tieringMode == "DoNotTier"
  ' >/dev/null

  RETRY_AUDIT_JOB="$(az automation runbook start \
    --subscription "$SUBSCRIPTION_ID" \
    --resource-group "$RESOURCE_GROUP" \
    --automation-account-name "$AUTOMATION_ACCOUNT" \
    --name Enable-SmartTiering \
    --parameters \
      SubscriptionId="$SUBSCRIPTION_ID" \
      ScopeType=ResourceGroup \
      ResourceGroupName="$RESOURCE_GROUP" \
      VaultName="$RG_VAULT" \
      PolicyName="$BACKUP_POLICY" \
      Apply=false \
      ExpectedMatches=1 \
    --query name -o tsv)"
  wait_for_automation_job "$RETRY_AUDIT_JOB" Completed "$APPLY_DEADLINE"
  automation_job_output "$RETRY_AUDIT_JOB" \
    >"$PRIVATE_WORK/pre-retry-audit-output.txt"
  require_job_result \
    "$PRIVATE_WORK/pre-retry-audit-output.txt" \
    WouldEnableTierRecommended 1 0 0
  sleep 10
done
test "$APPLY_SUCCEEDED" = true
```

Expected apply result: Completed, `EnabledAndVerified`, and counters `1/1/1`
for candidate/submitted/written with zero failed or unknown writes.

Run the same bounded apply again and require idempotence:

```bash
REPEAT_JOB="$(az automation runbook start \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --automation-account-name "$AUTOMATION_ACCOUNT" \
  --name Enable-SmartTiering \
  --parameters \
    SubscriptionId="$SUBSCRIPTION_ID" \
    ScopeType=ResourceGroup \
    ResourceGroupName="$RESOURCE_GROUP" \
    VaultName="$RG_VAULT" \
    PolicyName="$BACKUP_POLICY" \
    Apply=true \
    ExpectedMatches=1 \
    MaxChanges=1 \
    MaxProtectedItemsPerPolicy=0 \
  --query name -o tsv)"

wait_for_automation_job "$REPEAT_JOB" Completed
automation_job_output "$REPEAT_JOB" >"$PRIVATE_WORK/repeat-output.txt"
require_job_result "$PRIVATE_WORK/repeat-output.txt" AlreadyCompliant 0 0 0
```

Require Completed, `AlreadyCompliant`, and `0/0/0` for
candidate/submitted/written. Then revoke writer access immediately:

```bash
scripts/ring-role.sh revoke \
  "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$AUTOMATION_PRINCIPAL"
WRITER_GRANTED=false
```

Do not interpret every failed job as having a summary:

- parameter binding, script-level validation, token/context initialization,
  and top-level vault discovery can fail before `SUMMARY` exists;
- a misspelled exact policy filter reaches `NoPolicyMatched` with zero writes;
- a reader-only apply attempts one PUT, so its summary reports one submitted
  and failed write even though Azure leaves the policy unchanged.

## 8. Prove final invariants

Fetch the changed policy and compare all non-tiering properties. Then assert
both policies, both vaults, the published runbook, schedules, jobs, tags,
Policy enforcement, and final Automation RBAC. These are executable gates,
not an inspection checklist:

```bash
AUTOMATION_RESOURCE_ID="$(az automation account show \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$AUTOMATION_ACCOUNT" \
  --query id -o tsv)"
RG_VAULT_RESOURCE_ID="$(az backup vault show \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$RG_VAULT" \
  --query id -o tsv)"
SUBSCRIPTION_VAULT_RESOURCE_ID="$(az backup vault show \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$SUBSCRIPTION_VAULT" \
  --query id -o tsv)"

# Automation and both vaults were created after the earlier convergence gate.
# Re-evaluate now and require the retained, finished product in Policy state.
az policy state trigger-scan \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --no-wait
wait_for_final_policy_state

jq -e \
  --arg automation "$AUTOMATION_RESOURCE_ID" \
  --arg rgVault "$RG_VAULT_RESOURCE_ID" \
  --arg subscriptionVault "$SUBSCRIPTION_VAULT_RESOURCE_ID" '
    def norm: ascii_downcase | sub("/$"; "");
    . as $states
    | [$automation, $rgVault, $subscriptionVault]
    | all(.[]; . as $id
        | any($states[];
            .policyDefinitionReferenceId == "inherit-tag-from-resource-group" and
            .complianceState == "Compliant" and
            (((.resourceId // "") | norm) == ($id | norm))))
  ' "$PRIVATE_WORK/policy-final.json" >/dev/null

az rest --method get --url "$POLICY_URL" >"$PRIVATE_WORK/policy-after.json"

jq -S '
  .properties
  | del(.protectedItemsCount, .resourceGuardOperationRequests, .tieringPolicy.ArchivedRP)
' "$PRIVATE_WORK/policy-before.json" >"$PRIVATE_WORK/policy-before-nontiering.json"
jq -S '
  .properties
  | del(.protectedItemsCount, .resourceGuardOperationRequests, .tieringPolicy.ArchivedRP)
' "$PRIVATE_WORK/policy-after.json" >"$PRIVATE_WORK/policy-after-nontiering.json"
diff -u \
  "$PRIVATE_WORK/policy-before-nontiering.json" \
  "$PRIVATE_WORK/policy-after-nontiering.json"

for policy_url in "$POLICY_URL" "$SUBSCRIPTION_POLICY_URL"; do
  test "$(az rest --method get --url "$policy_url" \
    --query properties.tieringPolicy.ArchivedRP.tieringMode -o tsv)" = \
    "TierRecommended"
done

for vault in "$RG_VAULT" "$SUBSCRIPTION_VAULT"; do
  test "$(az backup item list \
    --subscription "$SUBSCRIPTION_ID" \
    --resource-group "$RESOURCE_GROUP" \
    --vault-name "$vault" \
    --query 'length(@)' -o tsv)" = "0"
done

RUNBOOK_URL="$AUTOMATION_BASE/runbooks/Enable-SmartTiering"
az rest --method get \
  --url "$RUNBOOK_URL?api-version=2024-10-23" \
  --output json >"$PRIVATE_WORK/runbook-metadata.json"
jq -e '
  .properties.state == "Published" and
  .properties.runtimeEnvironment == "PowerShell74"
' "$PRIVATE_WORK/runbook-metadata.json" >/dev/null

az rest --method get \
  --url "$RUNBOOK_URL/content?api-version=2023-11-01" \
  --output-file "$PRIVATE_WORK/runbook-content.ps1"
REMOTE_RUNBOOK_SHA="$(sha256sum "$PRIVATE_WORK/runbook-content.ps1" | cut -d' ' -f1)"
test "$REMOTE_RUNBOOK_SHA" = "$EXPECTED_RUNBOOK_SHA"

test "$(az rest --method get \
  --url "$AUTOMATION_BASE/schedules?api-version=2024-10-23" \
  --query 'length(value)' -o tsv)" = "0"
test "$(az rest --method get \
  --url "$AUTOMATION_BASE/jobSchedules?api-version=2024-10-23" \
  --query 'length(value)' -o tsv)" = "0"

az rest --method get \
  --url "$AUTOMATION_BASE/jobs?api-version=2024-10-23" \
  --output json >"$PRIVATE_WORK/final-jobs.json"
jq -e '
  [.value[] | select(
    .properties.status != "Completed" and
    .properties.status != "Failed" and
    .properties.status != "Stopped" and
    .properties.status != "Suspended"
  )] | length == 0
' "$PRIVATE_WORK/final-jobs.json" >/dev/null

az policy assignment show \
  --subscription "$SUBSCRIPTION_ID" \
  --name "$POLICY_ASSIGNMENT" \
  --scope "$POLICY_SCOPE" \
  --output json >"$PRIVATE_WORK/final-policy-assignment.json"

jq -e '
  .enforcementMode == "Default" and
  .identity.type == "SystemAssigned" and
  (.identity.principalId | type == "string" and length > 0)
' "$PRIVATE_WORK/final-policy-assignment.json" >/dev/null
POLICY_PRINCIPAL="$(jq -r '.identity.principalId' \
  "$PRIVATE_WORK/final-policy-assignment.json")"

az role assignment list \
  --subscription "$SUBSCRIPTION_ID" \
  --assignee-object-id "$POLICY_PRINCIPAL" \
  --fill-principal-name false \
  --all \
  --output json >"$PRIVATE_WORK/final-policy-roles.json"

jq -e --arg rg "$POLICY_SCOPE" --arg workspace "$WORKSPACE_ID" '
  [.[] | {role: .roleDefinitionName, scope: (.scope | ascii_downcase)}] as $direct
  | ($direct | length) == 3 and
    ([$direct[] | select(.role == "Contributor" and .scope == ($rg | ascii_downcase))] | length) == 1 and
    ([$direct[] | select(.role == "Monitoring Contributor" and .scope == ($rg | ascii_downcase))] | length) == 1 and
    ([$direct[] | select(.role == "Log Analytics Contributor" and .scope == ($workspace | ascii_downcase))] | length) == 1
' "$PRIVATE_WORK/final-policy-roles.json" >/dev/null

assert_cost_center Microsoft.Automation/automationAccounts "$AUTOMATION_ACCOUNT"
assert_cost_center Microsoft.RecoveryServices/vaults "$RG_VAULT"
assert_cost_center Microsoft.RecoveryServices/vaults "$SUBSCRIPTION_VAULT"

DISCOVERY_ROLE_SUFFIX="$(printf '%s' "$POLICY_SCOPE" | sha256sum | cut -c1-12)"
DISCOVERY_ROLE="Azure Backup Smart Tiering Discovery Reader - $DISCOVERY_ROLE_SUFFIX"
az role assignment list \
  --subscription "$SUBSCRIPTION_ID" \
  --assignee-object-id "$AUTOMATION_PRINCIPAL" \
  --scope "$POLICY_SCOPE" \
  --fill-principal-name false \
  --output json >"$PRIVATE_WORK/final-automation-roles.json"

jq -e --arg scope "$POLICY_SCOPE" --arg reader "$DISCOVERY_ROLE" '
  [.[] | select((.scope | ascii_downcase) == ($scope | ascii_downcase))] as $direct
  | ([$direct[] | select(.roleDefinitionName == $reader)] | length) == 1
    and ([$direct[] | select(.roleDefinitionName | startswith("Azure Backup Smart Tiering Policy Remediator - "))] | length) == 0
    and ($direct | length) == 1
' "$PRIVATE_WORK/final-automation-roles.json" >/dev/null

az role definition list \
  --subscription "$SUBSCRIPTION_ID" \
  --custom-role-only true \
  --scope "$POLICY_SCOPE" \
  --output json >"$PRIVATE_WORK/final-custom-roles.json"
jq -e \
  --arg role "$DISCOVERY_ROLE" \
  --arg scope "$POLICY_SCOPE" \
  --slurpfile expected "$AUTOMATION_DIR/infra/rbac/discovery-reader-rg-role.template.json" '
    $expected[0] as $e
    | [.[] | select(.roleName == $role)] as $reader
    | ($reader | length) == 1 and
      ($reader[0].description == $e.Description) and
      (($reader[0].assignableScopes // [] | map(ascii_downcase) | sort) == [($scope | ascii_downcase)]) and
      (($reader[0].permissions // [] | length) == 1) and
      (($reader[0].permissions[0].actions // [] | map(ascii_downcase) | sort) == ($e.Actions | map(ascii_downcase) | sort)) and
      (($reader[0].permissions[0].notActions // [] | map(ascii_downcase) | sort) == ($e.NotActions | map(ascii_downcase) | sort)) and
      (($reader[0].permissions[0].dataActions // [] | map(ascii_downcase) | sort) == ($e.DataActions | map(ascii_downcase) | sort)) and
      (($reader[0].permissions[0].notDataActions // [] | map(ascii_downcase) | sort) == ($e.NotDataActions | map(ascii_downcase) | sort)) and
      ([.[] | select(.roleName | startswith("Azure Backup Smart Tiering Policy Remediator - "))] | length) == 0
  ' "$PRIVATE_WORK/final-custom-roles.json" >/dev/null

test "$WRITER_GRANTED" = false
find "$PRIVATE_WORK" -type f -delete
rmdir "$PRIVATE_WORK"
trap - EXIT
```

The expected result is two unchanged zero-item policies in
`TierRecommended`, a byte-matched Published PowerShell 7.4 runbook, no linked
or active Automation work, exactly one retained RG-scoped reader assignment,
and no remediator assignment or definition. Deleting the local evidence does
not delete the retained Azure showcase or its portal-visible job history.

## 9. Inspect the finished product

In the Azure portal, inspect these surfaces without copying tenant-specific
portal URLs into public documentation:

1. **Policy → Definitions**: the 16 custom definitions and
   `enterprise-baseline` initiative.
2. **Policy → Assignments**: `enterprise-baseline-showcase`, scoped only to the
   canary RG, enforced, with a system-assigned identity.
3. **Policy → Compliance**: compliant secure resources and remediated tags /
   Key Vault diagnostics; one intentional file-share audit finding.
4. **Resource group**: capped workspace, secure storage/share, purge-protected
   Key Vault, VNet/NSG/private NIC, Automation Account, and two empty vaults.
5. **Automation Account → Runbooks → Enable-SmartTiering**: Published,
   PowerShell 7.4, with audit/apply/idempotent job history and no schedules.
6. **Recovery Services vault → Backup policies**: the empty canary policy is
   `TierRecommended`; there are no protected items.
7. **Access control (IAM)**: Policy remediation roles belong to the Policy
   assignment identity; Automation retains reader only.

This is the intended retained state. Remove any temporary human elevation used
to create role definitions/assignments. Do not remove the Policy identity's
three remediation roles or the Automation reader while you want the live
showcase to remain functional.

## 10. Reuse in another tenant or subscription

Repeat the procedure with fresh names; do not copy an assignment export,
rendered parameter file, managed-identity ID, custom-role ID, or portal URL.
The portable inputs are:

- reviewed Git commit SHAs and the runbook SHA-256;
- approved Policy effects, allowed locations/SKUs, and required tag name;
- the target workspace resource ID generated in the destination; and
- fresh resource names generated in the destination.

Start at one empty RG with `DoNotEnforce`, require nonempty Policy state, then
promote. A successful deployment in one tenant says nothing about provider
registration, quotas, RBAC propagation, regional availability, or existing
Policy conflicts in another.

## 11. Optional cleanup

The purpose of this runbook is to leave the showcase alive. When you are done,
the combined guide owns teardown; do not follow Automation's standalone
resource-group deletion while Policy still uses the same RG.

```bash
# Refuse every teardown action unless the target is the exact owned scope and
# still carries the showcase ownership tag.
DELETE_RG_ID="$(az group show \
  --subscription "$SUBSCRIPTION_ID" \
  --name "$RESOURCE_GROUP" \
  --query id -o tsv)"
DELETE_RG_PURPOSE="$(az group show \
  --subscription "$SUBSCRIPTION_ID" \
  --name "$RESOURCE_GROUP" \
  --query 'tags.Purpose' -o tsv)"
test "$(printf '%s' "$DELETE_RG_ID" | tr '[:upper:]' '[:lower:]')" = \
  "$(printf '%s' "$POLICY_SCOPE" | tr '[:upper:]' '[:lower:]')"
test "$DELETE_RG_PURPOSE" = "AzurePolicyAutomationShowcase"

cd "$AUTOMATION_DIR"
scripts/ring-role.sh revoke \
  "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$AUTOMATION_PRINCIPAL"
scripts/discovery-role.sh revoke \
  "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$AUTOMATION_PRINCIPAL"

cd "$POLICY_DIR"
./scripts/unassign.sh \
  --name "$POLICY_ASSIGNMENT" \
  --scope "$POLICY_SCOPE"

# Deliberately retain the 16 cost-free definitions and initiative. Their fixed
# subscription-wide names can become shared after creation, so this guide does
# not infer exclusive ownership or call scripts/undeploy.sh.

# Review the exact owned RG one last time, then request asynchronous deletion.
az resource list \
  --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" \
  --query '[].{name:name,type:type}' \
  --output table
az group delete \
  --subscription "$SUBSCRIPTION_ID" \
  --name "$RESOURCE_GROUP" \
  --yes --no-wait
```

Deleting the RG soft-deletes the purge-protected Key Vault; its name remains
reserved for the soft-delete retention period unless an authorized operator
performs a separately reviewed purge. The subscription definitions and
initiative intentionally remain. Remove them only after a separate inventory
proves byte identity, exclusive ownership, and zero assignments at every scope.

## Related detail

- [Policy design and rollout boundaries](DESIGN.md)
- [Automation subsystem replication guide](https://github.com/kevo099/azure-backup-smart-tiering-automation/blob/main/docs/replicate-in-azure.md)
- [Automation design and limitations](https://github.com/kevo099/azure-backup-smart-tiering-automation/blob/main/docs/design-and-limitations.md)
- [Automation sanitized validation record](https://github.com/kevo099/azure-backup-smart-tiering-automation/blob/main/docs/validation.md)

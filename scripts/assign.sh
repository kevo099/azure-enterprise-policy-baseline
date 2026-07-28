#!/usr/bin/env bash
# Assigns the enterprise-baseline initiative at a scope with a system-assigned
# managed identity, then grants that identity the roles the Modify (tag
# inheritance) and DeployIfNotExists (key vault diagnostics) policies need to
# remediate resources.
set -euo pipefail

NAME="enterprise-baseline"
LOCATION="eastus2"
PARAMS_FILE=""
SCOPE=""
DEFINITION_SCOPE=""
ENFORCE="Default"

usage() {
  cat <<'EOF'
Usage: assign.sh --params <params.json> [options]

Options:
  --params <file>           Assignment parameter values (required); start from
                            examples/assignment-params.example.json
  --scope <scope-id>        Assignment scope, e.g. /subscriptions/<id> or
                            .../resourceGroups/<rg> (default: current subscription)
  --definition-scope <id>   Scope where deploy.sh published the initiative
                            (default: the assignment scope's subscription)
  --name <name>             Assignment name (default: enterprise-baseline)
  --location <region>       Managed-identity location (default: eastus2)
  --dry-run                 Assign with enforcementMode=DoNotEnforce: compliance
                            is evaluated and reported but nothing is blocked
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --params)           PARAMS_FILE="$2"; shift 2 ;;
    --scope)            SCOPE="$2"; shift 2 ;;
    --definition-scope) DEFINITION_SCOPE="$2"; shift 2 ;;
    --name)             NAME="$2"; shift 2 ;;
    --location)         LOCATION="$2"; shift 2 ;;
    --dry-run)          ENFORCE="DoNotEnforce"; shift ;;
    -h|--help)          usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$PARAMS_FILE" || ! -f "$PARAMS_FILE" ]]; then
  echo "--params <file> is required (see examples/assignment-params.example.json)" >&2
  exit 1
fi

command -v az >/dev/null || { echo "az CLI is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

[[ -z "$SCOPE" ]] && SCOPE="/subscriptions/$(az account show --query id -o tsv)"
if [[ -z "$DEFINITION_SCOPE" ]]; then
  if [[ "$SCOPE" =~ ^/subscriptions/([^/]+)(/|$) ]]; then
    DEFINITION_SCOPE="/subscriptions/${BASH_REMATCH[1]}"
  else
    DEFINITION_SCOPE="/subscriptions/$(az account show --query id -o tsv)"
  fi
fi
SET_DEF_ID="$DEFINITION_SCOPE/providers/Microsoft.Authorization/policySetDefinitions/enterprise-baseline"
LOG_ANALYTICS_SCOPE="$(jq -r '.logAnalyticsWorkspaceId.value // empty' "$PARAMS_FILE")"
if [[ -z "$LOG_ANALYTICS_SCOPE" ]]; then
  echo "params file must set logAnalyticsWorkspaceId.value" >&2
  exit 1
fi

# Capture the existing assignment in one request before updating it. A failed
# preflight must not be treated as "not found", because that would lose the old
# workspace scope needed for RBAC cleanup.
previous_assignment_exists=false
previous_principal=""
previous_log_analytics_scope=""
previous_assignment_json=""
if previous_assignment_json="$(az policy assignment show \
    --name "$NAME" --scope "$SCOPE" --only-show-errors -o json 2>&1)"; then
  previous_assignment_exists=true
  previous_principal="$(jq -r '.identity.principalId // empty' \
    <<<"$previous_assignment_json")"
  previous_log_analytics_scope="$(jq -r \
    '.parameters.logAnalyticsWorkspaceId.value // empty' \
    <<<"$previous_assignment_json")"
else
  previous_status=$?
  if [[ "$previous_status" -ne 3 ]]; then
    echo "Failed to inspect existing assignment '$NAME' at '$SCOPE':" >&2
    echo "$previous_assignment_json" >&2
    exit "$previous_status"
  fi
fi

echo "==> Assigning '$NAME' at $SCOPE (enforcement: $ENFORCE)"
az policy assignment create \
  --name "$NAME" \
  --display-name "Enterprise governance baseline" \
  --scope "$SCOPE" \
  --policy-set-definition "$SET_DEF_ID" \
  --params "$(cat "$PARAMS_FILE")" \
  --mi-system-assigned \
  --location "$LOCATION" \
  --enforcement-mode "$ENFORCE" \
  --output none

principal="$(az policy assignment show --name "$NAME" --scope "$SCOPE" \
  --query identity.principalId -o tsv)"
echo "==> Granting remediation roles to assignment identity $principal"

# Contributor covers the tag Modify operations; the two monitoring roles cover
# the diagnostic-settings DeployIfNotExists. Log Analytics permissions belong
# on the workspace itself, which can be outside the policy assignment scope.
# New identities can take a moment to propagate through Entra ID, hence the
# retry loop. Existing grants make the script safely idempotent.
roles=("Contributor" "Monitoring Contributor" "Log Analytics Contributor")
role_scopes=("$SCOPE" "$SCOPE" "$LOG_ANALYTICS_SCOPE")
grant_failures=0
for index in "${!roles[@]}"; do
  role="${roles[$index]}"
  role_scope="${role_scopes[$index]}"
  granted=false
  effective_scope=""
  for attempt in 1 2 3 4 5; do
    existing_scope="$(az role assignment list \
      --assignee-object-id "$principal" \
      --scope "$role_scope" \
      --include-inherited \
      --fill-principal-name false \
      --query "[?roleDefinitionName=='$role'] | [0].scope" \
      -o tsv 2>/dev/null || true)"
    if [[ -n "$existing_scope" ]]; then
      granted=true
      effective_scope="$existing_scope"
      break
    fi
    if az role assignment create \
         --assignee-object-id "$principal" \
         --assignee-principal-type ServicePrincipal \
         --role "$role" \
         --scope "$role_scope" \
         --output none 2>/dev/null; then
      granted=true
      effective_scope="$role_scope"
      break
    fi
    sleep 15
  done
  if [[ "$granted" == true ]]; then
    echo "    - $role effective at $effective_scope"
  else
    echo "    ! failed to grant '$role' at '$role_scope'" >&2
    grant_failures=$((grant_failures + 1))
  fi
done

if [[ "$grant_failures" -gt 0 ]]; then
  echo "Assignment exists, but $grant_failures required remediation role grant(s) failed." >&2
  exit 1
fi

cleanup_failures=0
delete_previous_grants() {
  local assignee="$1"
  local role="$2"
  local role_scope="$3"
  local ids_output=""
  local ids=()

  [[ -z "$assignee" || -z "$role_scope" ]] && return 0
  if ! ids_output="$(az role assignment list \
      --assignee-object-id "$assignee" \
      --scope "$role_scope" \
      --fill-principal-name false \
      --query "[?roleDefinitionName=='$role'].id" \
      -o tsv)"; then
    echo "    ! failed to list stale '$role' grants at '$role_scope'" >&2
    echo "      retry: az role assignment list --assignee-object-id '$assignee' \\" >&2
    echo "        --scope '$role_scope' --fill-principal-name false \\" >&2
    echo "        --query \"[?roleDefinitionName=='$role'].id\" -o tsv" >&2
    cleanup_failures=$((cleanup_failures + 1))
    return
  fi
  mapfile -t ids <<<"$ids_output"
  for id in "${ids[@]}"; do
    [[ -z "$id" ]] && continue
    if az role assignment delete --ids "$id" --output none; then
      echo "    - removed stale $role at $role_scope"
    else
      echo "    ! failed to remove stale '$role' at '$role_scope'" >&2
      echo "      retry: az role assignment delete --ids '$id'" >&2
      cleanup_failures=$((cleanup_failures + 1))
    fi
  done
}

normalize_resource_id() {
  local value="${1%/}"
  printf '%s' "${value,,}"
}

normalized_scope="$(normalize_resource_id "$SCOPE")"
normalized_log_analytics_scope="$(normalize_resource_id "$LOG_ANALYTICS_SCOPE")"
normalized_previous_log_analytics_scope="$(
  normalize_resource_id "$previous_log_analytics_scope"
)"
workspace_changed=false
if [[ "$normalized_previous_log_analytics_scope" != "$normalized_log_analytics_scope" ]]; then
  workspace_changed=true
fi

# A system-assigned principal is normally preserved by an in-place assignment
# update. Handle identity replacement defensively, and always remove the old
# workspace grant when either the principal or workspace changed.
if [[ -n "$previous_principal" && "$previous_principal" != "$principal" ]]; then
  delete_previous_grants "$previous_principal" "Contributor" "$SCOPE"
  delete_previous_grants "$previous_principal" "Monitoring Contributor" "$SCOPE"
fi
# assign.sh versions before 1.1 placed Log Analytics Contributor at the broad
# assignment scope. Remove that legacy direct grant once the least-scope
# workspace grant has succeeded.
if [[ "$previous_assignment_exists" == true &&
      -n "$previous_principal" &&
      "$normalized_scope" != "$normalized_log_analytics_scope" ]]; then
  delete_previous_grants "$previous_principal" "Log Analytics Contributor" "$SCOPE"
fi
if [[ -n "$previous_log_analytics_scope" ]] &&
   [[ "$previous_principal" != "$principal" || "$workspace_changed" == true ]]; then
  delete_previous_grants "$previous_principal" "Log Analytics Contributor" \
    "$previous_log_analytics_scope"
fi

if [[ "$cleanup_failures" -gt 0 ]]; then
  echo "Assignment updated, but $cleanup_failures stale remediation role cleanup(s) failed." >&2
  echo "Use the retry command(s) above; a later run cannot infer an older workspace scope." >&2
  exit 1
fi

scope_hint=""
if [[ "$SCOPE" =~ /resourceGroups/([^/]+)$ ]]; then
  scope_hint="--resource-group '${BASH_REMATCH[1]}'"
fi

cat <<EOF
==> Assigned. Useful follow-ups:
    Trigger an evaluation now:
      az policy state trigger-scan ${scope_hint:+$scope_hint }--no-wait
    Review compliance:
      az policy state summarize ${scope_hint:+$scope_hint }--policy-assignment "$NAME"
    Remediate existing resources (tags example):
      az policy remediation create ${scope_hint:+$scope_hint }--name inherit-tags \\
        --policy-assignment "$NAME" \\
        --definition-reference-id inherit-tag-from-resource-group
EOF

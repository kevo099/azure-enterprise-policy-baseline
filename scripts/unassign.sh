#!/usr/bin/env bash
# Removes an enterprise-baseline assignment and the RBAC grants created for
# its system-assigned managed identity. Safe to re-run after partial cleanup.
set -euo pipefail

NAME="enterprise-baseline"
SCOPE=""

usage() {
  cat <<'EOF'
Usage: unassign.sh [options]

Options:
  --scope <scope-id>  Assignment scope (default: current subscription)
  --name <name>       Assignment name (default: enterprise-baseline)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) SCOPE="$2"; shift 2 ;;
    --name)  NAME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

command -v az >/dev/null || { echo "az CLI is required" >&2; exit 1; }
[[ -z "$SCOPE" ]] && SCOPE="/subscriptions/$(az account show --query id -o tsv)"

if ! az policy assignment show --name "$NAME" --scope "$SCOPE" --output none 2>/dev/null; then
  echo "==> Assignment '$NAME' does not exist at $SCOPE"
  exit 0
fi

principal="$(az policy assignment show --name "$NAME" --scope "$SCOPE" \
  --query identity.principalId -o tsv)"
log_analytics_scope="$(az policy assignment show --name "$NAME" --scope "$SCOPE" \
  --query parameters.logAnalyticsWorkspaceId.value -o tsv)"

delete_role_grants() {
  local role="$1"
  local role_scope="$2"
  local ids=()

  [[ -z "$role_scope" ]] && return 0
  mapfile -t ids < <(az role assignment list \
    --assignee-object-id "$principal" \
    --scope "$role_scope" \
    --query "[?roleDefinitionName=='$role'].id" \
    -o tsv)
  for id in "${ids[@]}"; do
    [[ -z "$id" ]] && continue
    az role assignment delete --ids "$id" --output none
    echo "    - removed $role at $role_scope"
  done
}

echo "==> Removing remediation role grants for assignment identity $principal"
delete_role_grants "Contributor" "$SCOPE"
delete_role_grants "Monitoring Contributor" "$SCOPE"
# Remove both the current least-scope grant and the legacy assignment-scope
# grant produced by assign.sh versions before 1.1.
delete_role_grants "Log Analytics Contributor" "$log_analytics_scope"
if [[ "$log_analytics_scope" != "$SCOPE" ]]; then
  delete_role_grants "Log Analytics Contributor" "$SCOPE"
fi

echo "==> Deleting assignment '$NAME' from $SCOPE"
az policy assignment delete --name "$NAME" --scope "$SCOPE" --output none
echo "==> Unassigned cleanly."

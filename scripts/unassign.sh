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
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
[[ -z "$SCOPE" ]] && SCOPE="/subscriptions/$(az account show --query id -o tsv)"

assignment_json=""
if assignment_json="$(az policy assignment show \
    --name "$NAME" --scope "$SCOPE" --only-show-errors -o json 2>&1)"; then
  principal="$(jq -r '.identity.principalId // empty' <<<"$assignment_json")"
  log_analytics_scope="$(jq -r \
    '.parameters.logAnalyticsWorkspaceId.value // empty' <<<"$assignment_json")"
else
  assignment_status=$?
  if [[ "$assignment_status" -eq 3 ]]; then
    echo "==> Assignment '$NAME' does not exist at $SCOPE"
    exit 0
  fi
  echo "Failed to inspect assignment '$NAME' at '$SCOPE':" >&2
  echo "$assignment_json" >&2
  exit "$assignment_status"
fi

cleanup_failures=0
delete_role_grants() {
  local role="$1"
  local role_scope="$2"
  local ids_output=""
  local ids=()

  [[ -z "$principal" || -z "$role_scope" ]] && return 0
  if ! ids_output="$(az role assignment list \
      --assignee-object-id "$principal" \
      --scope "$role_scope" \
      --fill-principal-name false \
      --query "[?roleDefinitionName=='$role'].id" \
      -o tsv)"; then
    echo "    ! failed to list '$role' grants at '$role_scope'" >&2
    cleanup_failures=$((cleanup_failures + 1))
    return
  fi
  mapfile -t ids <<<"$ids_output"
  for id in "${ids[@]}"; do
    [[ -z "$id" ]] && continue
    if az role assignment delete --ids "$id" --output none; then
      echo "    - removed $role at $role_scope"
    else
      echo "    ! failed to remove '$role' at '$role_scope'" >&2
      cleanup_failures=$((cleanup_failures + 1))
    fi
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

if [[ "$cleanup_failures" -gt 0 ]]; then
  echo "Refusing to delete assignment: $cleanup_failures RBAC cleanup operation(s) failed." >&2
  exit 1
fi

echo "==> Deleting assignment '$NAME' from $SCOPE"
az policy assignment delete --name "$NAME" --scope "$SCOPE" --output none
echo "==> Unassigned cleanly."

#!/usr/bin/env bash
# Removes the enterprise-baseline initiative and all policy definitions from a
# subscription or management group. Assignments must be removed first.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MG=""
SUB=""

usage() {
  cat <<'EOF'
Usage: undeploy.sh [-s <subscription-id>] [-m <management-group-id>]

Deletes the enterprise-baseline initiative and its custom definitions. With
no arguments it targets the CLI's current subscription. Remove assignments
first with scripts/unassign.sh.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--subscription)     SUB="$2"; shift 2 ;;
    -m|--management-group) MG="$2"; shift 2 ;;
    -h|--help)             usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -n "$MG" && -n "$SUB" ]]; then
  echo "Pick one target: --subscription or --management-group, not both." >&2
  exit 1
fi

command -v az >/dev/null || { echo "az CLI is required" >&2; exit 1; }

SCOPE_ARGS=()
if [[ -n "$MG" ]]; then
  SCOPE_ARGS=(--management-group "$MG")
  DEFINITION_SCOPE="/providers/Microsoft.Management/managementGroups/$MG"
else
  [[ -z "$SUB" ]] && SUB="$(az account show --query id -o tsv)"
  SCOPE_ARGS=(--subscription "$SUB")
  DEFINITION_SCOPE="/subscriptions/$SUB"
fi

echo "==> Removing initiative from $DEFINITION_SCOPE"
if az policy set-definition show "${SCOPE_ARGS[@]}" \
     --name enterprise-baseline --output none 2>/dev/null; then
  az policy set-definition delete "${SCOPE_ARGS[@]}" \
    --name enterprise-baseline --output none
  echo "    - enterprise-baseline"
fi

echo "==> Removing policy definitions"
count=0
while IFS= read -r file; do
  name="$(basename "$file" .json)"
  if az policy definition show "${SCOPE_ARGS[@]}" \
       --name "$name" --output none 2>/dev/null; then
    az policy definition delete "${SCOPE_ARGS[@]}" \
      --name "$name" --output none
    echo "    - $name"
    count=$((count + 1))
  fi
done < <(find "$REPO_ROOT/policies" -name '*.json' | sort)

echo "==> Done: removed $count definition(s)."

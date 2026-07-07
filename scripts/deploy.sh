#!/usr/bin/env bash
# Deploys every policy definition in policies/ plus the enterprise-baseline
# initiative to a subscription (default: the CLI's current one) or a
# management group. Safe to re-run: create acts as an upsert for both
# definitions and initiatives.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MG=""
SUB=""

usage() {
  cat <<'EOF'
Usage: deploy.sh [-s <subscription-id>] [-m <management-group-id>]

Creates or updates all 15 policy definitions and the enterprise-baseline
initiative. With no arguments it targets the CLI's current subscription.
Deploy to a management group to make the baseline assignable anywhere
beneath it.
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
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

SCOPE_ARGS=()
if [[ -n "$MG" ]]; then
  SCOPE_ARGS=(--management-group "$MG")
  DEFINITION_SCOPE="/providers/Microsoft.Management/managementGroups/$MG"
else
  [[ -z "$SUB" ]] && SUB="$(az account show --query id -o tsv)"
  SCOPE_ARGS=(--subscription "$SUB")
  DEFINITION_SCOPE="/subscriptions/$SUB"
fi

echo "==> Deploying policy definitions to $DEFINITION_SCOPE"
count=0
while IFS= read -r file; do
  name="$(jq -r '.name' "$file")"
  echo "    - $name"
  az policy definition create \
    "${SCOPE_ARGS[@]}" \
    --name "$name" \
    --display-name "$(jq -r '.properties.displayName' "$file")" \
    --description "$(jq -r '.properties.description' "$file")" \
    --mode "$(jq -r '.properties.mode' "$file")" \
    --metadata "category=$(jq -r '.properties.metadata.category' "$file")" \
               "version=$(jq -r '.properties.metadata.version' "$file")" \
    --params "$(jq -c '.properties.parameters' "$file")" \
    --rules "$(jq -c '.properties.policyRule' "$file")" \
    --output none
  count=$((count + 1))
done < <(find "$REPO_ROOT/policies" -name '*.json' | sort)

echo "==> Deploying initiative: enterprise-baseline"
initiative="$(sed "s|{{DEFINITION_SCOPE}}|$DEFINITION_SCOPE|g" \
  "$REPO_ROOT/initiatives/enterprise-baseline.json")"
az policy set-definition create \
  "${SCOPE_ARGS[@]}" \
  --name "$(jq -r '.name' <<<"$initiative")" \
  --display-name "$(jq -r '.properties.displayName' <<<"$initiative")" \
  --description "$(jq -r '.properties.description' <<<"$initiative")" \
  --metadata "category=$(jq -r '.properties.metadata.category' <<<"$initiative")" \
             "version=$(jq -r '.properties.metadata.version' <<<"$initiative")" \
  --params "$(jq -c '.properties.parameters' <<<"$initiative")" \
  --definitions "$(jq -c '.properties.policyDefinitions' <<<"$initiative")" \
  --output none

echo "==> Done: $count definitions + 1 initiative deployed."
echo "    Next: scripts/assign.sh --params <your-params.json> [--dry-run]"

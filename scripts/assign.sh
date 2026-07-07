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

[[ -z "$SCOPE" ]] && SCOPE="/subscriptions/$(az account show --query id -o tsv)"
[[ -z "$DEFINITION_SCOPE" ]] && DEFINITION_SCOPE="/subscriptions/$(az account show --query id -o tsv)"
SET_DEF_ID="$DEFINITION_SCOPE/providers/Microsoft.Authorization/policySetDefinitions/enterprise-baseline"

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
# the diagnostic-settings DeployIfNotExists. New identities can take a moment
# to propagate through Entra ID, hence the retry loop.
for role in "Contributor" "Monitoring Contributor" "Log Analytics Contributor"; do
  granted=false
  for attempt in 1 2 3 4 5; do
    if az role assignment create \
         --assignee-object-id "$principal" \
         --assignee-principal-type ServicePrincipal \
         --role "$role" \
         --scope "$SCOPE" \
         --output none 2>/dev/null; then
      granted=true
      break
    fi
    sleep 15
  done
  if [[ "$granted" == true ]]; then
    echo "    - $role"
  else
    echo "    ! failed to grant '$role' — grant it manually before remediating" >&2
  fi
done

cat <<EOF
==> Assigned. Useful follow-ups:
    Trigger an evaluation now:
      az policy state trigger-scan --no-wait
    Review compliance:
      az policy state summarize --policy-assignment "$NAME"
    Remediate existing resources (tags example):
      az policy remediation create --name inherit-tags \\
        --policy-assignment "$NAME" \\
        --definition-reference-id inherit-tag-from-resource-group
EOF

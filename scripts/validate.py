#!/usr/bin/env python3
"""Structural validator for the policy definitions and initiative in this repo.

Checks every policies/**/*.json and initiatives/*.json for the mistakes that
otherwise only surface when Azure rejects the deployment: malformed JSON,
missing required properties, undeclared or unused parameters, count expressions
without a comparison operator, remediation effects without roleDefinitionIds,
and initiative references that don't line up with the definitions on disk.

Runs on the Python standard library only. Exit code 0 = clean, 1 = findings.
"""
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = ROOT / "policies"
INITIATIVE_DIR = ROOT / "initiatives"

VALID_MODES = {"All", "Indexed"}
EFFECT_VALUES = {
    "Append",
    "Audit",
    "AuditIfNotExists",
    "Deny",
    "DenyAction",
    "DeployIfNotExists",
    "Disabled",
    "Manual",
    "Modify",
}
COUNT_OPERATORS = {
    "equals",
    "notEquals",
    "greater",
    "greaterOrEquals",
    "less",
    "lessOrEquals",
}
PARAM_RE = re.compile(r"parameters\('([^']+)'\)")

errors = []


def err(path, msg):
    errors.append(f"{path.relative_to(ROOT)}: {msg}")


def walk_dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_dicts(value)


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_strings(value)


def param_refs(node):
    refs = set()
    for text in walk_strings(node):
        refs.update(PARAM_RE.findall(text))
    return refs


def rule_without_arm_template(rule):
    """The ARM template inside a DeployIfNotExists deployment declares its own
    parameters, which must not be confused with policy parameters."""
    pruned = copy.deepcopy(rule)
    details = pruned.get("then", {}).get("details", {})
    deployment = details.get("deployment", {}) if isinstance(details, dict) else {}
    properties = deployment.get("properties", {}) if isinstance(deployment, dict) else {}
    if isinstance(properties, dict):
        properties.pop("template", None)
    return pruned


def load_json(path):
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        err(path, f"invalid JSON: {exc}")
        return None


def validate_policy(path):
    doc = load_json(path)
    if doc is None:
        return None

    name = doc.get("name")
    props = doc.get("properties")
    if not name or not isinstance(props, dict):
        err(path, "must have top-level 'name' and 'properties'")
        return None

    if name != path.stem:
        err(path, f"name '{name}' does not match filename '{path.stem}'")
    if len(name) > 64:
        err(path, f"name exceeds 64 characters ({len(name)})")

    display_name = props.get("displayName", "")
    if not display_name:
        err(path, "missing properties.displayName")
    elif len(display_name) > 128:
        err(path, f"displayName exceeds 128 characters ({len(display_name)})")
    if not props.get("description"):
        err(path, "missing properties.description")
    if props.get("mode") not in VALID_MODES:
        err(path, f"mode must be one of {sorted(VALID_MODES)}")
    metadata = props.get("metadata", {})
    for key in ("category", "version"):
        if not metadata.get(key):
            err(path, f"missing metadata.{key}")

    rule = props.get("policyRule")
    if not isinstance(rule, dict) or "if" not in rule or "then" not in rule:
        err(path, "policyRule must contain 'if' and 'then'")
        return None
    then = rule["then"]
    effect = then.get("effect")
    if not effect:
        err(path, "policyRule.then must set an effect")
        return None

    params = props.get("parameters", {}) or {}

    # Effect must be a declared parameter (or a literal effect value).
    effect_values = set()
    match = PARAM_RE.search(effect)
    if match:
        effect_param = params.get(match.group(1))
        if effect_param is None:
            err(path, f"effect references undeclared parameter '{match.group(1)}'")
        else:
            effect_values = set(effect_param.get("allowedValues", []))
            bad = effect_values - EFFECT_VALUES
            if not effect_values:
                err(path, "effect parameter must declare allowedValues")
            if bad:
                err(path, f"effect parameter allows non-effect values: {sorted(bad)}")
    else:
        effect_values = {effect}
        if effect not in EFFECT_VALUES:
            err(path, f"unknown literal effect '{effect}'")

    # Parameter declarations and references must match exactly.
    declared = set(params)
    referenced = param_refs(rule_without_arm_template(rule))
    for missing in sorted(referenced - declared):
        err(path, f"policyRule references undeclared parameter '{missing}'")
    for unused in sorted(declared - referenced):
        err(path, f"parameter '{unused}' is declared but never referenced")

    # Every count expression needs a sibling comparison operator.
    for node in walk_dicts(rule):
        if "count" in node and not COUNT_OPERATORS & set(node):
            err(path, "count expression lacks a comparison operator "
                       f"(one of {sorted(COUNT_OPERATORS)})")

    # Remediation effects need deployment details and roleDefinitionIds.
    details = then.get("details", {})
    if effect_values & {"DeployIfNotExists", "Modify"}:
        if not details.get("roleDefinitionIds"):
            err(path, "DeployIfNotExists/Modify requires details.roleDefinitionIds")
    if "DeployIfNotExists" in effect_values and not details.get("deployment"):
        err(path, "DeployIfNotExists requires details.deployment")
    if "Modify" in effect_values and not details.get("operations"):
        err(path, "Modify requires details.operations")
    if effect_values & {"AuditIfNotExists", "DeployIfNotExists"} and not details.get("type"):
        err(path, "AuditIfNotExists/DeployIfNotExists requires details.type")

    return doc


def validate_initiative(path, definitions):
    doc = load_json(path)
    if doc is None:
        return

    props = doc.get("properties", {})
    if not doc.get("name") or not props:
        err(path, "must have top-level 'name' and 'properties'")
        return
    for key in ("displayName", "description"):
        if not props.get(key):
            err(path, f"missing properties.{key}")

    declared = set(props.get("parameters", {}) or {})
    referenced = set()
    seen_refs = set()
    covered = set()

    for entry in props.get("policyDefinitions", []):
        ref_id = entry.get("policyDefinitionReferenceId", "")
        if not ref_id:
            err(path, "policyDefinitions entry missing policyDefinitionReferenceId")
        elif ref_id in seen_refs:
            err(path, f"duplicate policyDefinitionReferenceId '{ref_id}'")
        seen_refs.add(ref_id)

        def_id = entry.get("policyDefinitionId", "")
        if "{{DEFINITION_SCOPE}}" not in def_id:
            err(path, f"'{ref_id}': policyDefinitionId must use the "
                       "{{DEFINITION_SCOPE}} placeholder")
        def_name = def_id.rsplit("/", 1)[-1]
        definition = definitions.get(def_name)
        if definition is None:
            err(path, f"'{ref_id}': no definition named '{def_name}' in policies/")
            continue
        covered.add(def_name)

        bindings = entry.get("parameters", {}) or {}
        referenced.update(param_refs(bindings))

        def_params = definition["properties"].get("parameters", {}) or {}
        for key in bindings:
            if key not in def_params:
                err(path, f"'{ref_id}': binds unknown definition parameter '{key}'")
        for key, spec in def_params.items():
            if "defaultValue" not in spec and key not in bindings:
                err(path, f"'{ref_id}': required definition parameter "
                           f"'{key}' is not bound")

    for missing in sorted(referenced - declared):
        err(path, f"references undeclared initiative parameter '{missing}'")
    for unused in sorted(declared - referenced):
        err(path, f"initiative parameter '{unused}' is never used")
    for orphan in sorted(set(definitions) - covered):
        err(path, f"definition '{orphan}' exists in policies/ but is not "
                   "included in the initiative")


def main():
    policy_files = sorted(POLICY_DIR.rglob("*.json"))
    initiative_files = sorted(INITIATIVE_DIR.glob("*.json"))
    if not policy_files:
        print("no policy files found under policies/", file=sys.stderr)
        return 1

    definitions = {}
    for path in policy_files:
        doc = validate_policy(path)
        if doc is not None and doc.get("name"):
            definitions[doc["name"]] = doc

    for path in initiative_files:
        validate_initiative(path, definitions)

    if errors:
        print(f"FAIL: {len(errors)} finding(s)\n")
        for finding in errors:
            print(f"  - {finding}")
        return 1

    print(f"OK: {len(policy_files)} policy definition(s) and "
          f"{len(initiative_files)} initiative(s) validated clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

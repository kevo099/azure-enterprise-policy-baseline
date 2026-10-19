# Design notes

How this baseline was constructed, the decisions baked into it, and the sharp
edges to know about before enforcing it.

## Sources and method

The operational selection rule is Microsoft first: evaluate supported
service-owned built-in policies, Azure Landing Zones assignments, and official
deployment templates before adopting a repository-owned definition. The
custom catalog then provides pinned alternatives for controls that are broadly
applicable to enterprises rather than industry-specific:

- **Azure Policy built-in catalog** — the rule *patterns* (alias choices,
  `exists`/`equals` handling for properties with platform defaults, the
  `requestContext().apiVersion` guard, AuditIfNotExists existence checks,
  DeployIfNotExists deployment shape) follow how Microsoft's built-ins solve
  the same problems, because those patterns encode years of evaluation-engine
  edge cases.
- **Azure Landing Zones (Enterprise-Scale)** — the control selection mirrors
  the guardrails ALZ assigns by default: deny insecure storage transport,
  deny Internet-exposed management ports, deny public IPs on NICs, restrict
  locations, enforce tagging.
- **Cloud Adoption Framework governance disciplines** — the category split
  (security baseline, cost/resource consistency, identity, operations) comes
  from CAF's Five Disciplines of Cloud Governance.

Custom re-implementations remain available so definitions can be
version-controlled here, read end-to-end, consistently parameterized (every
policy exposes `effect`), and kept under an organization-owned ID. Those are
explicit tradeoffs, not a blanket claim that custom is better. If a Microsoft
built-in or template satisfies the requirement and its lifecycle state is
acceptable, it is the recommended starting point.

Azure Files backup demonstrates the rule: use Microsoft's existing-vault
`DeployIfNotExists` and audit definitions first. Use this repository's custom
audit twin only when Preview adoption is prohibited or a pinned initiative
reference is required, and use the reconciliation script as an independent
operational backstop.

## Decisions

**Every effect is a parameter.** Definitions never hard-code their effect.
The initiative re-exposes each one, so a single assignment can mix enforcement
levels and a brownfield estate can start at `Audit` everywhere without forking
JSON. Defaults are deliberately strict (`Deny`) because a baseline should be
secure by default; the [rollout procedure](#rollout) is the escape hatch.

**Properties with platform defaults are treated as non-compliant when
absent.** `allowBlobPublicAccess`, `enablePurgeProtection`, and
`minimumTlsVersion` are flagged when omitted, not just when explicitly bad,
because the platform default for each is the insecure value. The one
exception is storage HTTPS enforcement, where API versions ≥ 2019-04-01
default to secure — the rule uses a `requestContext().apiVersion` guard so
modern deployments that omit the property aren't falsely denied (this mirrors
the built-in).

**`count` expressions instead of double negation.** Array conditions like
"any ipConfiguration has a public IP" are written as
`count(... where ...) > 0` rather than the equivalent but harder-to-review
`not(... notLike ...)` construction some built-ins use.

**Tag governance is a two-policy system.** Deny missing or empty tags on
*resource groups* (humans create those deliberately) + Modify-inherit onto
*resources* that have a missing or empty value (created constantly, often by
automation). Non-empty resource overrides are preserved. Enforcing tags on
every resource directly generates deployment friction; inheriting from the RG
gives the same cost attribution coverage for free. The Modify policy uses the
Contributor role for remediation to match Microsoft's built-in inherit-tag
policy; scope it down to Tag Contributor if your security review prefers least
privilege.

**Diagnostics DINE uses `categoryGroup: "audit"`.** Category groups track new
log categories automatically, unlike enumerating categories. The existence
condition requires enabled `audit` logs and `AllMetrics` pointed at the central
workspace. `evaluationDelay: AfterProvisioning` avoids a fixed post-create wait,
and assignment tooling scopes Log Analytics permissions to the workspace.

**Initiative references use a `{{DEFINITION_SCOPE}}` placeholder.** Policy
set definitions must reference definitions by full resource ID, which isn't
known until deploy time. `deploy.sh` substitutes the subscription or
management group scope. The validator enforces that the placeholder is used
and that every referenced definition exists on disk (and vice versa — a
definition missing from the initiative fails CI).

**Org-specific values have no defaults.** Allowed locations, allowed VM SKUs,
and the Log Analytics workspace ID must be supplied at assignment. Any
default this repo could ship would be wrong for most organizations, and a
wrong-but-working default is how policy ends up silently ineffective.

## Rollout

Roll the initiative out in stages so that a strict default never surprises an
existing estate:

1. **Publish** the definitions and initiative at the definition scope
   (`scripts/deploy.sh`). Definitions alone change nothing.
2. **Canary in report-only mode.** Assign to one owned resource group with
   `scripts/assign.sh --dry-run` (`DoNotEnforce`). Trigger a compliance scan
   and wait for nonempty state; zero rows is inconclusive, not compliant.
3. **Broaden in audit.** Assign to the wider scope with the `Deny` effects set
   to `Audit` in the parameter file. Review every noncompliant resource and add
   time-bound exemptions where an exception is justified.
4. **Promote per scope.** Restore the `Deny` effects and the default
   enforcement mode one scope at a time. For `Modify` and `DeployIfNotExists`
   effects, trigger a scan after the change, wait for a compliance record newer
   than the change, then create the remediation and require at least one
   successful deployment.
5. **Roll back by effect, not deletion.** The fast escape is to set the affected
   effect to `Audit`/`Disabled` or the assignment back to `DoNotEnforce`. That
   keeps the assignment identity and its role grants, so promoting again does not
   recreate them. Remove an assignment with `scripts/unassign.sh` only when
   retiring it.

The [Policy + Automation canary](REPLICATE-POLICY-AUTOMATION.md) implements
steps 1, 2 and 4 end to end in a disposable resource group.

## Known limitations

- **NSG port policy (3.0.0) covers inline rules and port ranges.** It evaluates
  both the child `securityRules` type and rules declared inline in an NSG
  (`securityRules[*]`), and it compares each destination port, list entry and
  `N-M` range against every blocked port, so `20-25` and `1-65535` are caught.
  Versions before 3.0.0 matched only child rules and exact port strings; a
  2026-09-27 [probe](LIVE-TEST-2026-09-27-new-tenant.md#nsg-observations) proved
  both bypasses. On 2026-10-19 the 3.0.0 rule passed 21 of 21 validation probes
  (13 denials, including all four 2026-09-27 bypass shapes, and 8 correct
  allows) and two real requests, with a test copy assigned to one resource group
  ([record](LIVE-TEST-2026-10-19-new-tenant.md#nsg-management-ports-300)).
- **NSG source matching is a literal list.** Sources `*`, `Internet`,
  `0.0.0.0`, `0.0.0.0/0` and `::/0` (as a prefix or in a prefix list) are
  treated as the Internet. A rule that reaches the Internet through split
  prefixes such as `0.0.0.0/1` plus `128.0.0.0/1`, or through other public
  ranges, is not detected. Keep the `ports` parameter to numeric ports or `N-M`
  ranges (at most 100 entries); compliance scans and periodic rule review
  remain the backstop for unusual shapes.
- **`allowed-vm-skus` covers `Microsoft.Compute/virtualMachines` only**, not
  scale-set SKU properties. Add a VMSS variant if scale sets are common in
  your estate.
- **VM sizes offered vary by subscription and region.** Before setting
  `allowedVmSkus`, check
  `az vm list-skus --location <region> --resource-type virtualMachines` in the
  target subscription; its output accounts for subscription-level
  restrictions. Also check regional vCPU quota with
  `az vm list-usage --location <region>`. The example parameter file lists
  B-series and v5 sizes, but a 2026-09-27 Free Trial subscription was offered
  no v5 D-series size in eastus2, and its regional vCPU quota was 4.
- **The unmanaged-disk policy is legacy defense in depth.** Azure retired
  unmanaged disks on 2026-03-31, so new subscriptions cannot provision a real
  violating VM. A crafted ARM request can still prove policy interception, as
  a 2026-09-27
  [validation probe](LIVE-TEST-2026-09-27-new-tenant.md#validation-only-deny-probes)
  did.
- **The diagnostics DINE can conflict with pre-existing settings** that send
  the same categories to a different sink under a different setting name
  (Azure rejects duplicate category/destination pairs). Existing vaults with
  bespoke diagnostics should get an exemption instead of remediation.
- **`allowed-locations` exempts `global`** and B2C directories; it does not
  govern resource group locations themselves (add the RG-location variant if
  you need it).

## Testing a change

1. `python3 scripts/validate.py` — structural correctness.
2. Deploy to a sandbox subscription: `./scripts/deploy.sh -s <sandbox-sub>`.
   Azure performs full server-side validation of rules, aliases, and
   parameter plumbing at creation time, so a clean deploy is a meaningful
   syntax check.
3. Assign to a throwaway resource group with `--dry-run`, deploy a
   deliberately non-compliant resource (e.g. a storage account with
   `--allow-blob-public-access true`), then
   `az policy state trigger-scan --resource-group <rg> --no-wait` and confirm it shows
   non-compliant. If a scan is already running for the group, the request
   joins it, and a resource created after that scan began may need a later
   scan before it appears.
4. Promote the same assignment to enforcing and confirm the create is denied.
5. Test `require-tag-on-resource-groups` separately at subscription scope,
   excluding all existing resource groups with `notScopes`; an assignment on
   an already-created RG cannot govern that RG's creation.
6. Remove the assignment with `scripts/unassign.sh`, then remove definitions
   with `scripts/undeploy.sh`.

## Versioning

Definitions carry a semantic `version` in metadata. Bump patch for
description/metadata edits, minor for rule changes that don't widen scope,
major for anything that could newly deny a previously allowed request.

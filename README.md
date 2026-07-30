# Azure Enterprise Policy Baseline

A ready-to-deploy set of **16 custom Azure Policy definitions**, bundled into a
single **initiative**, that implements the governance guardrails Microsoft
recommends for enterprise Azure estates. The patterns are drawn from
Microsoft's own published governance tooling — the Azure Policy built-in
catalog, the Azure Landing Zones (Enterprise-Scale) policy set, and the Cloud
Adoption Framework governance disciplines — re-implemented as clean,
consistently parameterized custom definitions you own, can read end-to-end,
and can extend.

Everything deploys and tears down with small shell scripts (`az` + `jq` are
the only dependencies), and a stdlib-only Python validator keeps the JSON
honest in CI.

> This is a community project. It is not affiliated with or endorsed by
> Microsoft.

## What's in the baseline

| # | Policy | Category | Default effect | What it enforces |
|---|--------|----------|----------------|------------------|
| 1 | `deny-storage-https-disabled` | Security | Deny | Storage accounts must enforce HTTPS-only traffic |
| 2 | `deny-storage-minimum-tls` | Security | Deny | Storage accounts must set a minimum TLS version (default TLS 1.2) |
| 3 | `deny-storage-public-blob-access` | Security | Deny | Storage accounts must disallow anonymous blob access |
| 4 | `deny-nsg-open-management-ports` | Security | Deny | No NSG rule may allow SSH/RDP (parameterized) inbound from the Internet |
| 5 | `deny-nic-public-ip` | Security | Deny | Network interfaces must not attach public IP addresses |
| 6 | `deny-sql-public-network-access` | Security | Deny | SQL logical servers must disable public network access |
| 7 | `deny-keyvault-purge-protection-disabled` | Security | Deny | Key vaults must enable purge protection |
| 8 | `require-tag-on-resource-groups` | Governance | Deny | Resource groups must carry a non-empty required tag (default `CostCenter`) |
| 9 | `inherit-tag-from-resource-group` | Governance | Modify | Missing or empty resource tags inherit the value from their resource group |
| 10 | `allowed-locations` | Governance | Deny | Resources may only deploy to approved regions |
| 11 | `allowed-vm-skus` | Governance | Deny | VMs may only use approved sizes |
| 12 | `audit-vm-backup-protection` | Operations | AuditIfNotExists | Flags VMs not protected by Azure Backup |
| 13 | `audit-file-share-backup-protection` | Operations | AuditIfNotExists | Flags SMB Azure file shares not protected by Azure Backup |
| 14 | `deny-vm-unmanaged-disks` | Operations | Deny | VMs and scale sets must use managed disks |
| 15 | `deploy-keyvault-diagnostics` | Operations | DeployIfNotExists | Auto-deploys key vault audit logging and metrics to Log Analytics |
| 16 | `audit-vm-system-assigned-identity` | Identity | Audit | Flags VMs without a system-assigned managed identity |

The `enterprise-baseline` initiative includes all sixteen and surfaces every
policy's effect as an initiative parameter, so you can run the entire baseline
in audit mode, harden policy-by-policy, or disable individual policies —
all from assignment parameters, never by editing definitions.

## Repository layout

```
policies/            One JSON file per policy definition, grouped by category
  security/          7 deny policies for data-in-transit, exposure, key protection
  governance/        4 policies for tags, regions, and SKU control
  operations/        4 policies for backup, managed disks, diagnostics
  identity/          1 policy for managed identity adoption
initiatives/         enterprise-baseline.json (policy set definition)
scripts/             Deploy, assign, unassign, undeploy, and validation tools
examples/            Starter assignment parameter values
docs/DESIGN.md       Design decisions, sources, known limitations, rollout SOP
.github/workflows/   CI: structural validation on every push and PR
```

Each policy file is a complete, self-describing definition: `displayName`,
`description` (including *why* the control matters), semantic `version`,
`mode`, declared `parameters`, and the `policyRule`.

## Quickstart

```bash
az login

# 1. Publish the 16 definitions + initiative
#    (current subscription by default, or -m <management-group-id>)
./scripts/deploy.sh

# 2. Set your organization's values
cp examples/assignment-params.example.json my-params.json
$EDITOR my-params.json   # allowed regions/SKUs, Log Analytics workspace, tag name

# 3. Assign — start in report-only mode
./scripts/assign.sh --params my-params.json --dry-run
```

`assign.sh` creates the assignment with a system-assigned managed identity and
grants it the roles the Modify/DeployIfNotExists policies need. Contributor and
Monitoring Contributor are scoped to the assignment; Log Analytics Contributor
is scoped to the selected workspace, even when that workspace is in another
resource group. The script is idempotent and exits nonzero if any required role
grant fails. `my-params.json` is gitignored so real subscription IDs stay out of
the repo.

### Recommended rollout

1. **Assign with `--dry-run`** (`enforcementMode: DoNotEnforce`). Compliance is
   evaluated and reported, nothing is blocked yet.
2. **Review the compliance dashboard** for a week:
   `az policy state summarize --policy-assignment enterprise-baseline`
3. **Remediate existing resources** where it's automatic:
   ```bash
   az policy remediation create --name inherit-tags \
     --policy-assignment enterprise-baseline \
     --definition-reference-id inherit-tag-from-resource-group
   az policy remediation create --name kv-diagnostics \
     --policy-assignment enterprise-baseline \
     --definition-reference-id deploy-keyvault-diagnostics
   ```
4. **Re-assign without `--dry-run`** once the estate is clean. For a gentler
   path, flip individual effects to `Audit` in your params file and promote
   them to `Deny` one at a time.

Exemptions for legitimate exceptions (that one NVA that genuinely needs a
public IP) belong in `az policy exemption create` — not in weakened policy.

### Clean removal

Remove assignments before definitions so the managed identity's RBAC grants
do not become orphaned:

```bash
./scripts/unassign.sh --name enterprise-baseline --scope /subscriptions/<id>
./scripts/undeploy.sh --subscription <id>
```

Resource-group assignments use the full RG resource ID as `--scope`. These
commands remove policy and RBAC objects; application resources remain under
your normal lifecycle tooling.

## Live validation

The original 15-policy baseline was server-validated and exercised in a
disposable Azure subscription on 2026-07-16. The test matrix, platform caveats,
fixes found, and cleanup assertions are recorded in
[docs/LIVE-TEST-2026-07-16.md](docs/LIVE-TEST-2026-07-16.md). The
`audit-file-share-backup-protection` policy was added later and validated
separately on 2026-07-28 — see
[docs/LIVE-TEST-2026-07-28-file-share-backup.md](docs/LIVE-TEST-2026-07-28-file-share-backup.md)
and the step-by-step
[file-share backup audit guide](docs/GUIDE-file-share-backup-audit.md).

## Validating changes

```bash
python3 scripts/validate.py
```

The validator checks JSON syntax, required properties, name/filename
agreement, that every parameter is both declared and used, that `count`
expressions carry a comparison operator, that Modify/DeployIfNotExists
policies declare `roleDefinitionIds` and deployment details, and that the
initiative's references, parameter bindings, and coverage all line up with the
definitions on disk. The same check runs in GitHub Actions on every push.

## Extending the baseline

1. Drop a new definition JSON into the right `policies/<category>/` folder
   (filename = policy `name`).
2. Add a reference (and any new parameters) to
   `initiatives/enterprise-baseline.json`.
3. `python3 scripts/validate.py` — it will fail until the initiative includes
   your new policy, which is by design: definitions and initiative can't
   drift apart.
4. Re-run `./scripts/deploy.sh`; bump the `version` in any definition you
   changed.

## Versioning and releases

Repository releases use semantic tags such as `v1.0.0` for a tested snapshot
of the complete baseline. The `version` fields inside policy and initiative
JSON files are an independent namespace: bump the affected in-file version
when its Azure definition changes, even when the repository release number
also changes.

Use a tagged release for reproducible deployment. Review the release notes and
validation records before promoting a newer tag, and roll changes through the
report-only workflow above rather than treating a repository tag as automatic
authorization to enforce policy.

## Support and security

Use GitHub Issues for reproducible defects and documentation problems. This
community project does not replace Microsoft support for Azure platform
behavior. For security-sensitive reports, follow [SECURITY.md](SECURITY.md)
instead of opening a public issue.

## License

[MIT](LICENSE)

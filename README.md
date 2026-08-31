# Azure Enterprise Policy Baseline

A decision-first Azure governance reference: use Microsoft-managed built-in
policies and Microsoft-published deployment templates first, then use this
repository's **16 custom Azure Policy definitions** where you need a pinned
rule, an organization-owned definition, or one consistently parameterized
initiative. The custom definitions are bundled into a single **initiative**
and remain ready to deploy, read end-to-end, and extend.

The recommendations are drawn from Microsoft's Azure Policy built-in catalog,
Azure Landing Zones (Enterprise-Scale), Cloud Adoption Framework governance
disciplines, and Microsoft-published templates. A custom implementation is an
explicit fallback or extension, not the default merely because it is included
here.

The custom baseline deploys and tears down with small shell scripts (`az` +
`jq` are the only dependencies), and a stdlib-only Python validator keeps the
JSON honest in CI.

> This is a community project. It is not affiliated with or endorsed by
> Microsoft.

## Recommended order of use

1. Prefer a generally available Microsoft built-in when it expresses the
   required control.
2. Evaluate a Microsoft Preview built-in in report-only mode and a canary scope
   before enabling remediation.
3. Use a Microsoft Quickstart or Azure Verified Module when the requirement is
   resource deployment rather than continuous policy evaluation.
4. Use the custom definitions below when the Microsoft option is missing,
   unsuitable, prohibited by your Preview policy, or must be frozen and owned
   by your organization.

### Azure Files backup: Microsoft options first

For automatic coverage, start with Microsoft's existing-vault, tag-exclusion
policy. It keeps vault and backup-policy design under central control. The
remaining Microsoft policies and templates cover tag-inclusion,
application-owned vaults, report-only auditing, and explicitly named shares.

| Evaluation order | Microsoft option | Use case |
|---|---|---|
| 1 | `e159e079-0ddd-4905-97c9-79a8fca6d880` — existing vault, exclude a tag | Central-vault `DeployIfNotExists` coverage for eligible shares |
| 2 | `cfc5190a-3b19-4a23-b563-a4c719b666e4` — audit | Microsoft-managed report-only coverage signal |
| 3 | `d8659d5a-a3bd-444d-98ea-570bceb568cf` — existing vault, include a tag | Opt-in central-vault coverage |
| 4 | `9e47cad9-bcdb-42dd-8c9b-a81e15ca2842` / `f32ca068-2ada-4705-b5b5-84ce89422846` — create a vault | Application-owned vault patterns; review the fixed defaults first |
| 5 | [Daily](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share) / [hourly](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.recoveryservices/recovery-services-backup-file-share-hourly) Quickstarts | Reference deployment for one explicitly named share; not all-share enforcement |
| 6 | [Azure Verified Recovery Services vault module](https://github.com/Azure/terraform-azurerm-avm-res-recoveryservices-vault) | Terraform-managed vault, policy, and explicit protected-share maps |

The five Azure Policy definitions above are currently Preview. Follow the
[Azure Files enforcement guide](docs/GUIDE-file-share-backup-enforcement.md)
for prerequisites, RBAC, canary rollout, and tested boundaries.

## Custom policy catalog

Review the Microsoft options above before deploying these definitions. The
custom catalog is valuable when you deliberately need repository-owned policy
artifacts and a single assignment surface.

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
| 13 | `audit-file-share-backup-protection` | Operations | AuditIfNotExists | Flags unprotected SMB shares; optional custom fallback for Microsoft's Preview audit policy |
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
infra/               Tenant-neutral retained-showcase Bicep fixture
docs/DESIGN.md       Design decisions, sources, known limitations, rollout SOP
docs/REPLICATE-*     Complete Policy + Automation replication runbook
.github/workflows/   CI: validation on PRs and pushes to main
```

Each policy file is a complete, self-describing definition: `displayName`,
`description` (including *why* the control matters), semantic `version`,
`mode`, declared `parameters`, and the `policyRule`.

## Replicate the complete retained showcase

The [Policy + Automation replication runbook](docs/REPLICATE-POLICY-AUTOMATION.md)
starts from an empty resource group, stages this initiative in report-only
mode, proves nonempty compliance state, promotes and remediates it, then adds
the sibling Azure Backup Smart Tiering Automation canary. It finishes with the
exact portal inspection surfaces and least-privilege state to leave alive, and
uses tenant-neutral variables rather than checked-in subscription or principal
identifiers.

## Custom baseline quickstart

Use this flow only after deciding that the repository-owned initiative is the
right fit. For Azure Files backup, follow the Microsoft-first recommendation
above before deploying the custom fallback.

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
./scripts/unassign.sh --name enterprise-baseline --scope "/subscriptions/<id>"
./scripts/undeploy.sh --subscription "<id>"
```

Resource-group assignments use the full RG resource ID as `--scope`. These
commands remove policy and RBAC objects; application resources remain under
your normal lifecycle tooling.

## Live validation

The latest joint qualification retained a resource-group-scoped Policy +
Automation showcase after Policy report-only/enforcement/remediation and a
fresh Automation audit/apply/idempotence cycle. Its tenant-neutral outcomes,
least-privilege final state, and explicit limits are in
[the 2026-08-31 joint validation record](docs/LIVE-TEST-2026-08-31-policy-automation.md).

For Azure Files backup, begin with the Microsoft-first
[enforcement guide](docs/GUIDE-file-share-backup-enforcement.md). It covers the
service-owned Preview policies, official templates, the optional custom audit
fallback, and the reconciliation automation.

The original 15-policy custom baseline was server-validated and exercised in
a disposable Azure subscription on 2026-07-16. The test matrix, platform
caveats, fixes found, and cleanup assertions are recorded in
[docs/LIVE-TEST-2026-07-16.md](docs/LIVE-TEST-2026-07-16.md). The
optional `audit-file-share-backup-protection` custom fallback was added later
and validated separately on 2026-07-28 — see
[docs/LIVE-TEST-2026-07-28-file-share-backup.md](docs/LIVE-TEST-2026-07-28-file-share-backup.md)
and the step-by-step
[file-share backup audit guide](docs/GUIDE-file-share-backup-audit.md).

## Validating changes

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

The validator checks JSON syntax, required properties, name/filename
agreement, that every parameter is both declared and used, that `count`
expressions carry a comparison operator, that Modify/DeployIfNotExists
policies declare `roleDefinitionIds` and deployment details, and that the
initiative's references, parameter bindings, and coverage all line up with the
definitions on disk. The unit suite also exercises the Azure Files backup
reconciler's subscription-aware item correlation, account-level vault
registration, soft-delete and SMB/NFS handling, safety caps, and apply/wait
behavior. Both checks run in GitHub Actions for pull requests and pushes to
`main`; CI also runs the public-content privacy guard, compiles the retained
Bicep fixture, and syntax-checks every shell script and example JSON file.

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

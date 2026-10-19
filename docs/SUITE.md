# Azure governance suite

This page is the entry point for a small, live-tested Azure governance suite:
Azure Policy for request-time and continuous controls, Azure Automation
runbooks for guarded changes that Policy cannot express, and one
reconciliation script for immediate Azure Files backup coverage. Each part is
usable on its own. Start with the row that matches the outcome you need.

| Component | Where | Kind | What it does | Start here | Latest evidence |
|---|---|---|---|---|---|
| Enterprise Policy Baseline | this repository: `policies/`, `initiatives/` | 16 Azure Policy definitions + one initiative | Deny, Audit, Modify and DeployIfNotExists guardrails for storage, network, Key Vault, SQL, tags, regions, VM SKUs, backup and identity | [README quickstart](../README.md#custom-baseline-quickstart), then the [Policy + Automation canary](REPLICATE-POLICY-AUTOMATION.md) | 2026-09-27, new tenant ([record](LIVE-TEST-2026-09-27-new-tenant.md)) |
| Azure Files backup reconciler | this repository: `scripts/ensure_file_share_backup.py` | Python + Azure CLI script | Immediately protects unprotected classic SMB file shares in one vault and reports coverage; safe dry-run by default | [Azure Files enforcement guide](GUIDE-file-share-backup-enforcement.md) | 2026-10-19, new tenant ([record](LIVE-TEST-2026-10-19-new-tenant.md)) |
| Azure Backup smart tiering | [azure-backup-smart-tiering-automation](https://github.com/kevo099/azure-backup-smart-tiering-automation) | Azure Automation runbook (PowerShell) | Sets `TierRecommended` on eligible Azure VM backup policies, one guarded ring at a time | its [replication guide](https://github.com/kevo099/azure-backup-smart-tiering-automation/blob/main/docs/replicate-in-azure.md) | 2026-10-19 health check; 2026-09-27 protected-VM write ([record](https://github.com/kevo099/azure-backup-smart-tiering-automation/blob/main/docs/LIVE-TEST-2026-09-27.md)) |
| Blob Storage smart tier | [azure-storage-smart-tier-automation](https://github.com/kevo099/azure-storage-smart-tier-automation) | Azure Automation runbook (PowerShell) | Sets `accessTier=Smart` on opted-in, eligible storage accounts after audit | its [replication guide](https://github.com/kevo099/azure-storage-smart-tier-automation/blob/main/docs/replicate-in-azure.md) | 2026-10-19, new tenant, full walkthrough ([validation](https://github.com/kevo099/azure-storage-smart-tier-automation/blob/main/docs/validation.md)) |

The [Policy + Automation canary](REPLICATE-POLICY-AUTOMATION.md) combines the
baseline with the Azure Backup runbook in one owned resource group and leaves a
small showcase running for inspection.

## Which tool when

| Need | Use | Why |
|---|---|---|
| Block a bad configuration at request time | Azure Policy `Deny` | Evaluated before the resource is written; nothing to clean up |
| Report drift without changing anything | Azure Policy `Audit` / `AuditIfNotExists` | Continuous, read-only, visible in Policy compliance |
| Fix a property Azure Policy can modify (tags, some flags) | Azure Policy `Modify` + remediation | Applies at request time and backfills existing resources |
| Create a companion resource (diagnostic settings) | Azure Policy `DeployIfNotExists` + remediation | The policy identity deploys the missing resource |
| Change something Policy cannot reach, or that needs judgement, guards and verification | Azure Automation runbook (this suite's runbooks) | No policy alias exists for backup-policy tiering; blob Smart tier is a priced change that should stay opt-in and audited first |
| Close a coverage gap immediately, then keep checking | Reconciliation script on a schedule | Policy evaluation and remediation are asynchronous; a reconciler gives an immediate, independently checked result |
| Build new resources | Bicep/Terraform, Azure Verified Modules, Microsoft Quickstarts | Deployment is not governance; govern the result with Policy |

Prefer a Microsoft built-in policy or template when it expresses the control.
The README's [recommended order of use](../README.md#recommended-order-of-use)
explains when this repository's custom definitions are the better choice.

## Glossary

- **Azure Backup smart tiering** moves eligible *backup recovery points* to the
  vault-archive tier (`tieringPolicy.ArchivedRP.tieringMode` on a Recovery
  Services backup policy). It is unrelated to blob access tiers.
- **Blob Storage smart tier** is the storage account access tier `Smart`
  (`properties.accessTier`), which lets Azure manage hot/cool/cold placement of
  blobs. It is unrelated to backup recovery points.
- **Ring** is a deliberately small set of targets changed in one window; the
  runbooks change one named ring at a time and verify before the next.
- **Canary** is a disposable owned resource group used to prove a change
  before it reaches shared scopes.

## Evidence index

Records are sanitized: they contain counts, outcomes, source commits and
limitations, never tenant, subscription or principal identifiers.
"Sponsorship tenant" is the original Microsoft for Startups subscription
(decommissioned in October 2026); "new tenant" is the current pay-as-you-go
tenant.

| Date | Tenant | Record | What it proved |
|---|---|---|---|
| 2026-07-16 | sponsorship | [LIVE-TEST-2026-07-16](LIVE-TEST-2026-07-16.md) | Deny/Audit/Modify/DINE behaviour of the original definitions; teardown |
| 2026-07-28 | sponsorship | [LIVE-TEST-2026-07-28-file-share-backup](LIVE-TEST-2026-07-28-file-share-backup.md) | File-share backup audit noncompliant → compliant |
| 2026-08-25 | sponsorship | [LIVE-TEST-2026-08-25-file-share-backup-enforcement](LIVE-TEST-2026-08-25-file-share-backup-enforcement.md) | Reconciler protection, recovery points and an alternate-location restore |
| 2026-08-31 | sponsorship | [LIVE-TEST-2026-08-31-policy-automation](LIVE-TEST-2026-08-31-policy-automation.md) | Joint Policy remediation and Automation audit/apply/idempotence |
| 2026-09-06 | sponsorship | [LIVE-TEST-2026-09-06-policy-automation](LIVE-TEST-2026-09-06-policy-automation.md) | The published canary guide, with recoveries for observed propagation issues |
| 2026-09-27 | new | [LIVE-TEST-2026-09-27-new-tenant](LIVE-TEST-2026-09-27-new-tenant.md) | Guide sections 1–8, validation-only Deny probes, a protected D-series VM |
| 2026-10-19 | new | [LIVE-TEST-2026-10-19-new-tenant](LIVE-TEST-2026-10-19-new-tenant.md) | Reconciler protect/backup/restore, Microsoft's Azure Files DINE built-in, NSG Deny 3.0.0 probes, retained-showcase health |

The Automation repositories keep their own records (`docs/validation.md` and
`docs/LIVE-TEST-*.md`).

## Tested tooling and known platform quirks

| Item | Tested | Notes |
|---|---|---|
| Azure CLI | 2.89–2.91 | 2.90+ trims trailing newlines when expanding `--content @file`/`--body @file`; the backup publisher uploads binary bytes instead. 2.91 cannot resolve a full built-in policy ID such as `/providers/Microsoft.Authorization/policyDefinitions/<guid>` in `az policy assignment create --policy`; pass the definition GUID name. |
| Azure CLI `automation` extension | 1.0.0b2 | Pinned by the guides |
| Automation runtime | PowerShell 7.4 runtime environment; both runbooks also verified on 7.6 (2026-10-19) | Microsoft ends PowerShell 7.4 support on 2026-11-10. Both runbooks completed read-only audits unchanged on PowerShell 7.6 runtime environments; switching the templates to 7.6 is the next release item. |
| Azure Policy remediation | `ExistingNonCompliant` default mode | A remediation created immediately after changing an assignment can finish with zero deployments because the only noncompliant records predate the change. Trigger a scan, wait for a record newer than the change, and require `totalDeployments > 0`. |
| Recovery Services vaults | soft delete `AlwaysOn` | New vaults cannot disable soft delete; a deleted item is retained for 14 days and the vault can be deleted once those items purge. |

## Open items

- `deny-nsg-open-management-ports` 3.0.0 now covers inline rules and port
  ranges (live-probed 2026-10-19). Its source test is still a literal list; see
  [known limitations](DESIGN.md#known-limitations).
- Two further policies (Azure Hybrid Benefit for Windows Server and a blob
  smart-tier policy) are being prepared as optional, audit-by-default add-ons.
  Azure Hybrid Benefit attests license ownership; never enable a `Modify`
  effect without eligible Windows Server licenses with Software Assurance.
- Runbook templates will default to the PowerShell 7.6 runtime; both runbooks
  already passed read-only audits on 7.6.

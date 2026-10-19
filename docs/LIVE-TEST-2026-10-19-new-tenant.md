# Live test — 2026-10-19, new tenant

This record covers the Azure Files backup tooling and a health check of the
retained Policy + Automation showcase in the current pay-as-you-go tenant. It is
sanitized: it contains outcomes, counts and source commits, never tenant,
subscription, principal or resource identifiers.

## Environment and sources

- Azure CLI 2.91.0 with the `automation` extension 1.0.0b2; Python 3.14.
- Region East US 2 for every resource in this record.
- Repository `main` at `0c893e5` for the test tracks (`scripts/ensure_file_share_backup.py`
  and `examples/azure-files-backup-policy.json` unchanged since their last live test). The
  NSG track used a test-named copy of the proposed 3.0.0 rule.
- Each test track used its own new resource group and changed no shared resource. The one
  shared change came last: after 3.0.0 was merged, the showcase's subscription-scope
  definitions were republished from `main` at `3fc15ff`, which moved
  `deny-nsg-open-management-ports` from 2.0.0 to 3.0.0 (see the end of the NSG section).

## Retained showcase health check

The showcase from the [2026-09-27 run](LIVE-TEST-2026-09-27-new-tenant.md) was
still present and enforced. One read-only, unfiltered resource-group audit job
of the Azure Backup runbook (`Apply=false`) completed: three vaults, twelve
backup policies classified (nine VM policies, three log-backup policies skipped
as unsupported workloads), both canaries `AlreadyCompliant`, zero writes, zero
errors.

The extra vault left by the 2026-09-27 protected-VM extension held no items or
containers once its soft-deleted backup item had purged after the 14-day
retention. Deleting it succeeded, which settles the open question from that
run: with soft delete `AlwaysOn`, a vault can be deleted once its soft-deleted
items purge. The showcase is back to the guide's ten resources.

## Azure Files reconciliation

Fixture: one StorageV2 LRS account with two 1 GiB SMB shares, a Recovery
Services vault (LRS) in the same region, and an Azure Files backup policy created
from `examples/azure-files-backup-policy.json`. A small random canary file was
written to the first share and its SHA-256 recorded.

| Step | Result |
|---|---|
| Dry-run | Exit `2`; two `ENABLE` actions, zero blockers, zero NFS exceptions |
| First `--apply` (`--max-actions 2 --max-new-registrations 1`) | Exit `1`. Azure Backup's storage-account `Register` job failed with `CloudInternalError` (error code 1073871825, "wait a few minutes and then try the operation again"). Nothing was registered or protected; the script stopped before any share action |
| Same `--apply` about five minutes later | Exit `0`. Registration and both `ConfigureBackup` jobs completed; final state `INITIAL_RECOVERY_PENDING`, health `Healthy` |
| Repeat dry-run | Exit `0`; no further actions |
| On-demand backup of the canary share | Completed; one `FileSystemConsistent` recovery point |
| Item-level alternate-location restore of the canary file into the second share | Completed: one file restored, zero failed. The downloaded file's SHA-256 matched the original |

The first apply failure was a transient service error, and the script handled it
as designed by failing closed. It does not retry automatically; the
[enforcement guide](GUIDE-file-share-backup-enforcement.md#immediate-audit-and-reconciliation)
now documents the rerun.

## Microsoft's existing-vault DeployIfNotExists built-in

A third, unprotected SMB share was created. The built-in definition
`e159e079-0ddd-4905-97c9-79a8fca6d880` (version `2.0.0-preview`, parameters
unchanged from the guide) was then exercised through the guide's assignment
path, which earlier tests could not complete because their identity could not
grant roles.

| Step | Result |
|---|---|
| `az policy assignment create --policy /providers/Microsoft.Authorization/policyDefinitions/e159e079-…` | Failed in Azure CLI 2.91.0: `PolicyDefinitionNotFound`, then a client crash. The CLI parses the tenant-level ID as a management-group path. Nothing was created |
| Same command with `--policy e159e079-0ddd-4905-97c9-79a8fca6d880` | Assignment created with `DoNotEnforce` and a system-assigned identity |
| Two role grants (Storage Account Contributor at the source scope, Backup Contributor at the vault resource group) | Created |
| Scan in report-only mode | Nonempty state: two shares `Compliant`, the new share `NonCompliant` |
| Promote to `Default`, then remediation created seconds later | `Succeeded` with **zero** deployments, created and finished in the same second |
| New scan, wait for a noncompliant record newer than the promotion, new remediation | `Succeeded`, one of one deployment; the third share entered `IRPending` |
| Reconciler dry-run | Exit `0`; all three shares counted as covered (one `PROTECTED`, two `INITIAL_RECOVERY_PENDING`) |

Two guide corrections came from this: pass built-in definitions by GUID name,
and scan after promotion before creating an `ExistingNonCompliant` remediation,
then require at least one deployment. Both are now in the
[enforcement guide](GUIDE-file-share-backup-enforcement.md#continuous-enforcement-with-azure-policy).

## NSG management ports 3.0.0

The corrected `deny-nsg-open-management-ports` rule was created as a test-named
definition (same rule, parameters and mode as 3.0.0) and assigned with `Deny`
to one new resource group, leaving the shared definition used by the showcase
untouched. The assignment was effective about 30 seconds after creation. Each
probe was an `az deployment group validate` request; a denial counted only when
Azure returned `RequestDisallowedByPolicy` naming the test assignment.

| Request shape | Expected | Result |
|---|---|---|
| Inline rule, 22 from `*` | deny | deny |
| Inline rule, 3389 from `::/0` | deny | deny |
| Inline rule, `1-65535` from `*` | deny | deny |
| Inline rule, `20-25` from `Internet` | deny | deny |
| Inline rule, `destinationPortRanges` `["80","3389"]` from `*` | deny | deny |
| Inline rule, port `*` from `0.0.0.0/0` | deny | deny |
| Inline rule, 22 from `sourceAddressPrefixes` `["10.0.0.0/8","0.0.0.0/0"]` | deny | deny |
| Inline rule, port `022` from `*` | deny | deny |
| Inline NSG with a compliant 443 rule and a 3389-from-`*` rule | deny | deny |
| Child rule, 22 from `*` | deny | deny |
| Child rule, `1-65535` from `*` | deny | deny |
| Child rule, `20-25` from `Internet` | deny | deny |
| Child rule, `["80","3389"]` from `*` | deny | deny |
| Inline rule, 443 from `Internet` | allow | allow |
| Inline rule, 22 from `VirtualNetwork` | allow | allow |
| Inline rule, `23-80` from `*` | allow | allow |
| Inline rule, 22 from `*` with access `Deny` | allow | allow |
| Inline rule, 22 from `*` outbound | allow | allow |
| Child rule, 443 from `Internet` | allow | allow |
| Child rule, `3390-4000` from `*` | allow | allow |
| NSG with no custom rules | allow | allow |

All 21 matched, including the four shapes that versions before 3.0.0 allowed on
2026-09-27 (inline 22 and 3389, child `1-65535` and `20-25`). Two real requests
confirmed request-time behaviour: an NSG with an inline 22-from-`*` rule was
denied and nothing was created; an NSG with an inline 443-from-`Internet` rule
was created and then deleted. The probe resource group held no probe NSG
afterwards.

After 3.0.0 was merged, `scripts/deploy.sh` republished the showcase's
definitions from `main` at `3fc15ff`; of the shared definitions, only
`deny-nsg-open-management-ports` changed (2.0.0 to 3.0.0). The next scan evaluated the showcase NSG and its child
rule `Compliant`. The only noncompliant resource left in the showcase was its
intentionally unprotected file share, as expected.

## Cleanup

- NSG track: the test assignment and test definition were deleted, then the
  probe resource group.
- Azure Files track: the built-in's assignment and the two role grants it
  received were removed, so nothing is enforced there any more. Protection was
  then stopped on all three shares with their backup data deleted; each item
  reported `ProtectionStopped` and scheduled for deferred deletion. With soft
  delete `AlwaysOn` the recovery points are kept 14 days, so the empty vault
  and the storage account (still carrying Azure Backup's `CanNotDelete` lock)
  can only be deleted after that retention ends.

## Limits of this evidence

- No NFS share was created, so the reconciler's NFS exception path was not
  re-run live (it remains covered by unit tests).
- Only one transient registration failure was observed; the rerun behaviour is
  proven for that case only.
- The built-in remains a Preview definition. The test proves its current
  behaviour in one canary resource group, not at subscription scale.
- A restore from the vault tier was not tested; the restore used the snapshot
  recovery point.
- The NSG probes used validation requests plus two real requests in one
  resource group; split public prefixes such as `0.0.0.0/1` plus `128.0.0.0/1`
  remain outside the rule by design (see known limitations).

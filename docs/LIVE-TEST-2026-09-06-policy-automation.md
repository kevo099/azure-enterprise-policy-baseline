# Policy + Automation live test (2026-09-06)

**Policy verification passed at 05:38 UTC, combined Automation verification
passed at 05:56 UTC, and cleanup and preservation checks passed by 06:14 UTC.**
This record preserves the first attempt's findings and the completed second
attempt. The shared-definition adoption boundary below applies to this run; the
[2026-08-31 qualification](LIVE-TEST-2026-08-31-policy-automation.md) remains a
separate record.

Only tenant-neutral outcomes appear here. Live names, identities, resource and
job IDs, credentials, and raw responses remain in private evidence.

## Sources and validation

- Original guide tested: `c37d119f664d046a371ba8f63ad0a5f002e8aa3a`.
- Corrected guide used for the second attempt:
  `00a7eb9eeb91b6025eca2ddf2da071cb319ad811`.
- Qualified Policy core used for comparison:
  `d07fe194b46a4b1df9f20e04f6454dc1f3f81148`.
- Corrected shared Automation helper:
  `03839a29b0fff02442d88a414d7ac32851d227c7`.
- Unchanged Policy fixture SHA-256:
  `ee6a382443881993fea49ef25f9c6c89ecaff38addb1faa04180b22fc5f0eaad`.
- Unchanged Backup runbook SHA-256:
  `2cef45acc81b04a6bbcd62582db6f974102ae98f2de79231a90907f49a7dd555`.

Azure CLI 2.90.0 was used. All 50 local tests passed, including the 12 guide
regressions. All 16 definitions and the initiative validated, the fixture
compiled, and the staged Bash phases parsed. The live phases used the unchanged
assignment helper and current guide commands, with fresh names and a test-owned
group/tag value. Existing CLI
credentials and account selection were retained.

## First attempt and corrected defects

The first attempt created and verified the capped workspace, then stopped before
assignment because the caller lacked the required role-management operations.
That workspace was deleted, the group was verified empty, and all 17 shared
Policy artifacts were confirmed unchanged. The initial partial result remains
historical evidence; it is not evidence of a completed assignment or remediation.

The final-state wait in the original section 8 could accept compliant rows from
the earlier Policy fixtures before the new Automation Account and two vaults
appeared. Its immediately following exact-ID assertion could then fail during
normal result propagation. The helper now takes required resource IDs, and
section 8 passes all three new resources into the bounded poll. Section 5 retains
its original Policy-only behavior.

| Offline response sequence | Verified result |
|---|---|
| Only earlier compliant fixture rows | All 40 polls used, then timeout |
| New resource rows arrive one at a time | Accepted only when all three are present |
| Complete Policy-only state, with no extra IDs required | Accepted on the first response |

These regressions prove the changed predicate. The second attempt also captured
the actual live race at 05:54 UTC: Policy Insights still returned the 21 earlier
rows, the old Policy-only predicate would accept them, and none of the Automation
Account or two vaults had the required compliant tag row. The corrected predicate
kept waiting on that same saved response. It subsequently accepted 29 rows only
after all three exact resource records arrived. The combined verification passed
at 05:56 UTC. This proves the old acceptance race against real service results
and the corrected gate's live convergence; no failure was manufactured by
rerunning the old helper. Microsoft documents asynchronous scans without a fixed
completion guarantee; empty or incomplete state cannot establish compliance. [Policy evaluation guidance](https://learn.microsoft.com/en-us/azure/governance/policy/how-to/get-compliance-data#evaluation-triggers)

The separate Backup walkthrough also exposed Azure CLI 2.90.0 trimming the final
newline during runbook `@file` expansion. The original publisher rejected the
hash mismatch. The corrected helper sends a binary HTTP body and verified equal
draft/published hashes against the unchanged runbook in the standalone test.
This guide pins that correction. The second attempt additionally passed the
combined publication and final runtime/hash checks described below. [Backup publication test record](https://github.com/kevo099/azure-backup-smart-tiering-automation/blob/aa2c203c0a4fe645b03120f1ca341307362f3d7b/docs/LIVE-TEST-2026-09-06.md)

## Shared-definition adoption boundary

The subscription already held the fixed-name definitions and initiative. This
exercise created a new group-scoped assignment against reviewed existing
artifacts. Neither `deploy.sh` nor `undeploy.sh` was invoked. Fresh publication
of the shared definitions is outside this run's qualification.

The second preflight required exact equality of all deployment-owned fields in
the 16 custom definitions. The initiative had the same narrowly accepted
service enrichment as the first attempt: every reference included
`definitionVersion: "1.*.*"`, while each referenced custom definition reported
one service version, `1.0.0`. A separate comparison required that exact shape and
equality of every remaining authored initiative field. This establishes current
resolved-rule equivalence, not raw equality with the source JSON.

All 17 raw hashes in the second attempt's preflight matched the first attempt's
preserved artifacts. After the owned group was verified absent, a fresh capture
again matched all 17 preflight raw hashes. The authored-field and narrowly
accepted service-version comparison also passed again. The shared definitions
and initiative remained unchanged throughout both attempts.

## Completed second-attempt Policy stages

The administrator supplied temporary authority at the owned test group. Before
writes resumed, current permissions were checked for role-definition and
role-assignment read/write/delete. Role visibility alone was not treated as
proof of authority. The workflow needs role-definition administration as well
as assignment administration; RBAC Administrator alone does not supply both.
[Microsoft privileged role definitions](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged)

| Stage | Observed result |
|---|---|
| Capped Log Analytics workspace | `Succeeded`; `PerGB2018`; 30-day retention; 0.1 GB/day quota |
| Fresh report-only assignment | `DoNotEnforce`, new system-assigned identity; both selected regions allowed |
| Remediation identity access | Exactly three expected grants: Contributor and Monitoring Contributor at the owned group, Log Analytics Contributor at the exact workspace |
| Fresh sanitized fixture | Deployment `Succeeded`; no VM or public IP introduced |
| Report-only evaluation | 21 rows: 13 compliant, six missing inherited tags, one missing diagnostics setting, one intentionally unprotected share |
| Promotion | Same assignment and identity updated to `Default`; grants remained effective |
| Tag Modify remediation | `Succeeded`; 6/6 deployments successful; zero failures |
| Key Vault diagnostics remediation | `Succeeded`; 1/1 deployment successful; zero failures |
| Final Policy-only evaluation | 21 rows: 20 compliant and the one intentional unprotected-share finding |
| Independent resource checks | All six exact resources carried the expected inherited tag; exact Key Vault diagnostic setting targeted the workspace with audit logs and `AllMetrics` enabled |

The workspace quota limits ingestion, not the test's total spend. The fixture's
empty, unprotected share is an intentional audit example; this run did not test
Azure Files backup or restore. Applicable Deny policies reported the compliant fixture as compliant; no
negative Deny requests were executed in this attempt.

The nonempty report-only gate rejected both empty results and an incomplete
12-row response before all three intended signals arrived. Both remediation
tasks then spent about 17–18 minutes in Azure's processing path before finishing
within the original bound. No task or scan was resubmitted to accelerate them.
After remediation succeeded, the final poll also waited through stale findings
and a partial refresh before accepting the final state. Direct resource checks
then passed independently of Policy Insights.

`ReEvaluateCompliance` starts a new scan for the assignment. Evaluation duration
varies, and remediation acceptance alone does not prove deployment success.
The test required terminal success, positive deployment counts, zero failures,
updated Policy state, and direct resource checks. [Remediation task structure](https://learn.microsoft.com/en-us/azure/governance/policy/concepts/remediation-structure),
[Policy-as-code validation guidance](https://learn.microsoft.com/en-us/azure/governance/policy/concepts/policy-as-code)

## Completed combined verification and cleanup

The fresh combined Automation fixture passed publication and its exact reader,
audit, apply and repeat jobs. All four jobs completed. The apply submitted and
verified one intended policy write; the repeat performed zero writes. The table
records the qualification state before teardown.

| Combined stage | Observed result |
|---|---|
| Publication | Published PowerShell 7.4 runbook; final content SHA-256 equal to the qualified source |
| Reader readiness | Exact target policy already compliant; zero writes |
| Seed and audit | Exact empty policy seeded to DoNotTier; audit found the one intended candidate and submitted zero writes |
| Bounded apply | One submitted and verified write; target returned to TierRecommended |
| Idempotent repeat | Already compliant; zero writes |
| Other policy properties | No non-tiering drift; both vault policies finished TierRecommended |
| Protected items | Both vaults contained zero protected items |
| Final combined Policy state | 29 rows: 28 compliant and the one intentional unprotected-share finding |
| New-resource checks | Automation Account and both vaults each had their exact compliant tag row and expected inherited tag |
| Runtime and job state | Four Completed jobs; no schedules or job schedules |
| Final Automation access | One exact group-scoped discovery reader; temporary writer grant and definition absent |
| Final Policy access | Same enforced assignment with its three exact remediation grants |

| Cleanup stage | Verified result |
|---|---|
| Automation reader, Policy identity grants and assignment | Removed; three consecutive role-list and Policy-assignment absence checks passed |
| Temporary caller authority | Removed and verified absent |
| Owned group | Deletion completed; independent group-existence check returned false |
| Soft-delete records | Exact Key Vault tombstone and deleted-workspace record observed |
| Shared definitions and initiative | All 17 raw hashes matched preflight; resolved-version comparison still passed |

After combined verification, coordinated cleanup removed the Automation reader,
the three Policy identity grants and the Policy assignment. Independent checks
confirmed zero grants for both saved managed identities, no owned custom roles
and no Policy assignment in three consecutive observations. The temporary caller
authority was then removed, the group was deleted, and its actual absence was
verified. This order removed Automation roles before its identity and Policy
identity grants before its assignment, while retaining caller authority until
role-dependent cleanup finished.

The deleted Key Vault record showed purge protection enabled, deletion at
06:09:59 UTC, and scheduled purge seven days later on 2026-09-13. The exact
Log Analytics workspace also appeared in the deleted-workspace inventory.
Microsoft documents its 14-day recovery period and name reservation. These
retained recovery records are separate from active-resource absence; no force
purge or recovery was performed. [Key Vault recovery](https://learn.microsoft.com/en-us/azure/key-vault/general/key-vault-recovery),
[Log Analytics deletion and recovery](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/delete-workspace)

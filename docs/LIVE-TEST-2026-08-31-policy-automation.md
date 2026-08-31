# Joint Azure Policy + Automation qualification (2026-08-31)

This record contains only allowlisted, tenant-neutral evidence. Tenant,
subscription, principal, resource, assignment, remediation, deployment, and
job identifiers are intentionally omitted. Raw evidence remains private.

## Sources under test

| Component | Source |
|---|---|
| Policy core | `azure-enterprise-policy-baseline` commit `d07fe194b46a4b1df9f20e04f6454dc1f3f81148` |
| Automation replication tooling | `azure-backup-smart-tiering-automation` commit `67ecfe0f5a3bebbac472fc629aa1911846180056` |
| Automation runbook | version 1.1.0, SHA-256 `2cef45acc81b04a6bbcd62582db6f974102ae98f2de79231a90907f49a7dd555` |

The test used one new resource-group-scoped Policy assignment and one fresh
Automation replica. Subscription-scoped custom Policy definitions and the
initiative were published once; enforcement was not broadened beyond the
canary RG.

## Offline and GitHub checks

- Policy validator: 16 definitions and one complete initiative.
- Policy unit suite: passed on the source branch used for the review.
- Automation static/parser validation: passed.
- Automation behavioral harness: 45 of 45 scenarios passed under PowerShell
  7.4.
- GitHub Actions on both source branches: passed.
- Published runbook fetch-back: content hash matched the reviewed public
  source under the then-current helper's normalized fetch method. The newer
  replication helper performs a raw-payload exact-byte comparison.

## Policy live results

The assignment was first created with `enforcementMode: DoNotEnforce`.
Policy Insights returned nonempty state before promotion; empty state was not
accepted as compliance.

| Control | Result |
|---|---|
| Storage HTTPS | A validation-only insecure request was denied. |
| Storage minimum TLS | A TLS 1.0 validation request was denied. |
| Anonymous blob access | A public-blob validation request was denied. |
| NSG management ports | Internet-to-SSH and IPv6-any-to-RDP validation requests were denied; the retained HTTPS rule was compliant. |
| NIC public IP | A validation-only public-IP NIC was denied; the retained NIC was private-only. |
| SQL public network | A validation-only public SQL logical server was denied; no SQL resource remained. |
| Key Vault purge protection | A purge-disabled request was denied; the retained vault had purge protection and seven-day soft delete. |
| Required RG tag | An empty required-tag update was denied. |
| Tag inheritance | Request-time modification worked; two existing-resource remediations completed 5/5 and 4/4 with zero failures. Policy Insights reported all 10 retained tag targets compliant. |
| Allowed locations | A disallowed-region request was denied; both deliberately selected showcase regions were allowed. |
| Allowed VM SKUs | A disallowed SKU was denied. The ephemeral validation VM used an allowed SKU available in the test subscription. |
| Managed disks | A crafted legacy unmanaged-OS-disk request was denied. |
| VM backup audit | The short-lived, intentionally unprotected VM was noncompliant. |
| VM identity audit | The same VM moved from noncompliant to compliant after a system-assigned identity was added. |
| File-share backup audit | The intentionally unprotected empty SMB share was noncompliant. This proves existence detection only. |
| Key Vault diagnostics DINE | Remediation completed 1/1 with zero failures. Audit logs and all metrics targeted the exact canary workspace; Policy Insights reported compliant. |

The VM and its disk were deleted after audit evidence. No public IP or SQL
resource was created by the retained fixture.

## Fresh Automation live results

Two empty Recovery Services vaults each contained one target empty Azure VM
backup policy alongside Azure's service-created defaults. The exact-name
resource-group canary followed this sequence:

| Phase | Terminal action | Candidate / submitted / written | Result |
|---|---|---:|---|
| Audit | `WouldEnableTierRecommended` | `1 / 0 / 0` | Completed |
| Bounded apply | `EnabledAndVerified` | `1 / 1 / 1` | Completed; synchronous HTTP 200 |
| Idempotent repeat | `AlreadyCompliant` | `0 / 0 / 0` | Completed |

All three jobs reported one matched policy, zero errors, zero failed or
unknown writes, and no abort reason. Before/after documents had equal hashes
after removing only the intended `ArchivedRP` tiering member and volatile ETag.
Both policies finished `TierRecommended`, both still protected zero items, and
the Automation Account retained zero schedules.

The fresh no-reader job failed closed before its result/summary phase. The
underlying policy remained unchanged and no write was submitted, but there was
no `SUMMARY` line. This caught an overstatement in the public Automation docs:
parameter binding, script-level validation, token/context initialization, and
top-level discovery can all fail before summary construction.

## Retained least-privilege state

- The enforced Policy assignment remains scoped to only the showcase RG.
- Its system-assigned identity retains Contributor and Monitoring Contributor
  only at that RG, plus Log Analytics Contributor only at the workspace.
- The Automation identity retains only the custom discovery-reader role at the
  showcase RG.
- The Automation policy-writer assignment and temporary human role-assignment
  administration grant were removed.
- The Policy assignment, remediated Key Vault diagnostic setting, remediation
  history, Automation Account, PowerShell 7.4 runtime, Published runbook, two
  empty vaults, and two empty canary policies remain alive for inspection.
- No protected item, Automation schedule, VM, public IP, or SQL resource
  remains.

## Limits

This qualification did not exercise archive movement, a protected-item policy
write, restore from vault archive, Resource Guard/MUA, throttling, or an
asynchronous 202 write. Those paths remain outside the retained empty-canary
claim. Regional quota and SKU availability are subscription-specific.

Use the [complete replication runbook](REPLICATE-POLICY-AUTOMATION.md) to build
the same retained shape with fresh destination-generated names and identities.

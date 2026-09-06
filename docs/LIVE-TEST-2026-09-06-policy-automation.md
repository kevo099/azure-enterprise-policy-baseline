# Policy + Automation test preparation (2026-09-06, provisional)

**Preparation passed; end-to-end live qualification is pending Azure RBAC.**
This record covers the completed workspace preparation and an independently
reproduced documentation defect. It does not replace the completed
[2026-08-31 qualification](LIVE-TEST-2026-08-31-policy-automation.md).

Only tenant-neutral outcomes appear here. Live names, subscription and principal
identifiers, resource IDs, job IDs, raw responses, and access details remain in
private evidence.

## Sources and completed preparation

- Guide under test before the fix: commit
  `c37d119f664d046a371ba8f63ad0a5f002e8aa3a`.
- Qualified Policy core used for comparison: commit
  `d07fe194b46a4b1df9f20e04f6454dc1f3f81148`.
- Local validation passed for 16 definitions and one initiative. The unchanged
  Policy showcase Bicep compiled, and the staged Bash phases passed syntax checks.
- Azure CLI 2.90.0 read the existing 16 custom definitions and initiative.
  All 16 definitions matched the deployment-owned source fields.
- A fresh, owned test resource group was prepared. Its Log Analytics workspace
  reached `Succeeded`; direct readback confirmed `PerGB2018`, 30-day retention,
  and a 0.1 GB/day ingestion quota. The quota is not a total spending limit.

## Shared-definition adoption boundary

The subscription already contained the fixed-name definitions. This test
therefore prepares a new resource-group assignment against reviewed existing
definitions instead of invoking the guide's fresh-only publication step.
Neither `deploy.sh` nor `undeploy.sh` was run against those shared objects.

The live initiative differed from the source only by an additional
`definitionVersion: "1.*.*"` on each of its 16 references. Each corresponding
custom definition reported one service version, `1.0.0`. A separate comparison
required that exact version shape and equality of all remaining authored
initiative fields. This establishes the currently resolved rule equivalence;
it is not raw source-byte equality or proof of fresh definition publication.
The raw before state is retained privately for a later unchanged-state check.

## Section 8 convergence defect and fix

The original final-state wait could return while Policy Insights still showed
only the earlier compliant Policy fixtures. Section 8 then immediately checked
for the newly created Automation Account and two vaults, so normal arrival of
their records could cause a premature failure.

The wait helper now accepts required resource IDs. Section 8 passes all three
new resources, so the same bounded poll requires their compliant tag records
before continuing. Section 5 keeps its original Policy-only behavior.

The executable offline regression runs the documented Bash helper with mocked
Policy-state responses:

| Sequence | Required result | Result |
|---|---|---|
| Only the earlier compliant fixture state | Continue polling, then time out | Passed; all 40 polls used |
| New resource records arrive one at a time | Wait until all three are present | Passed; accepted only the fourth response |
| Earlier Policy-only gate with no additional IDs | Accept its original complete fixture state | Passed; first response accepted |

This is an offline reproduction and regression result, not a claim that the
new live Automation phase completed. Microsoft documents that on-demand scans
are asynchronous and gives no fixed completion guarantee. A timeout or empty
state is incomplete evidence, not proof of compliance.
[Microsoft Policy evaluation guidance](https://learn.microsoft.com/en-us/azure/governance/policy/how-to/get-compliance-data#evaluation-triggers)

## Pending live stages

| Stage | Current result |
|---|---|
| New report-only assignment and identity role grants | Pending scoped Azure RBAC authority |
| Policy fixture deployment and nonempty compliance state | Pending the assignment stage |
| Enforcement promotion, Modify and diagnostics remediation | Pending scoped Azure RBAC authority and prior stages |
| Fresh joint Automation publication, reader/apply/idempotence | Pending Policy qualification and scoped Azure RBAC authority |
| Final combined resource, Policy, job and RBAC invariants | Not run |
| Temporary-resource cleanup and shared-object after comparison | Pending coordinated teardown |

Contributor plus Resource Policy Contributor does not grant the role-management
operations needed by the workflow. The temporary grant must cover custom-role
definitions as well as assignments; RBAC Administrator alone lacks the former.
[Microsoft privileged role definitions](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged)

The prepared workspace remains a temporary test resource until cleanup is
verified. No new Policy assignment, Policy fixture, remediation, or joint
Automation qualification is claimed by this provisional record.

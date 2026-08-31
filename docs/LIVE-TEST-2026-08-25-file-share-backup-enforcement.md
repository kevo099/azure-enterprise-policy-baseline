# Live Azure validation — file-share backup enforcement — 2026-08-25

This test exercised `scripts/ensure_file_share_backup.py` against fresh Azure
resources. It validated discovery, dry-run planning, protection, idempotent
reconciliation, an on-demand backup, a listed recovery point, and an
alternate-location restore. It also re-assigned the custom and Microsoft audit
policies, but Azure Policy did not publish state records during the test
window; that portion is recorded as inconclusive rather than passed.

## Scope and safety

- One newly created, uniquely named resource group in East US 2. Its
  tenant-specific name is intentionally omitted.
- Purpose, automation-owner, and expiry tags bounded the fixture lifecycle;
  their environment-specific values are intentionally omitted.
- One new Standard_LRS StorageV2 account, one new Recovery Services vault, and
  one daily/30-day Azure Files backup policy.
- No pre-existing workload resource was selected or changed.
- The test identity held Contributor and Resource Policy Contributor. It did
  not hold Owner, User Access Administrator, or RBAC Administrator, so this
  run did not claim to test the Preview DINE assignment's managed-identity role
  grants. The caller-driven reconciliation path was fully exercised instead.

## Fixtures

The initial storage account contained two 10-GiB-quota shares:

| Share | ARM `enabledProtocols` | Purpose |
|---|---|---|
| `data-default` | `null` | Prove the automation treats the ARM default as SMB |
| `data-smb` | `SMB` | Prove explicit SMB handling |

The repository's public MIT `LICENSE` was uploaded to
`data-default/canary.txt`. Its SHA-256 was
`4532cf25b46bff92eab839c4a6267a3994ffd4ef992827cdef980f2b04b2a61c`.

## Reconciliation results

The first dry run discovered exactly two `ENABLE` actions and zero blockers.
The `--apply` run registered the storage account, configured both shares, and
waited until both had a healthy configuration record. At that point Azure
reported `IRPending`; that is accepted configuration with first recovery
pending, not yet proof of an active recovery point.

Azure then reported these completed jobs:

| Operation | Entity | UTC completion |
|---|---|---|
| Register | storage account | 15:02:23 |
| ConfigureBackup | `data-default` | 15:03:09 |
| ConfigureBackup | `data-smb` | 15:03:54 |

After the restore target was created, a second dry run left the two protected
shares unchanged and found exactly one new action for `restore-target`. The
second apply completed its ConfigureBackup job at 15:07:46 UTC. Final
inventory contained three healthy protected items and zero eligible
unprotected shares.

## Recovery-point and restore proof

An on-demand backup was then completed for every test share. Azure listed one
`FileSystemConsistent` recovery point for each:

| Share | Backup job | Recovery point | UTC time |
|---|---|---|---|
| `data-default` | Completed | `FileSystemConsistent` listed | 15:04:24 |
| `data-smb` | Completed | `FileSystemConsistent` listed | 15:09:40 |
| `restore-target` | Completed | `FileSystemConsistent` listed | 15:09:51 |

Tenant-specific job and recovery-point identifiers are intentionally omitted.

That recovery point was restored to the separately created
`restore-target` share. The restore job completed in 34 seconds; its
tenant-specific identifier is intentionally omitted. The restored
`canary.txt` was 1,064 bytes and its SHA-256 matched the source exactly:

```text
4532cf25b46bff92eab839c4a6267a3994ffd4ef992827cdef980f2b04b2a61c
```

This proves a usable recovery point and byte-identical restore for the test
fixture. It does not replace periodic production restore drills or RPO/RTO
monitoring.

## Azure Policy observation

The repository's custom audit definition and Microsoft's built-in
`cfc5190a-3b19-4a23-b563-a4c719b666e4` were both assigned to the isolated
resource group with `enforcementMode=Default`. Two blocking on-demand scans
completed, one before and one after assignment propagation time, but
`az policy state list` and `az policy state summarize` returned no resource
records. A final asynchronous scan was also requested after protection. The
CLI's legacy summarize path also returned `InvalidResourceType` for the
resource-group-scoped assignment rather than usable compliance data.

Zero state records is not a compliant verdict. The 2026-08-25 Policy result is
therefore **inconclusive due to missing platform evaluation data**. The prior
2026-07-28 live test remains the evidence that both definitions evaluate the
default and explicit SMB fixtures and transition from `NonCompliant` to
`Compliant` after protection.

During this test, Azure CLI 2.89.0 also rejected the repository's older
multi-token `--metadata category=... version=...` form. `scripts/deploy.sh`
was updated to pass one JSON metadata object, and the exact custom definition
then deployed successfully.

## Teardown

Protection was stopped with backup-data deletion for all three shares. Azure
reported these `DeleteBackupData` jobs `Completed`; tenant-specific job
identifiers are intentionally omitted:

| Share | Delete job result |
|---|---|
| `data-default` | Completed |
| `data-smb` | Completed |
| `restore-target` | Completed |

The container then reported `SoftDeleted`/not registered. Both scoped Policy
assignments and the temporary subscription definition were deleted before the
exact tagged resource group. Final **active-resource** verification returned:

- `az group exists`: `false`;
- no matching policy assignment or custom definition;
- no active test-named vault, storage account, or other ARM resource.

Azure soft-delete intentionally retained a recoverable deleted-vault record:

- one deleted-vault record remained; its tenant-specific identifier is
  intentionally omitted;
- vault deletion time: `2026-08-25T15:17:00.754700+00:00`;
- automatic purge time: `2026-09-08T15:11:49.537372+00:00`;
- retained soft-deleted containers/items: `3`.

The recovery points remain recoverable by undeleting the vault until Azure's
purge time; no manual purge surface is available in the current CLI. This is
zero **active** test delta, not zero service-retained soft-delete data. The
restored storage fixture itself was removed with the resource group.

After the live run, the script received additional fail-closed hardening for
subscription-aware correlation, account-level vault-registration discovery,
soft-delete handling, action caps, and command timeouts. Those branches are
covered by the repository unit suite; the Azure enable/configure/backup/restore
primitives above are the live evidence, not a claim that every synthetic error
branch was reproduced in Azure. The final hardened inventory path was also run
read-only against East US 2 after teardown. It queried the active, active-vault
soft-deleted, and whole deleted-vault surfaces and returned one deduplicated
`SoftDeletedVault` account association for this test's three retained items.
A final subscription-wide file-share inventory was also run after a review
found an existing `BlockBlobStorage` account with no Files endpoint. The
hardened inventory skipped that account and completed without calling the
unsupported Azure Files API for it.

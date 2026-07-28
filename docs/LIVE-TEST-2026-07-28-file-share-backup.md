# Live Azure validation — file-share backup audit — 2026-07-28

This test deployed and exercised policy #16,
`audit-file-share-backup-protection`, in a disposable Microsoft-sponsored
Azure subscription. It was a focused validation of the file-share backup
control only. The separate
[2026-07-16 live test](LIVE-TEST-2026-07-16.md) covers the baseline's original
15 policies.

## Scope and method

- Test scope: one isolated resource group in East US 2, separate from all
  pre-existing workload resource groups.
- Storage fixture: one Standard_LRS StorageV2 account with two Azure file
  shares:
  - one share created with the default protocol settings, whose ARM
    representation reported `enabledProtocols: null`;
  - one share created with `enabledProtocols: SMB` explicitly.
- Backup fixture: one Recovery Services vault in the same region.
- Policy comparison: the custom policy and Microsoft's Preview built-in
  `[Preview]: Azure Backup should be enabled on Azure file shares`
  (`cfc5190a-3b19-4a23-b563-a4c719b666e4`) were assigned side by side at the
  resource-group scope.
- Evaluation method: an on-demand Azure Policy scan before protection, then a
  second on-demand scan after both backup configuration jobs completed.

The test identity initially had Contributor. That role can create the storage
and backup fixtures, but it cannot create policy definitions or assignments.
Resource Policy Contributor was added for the live test. This was deployer
authorization only: the `AuditIfNotExists` policy itself has no managed
identity or role grants.

## Phase 1 — unprotected shares

The first on-demand scan completed in **7 minutes 35 seconds**. Both policies
reported both shares as `NonCompliant`.

| Share fixture | ARM `enabledProtocols` | Custom policy | Microsoft built-in |
|---|---|---|---|
| Default protocol | `null` | `NonCompliant` | `NonCompliant` |
| Explicit SMB | `SMB` | `NonCompliant` | `NonCompliant` |

This established two things:

1. The custom definition produced the same result as Microsoft's built-in rule
   for every test resource.
2. A default-created share was evaluated as SMB even though a resource GET
   exposed `enabledProtocols: null`. In this live evaluation, the Azure Policy
   alias gate did not skip the share; it behaved like the explicitly declared
   SMB share. Consumers should therefore not assume that a `null` value in the
   ARM representation makes a default SMB share invisible to this policy.

## Enabling Azure Files protection

A fresh Recovery Services vault did not contain an
`AzureStorage`/`AzureFileShare` backup policy. Its built-in `DefaultPolicy` is
for virtual machines and cannot be used to protect Azure file shares. The test
therefore created an AzureStorage-workload policy with a daily schedule and
30-day retention, then enabled protection for both shares.

Azure Files configure-backup is asynchronous. Successful CLI returns were not
treated as completion: the test waited for the vault jobs and observed two
`ConfigureBackup` jobs and the storage-container `Register` job in `Completed`
state before starting the second compliance scan.

## Phase 2 — protected shares

The second on-demand scan completed in **12 minutes 8.644 seconds**. Both
policies then reported both shares as `Compliant`.

| Share fixture | ARM `enabledProtocols` | Custom policy | Microsoft built-in |
|---|---|---|---|
| Default protocol | `null` | `Compliant` | `Compliant` |
| Explicit SMB | `SMB` | `Compliant` | `Compliant` |

The four-record transition from `NonCompliant` to `Compliant` confirms that
the custom rule's `Microsoft.RecoveryServices/backupprotecteditems`
pseudo-type correlated each SMB share with its Recovery Services protected
item in the same way as the Microsoft built-in.

This remains an existence-only result. It proves that a protected-item record
was correlated to each share; it does not prove backup health, recovery-point
freshness, retention compliance, or restore success.

## Teardown verification

- Both `DeleteBackupData` jobs completed and protection stopped for both
  shares.
- Both resource-group-scoped policy assignments and the subscription-scoped
  custom policy definition were deleted.
- Azure's secure-by-default behavior moved the backup items, their registered
  storage container, and then the Recovery Services vault into soft-deleted
  state. The vault disappeared from active ARM inventory. Its default 14-day
  retention is not billed; see Microsoft's
  [secure-by-default soft-delete documentation](https://learn.microsoft.com/azure/backup/secure-by-default).
- The isolated test resource group and its remaining active storage resources
  were deleted.
- Final enumeration showed no test resource group, no custom policy
  definitions, no policy assignments, and no active Recovery Services vaults.
  Only unrelated resource groups that pre-dated this test remained.
- The pre-existing workload resources were not changed.

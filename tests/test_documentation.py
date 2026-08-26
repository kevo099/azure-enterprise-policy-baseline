import json
import pathlib
import re
import subprocess
import unittest
from urllib.parse import unquote, urlsplit


ROOT = pathlib.Path(__file__).parents[1]
RUNBOOK = ROOT / "docs" / "RUNBOOK-replicate-azure-files-backup.md"
ENFORCEMENT_GUIDE = ROOT / "docs" / "GUIDE-file-share-backup-enforcement.md"
AUDIT_GUIDE = ROOT / "docs" / "GUIDE-file-share-backup-audit.md"


class ReplicationDocumentationTests(unittest.TestCase):
    def test_assignment_parameter_example_matches_preview_contract(self):
        example = json.loads(
            (
                ROOT / "examples" / "azure-files-backup-assignment.example.json"
            ).read_text()
        )
        self.assertEqual(
            set(example),
            {
                "effect",
                "vaultLocation",
                "backupPolicyId",
                "registerStorageAccount",
                "exclusionTagName",
                "exclusionTagValue",
            },
        )
        self.assertEqual(example["effect"]["value"], "DeployIfNotExists")
        self.assertIs(example["registerStorageAccount"]["value"], True)
        self.assertEqual(example["exclusionTagName"]["value"], "SkipAzureFilesBackup")
        self.assertEqual(example["exclusionTagValue"]["value"], ["true"])
        self.assertIn("<subscription-id>", example["backupPolicyId"]["value"])

    def test_snapshot_policy_example_is_not_described_as_vault_standard(self):
        example = json.loads(
            (ROOT / "examples" / "azure-files-backup-policy.json").read_text()
        )
        properties = example["properties"]
        self.assertEqual(properties["backupManagementType"], "AzureStorage")
        self.assertEqual(properties["workloadType"], "AzureFileShare")
        self.assertIn("retentionPolicy", properties)
        self.assertNotIn("vaultRetentionPolicy", properties)

        for document in (RUNBOOK, ENFORCEMENT_GUIDE):
            text = " ".join(document.read_text().casefold().split())
            self.assertIn("snapshot-tier", text, document)
            self.assertIn("not an offsite copy", text, document)

    def test_runbook_preserves_safe_promotion_and_recovery_order(self):
        text = RUNBOOK.read_text()
        required = [
            "set -euo pipefail",
            "--enforcement-mode DoNotEnforce",
            "source share did not evaluate NonCompliant before promotion",
            "--role 17d1049b-9a84-46fb-8f53-869881c3d3ab",
            "--role 5e467623-bb1f-42f4-a55d-6e525e11384b",
            "--enforcement-mode Default",
            "--resource-discovery-mode ReEvaluateCompliance",
            "remediation did not succeed within 30 minutes",
            "ConfigureBackup did not complete within 30 minutes",
            "az backup protection backup-now",
            "az backup recoverypoint list",
            "DINE did not pause before restore-target creation",
            "az backup restore restore-azurefileshare",
            "cmp \"$CANARY_FILE\" \"$RESTORED_FILE\"",
            "--delete-backup-data true",
            "az group exists --name \"$SOURCE_RG\"",
        ]
        positions = [text.index(marker) for marker in required]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Empty Policy output is **inconclusive**", text)
        self.assertIn("Deleting Policy/RBAC objects does not stop", text)
        self.assertIn("--definition-version '2.0.*'", text)
        self.assertIn("roleDefinitionId // empty", text)
        self.assertIn("status:.status", text)

    def test_runbook_teardown_handles_early_failures_safely(self):
        text = RUNBOOK.read_text()
        teardown = text.split("## 11. Teardown", 1)[1]
        self.assertIn("az backup vault show", teardown)
        self.assertIn(
            "Vault has no active canary item; skipping backup-data deletion",
            teardown,
        )
        self.assertIn(
            "Recovery Services vault does not exist; skipping backup-data cleanup",
            teardown,
        )
        self.assertIn("--arg account \"$STORAGE_ACCOUNT\"", teardown)
        self.assertIn("multiple vault containers match", teardown)
        self.assertIn("the only active item is not the expected source", teardown)
        self.assertIn(".properties.sourceResourceId // empty", teardown)
        self.assertIn("unexpected storage account", teardown)
        self.assertIn("rediscovered protected item does not match", teardown)
        self.assertIn("az group exists --name \"$BACKUP_RG\"", teardown)

    def test_full_initiative_audit_option_stays_report_only(self):
        text = AUDIT_GUIDE.read_text()
        option = text.split("## Option A", 1)[1].split("## Option B", 1)[0]
        self.assertIn("--dry-run", option)
        self.assertIn("unrelated Deny, Modify, and DeployIfNotExists", option)

    def test_no_stale_github_account_pointer_in_markdown(self):
        for document in ROOT.rglob("*.md"):
            self.assertNotIn("AoS-ssb", document.read_text(), document)

    def test_local_markdown_links_resolve(self):
        link_pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
        failures = []
        for document in ROOT.rglob("*.md"):
            for raw_target in link_pattern.findall(document.read_text()):
                target = raw_target.strip()
                if target.startswith("<") and target.endswith(">"):
                    target = target[1:-1]
                parsed = urlsplit(target)
                if parsed.scheme or target.startswith("#"):
                    continue
                path = unquote(parsed.path)
                if not path:
                    continue
                resolved = (document.parent / path).resolve()
                if not resolved.exists():
                    failures.append(f"{document.relative_to(ROOT)} -> {target}")
        self.assertEqual(failures, [])

    def test_replication_runbook_bash_blocks_are_valid_shell(self):
        text = RUNBOOK.read_text()
        blocks = re.findall(r"^```bash\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
        self.assertGreater(len(blocks), 10)
        result = subprocess.run(
            ["bash", "-n"],
            input="\n".join(blocks),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

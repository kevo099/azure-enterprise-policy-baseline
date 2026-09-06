from __future__ import annotations

import os
import json
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "REPLICATE-POLICY-AUTOMATION.md"
FIXTURE = ROOT / "infra" / "policy-showcase.bicep"


class ReplicationGuideTests(unittest.TestCase):
    def run_final_policy_wait(
        self, states: list[list[dict[str, str]]], required_ids: list[str]
    ) -> tuple[subprocess.CompletedProcess[str], int]:
        text = GUIDE.read_text(encoding="utf-8")
        match = re.search(
            r"^(wait_for_final_policy_state\(\) \{.*?^\})$",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, state in enumerate(states, 1):
                (Path(temp_dir) / f"fixture-{index}.json").write_text(
                    json.dumps(state), encoding="utf-8"
                )
            env = os.environ.copy()
            env.update(
                PRIVATE_WORK=temp_dir,
                SUBSCRIPTION_ID="example",
                RESOURCE_GROUP="example",
                POLICY_ASSIGNMENT="example",
            )
            result = subprocess.run(
                ["bash"],
                input=(
                    "set -u\nPOLL_COUNT=0\n"
                    "az() {\n"
                    "  POLL_COUNT=$((POLL_COUNT + 1))\n"
                    "  local fixture=$POLL_COUNT\n"
                    f"  if [ \"$fixture\" -gt {len(states)} ]; then fixture={len(states)}; fi\n"
                    '  cat "$PRIVATE_WORK/fixture-$fixture.json"\n'
                    "}\n"
                    "sleep() { :; }\n"
                    f"{match.group(1)}\n"
                    f"wait_for_final_policy_state {shlex.join(required_ids)}\n"
                    "status=$?\n"
                    'printf "%s" "$POLL_COUNT" >"$PRIVATE_WORK/polls"\n'
                    'exit "$status"\n'
                ),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
            )
            return result, int((Path(temp_dir) / "polls").read_text())

    def test_final_policy_wait_requires_each_new_resource(self) -> None:
        def row(reference: str, state: str, resource: str) -> dict[str, str]:
            return {
                "policyDefinitionReferenceId": reference,
                "complianceState": state,
                "resourceId": resource,
            }

        baseline = [
            row("inherit-tag-from-resource-group", "Compliant", "/example/storage"),
            row("deploy-keyvault-diagnostics", "Compliant", "/example/keyvault"),
            row("audit-file-share-backup-protection", "NonCompliant", "/example/share"),
        ]
        ids = ["/example/automation", "/example/vault-rg", "/example/vault-sub"]
        states = [baseline]
        for resource in ids:
            states.append(states[-1] + [
                row("inherit-tag-from-resource-group", "Compliant", resource.upper() + "/")
            ])

        ready, polls = self.run_final_policy_wait(states, ids)
        self.assertEqual(0, ready.returncode, ready.stderr)
        self.assertEqual(4, polls, "must wait until all three new resources appear")

        stale, polls = self.run_final_policy_wait([baseline], ids)
        self.assertNotEqual(0, stale.returncode)
        self.assertEqual(40, polls, "old fixture state must never satisfy the final gate")

        original_stage, polls = self.run_final_policy_wait([baseline], [])
        self.assertEqual(0, original_stage.returncode, original_stage.stderr)
        self.assertEqual(1, polls, "the earlier Policy-only gate needs no Automation IDs")

    def test_local_markdown_links_resolve(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#")):
                continue
            path_text = unquote(target.split("#", 1)[0])
            self.assertTrue(
                (GUIDE.parent / path_text).resolve().exists(),
                f"missing local guide link: {target}",
            )

    def test_bash_blocks_are_valid_shell(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        blocks = re.findall(r"```bash\n(.*?)\n```", text, flags=re.DOTALL)
        self.assertGreaterEqual(len(blocks), 10)
        result = subprocess.run(
            ["bash", "-n"],
            input="\n\n".join(blocks),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_cleanup_removes_assignments_before_group_and_retains_definitions(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        cleanup = text[text.index("## 11. Optional cleanup") :]
        self.assertLess(cleanup.index("DELETE_RG_PURPOSE"), cleanup.index("ring-role.sh revoke"))
        self.assertLess(cleanup.index("discovery-role.sh revoke"), cleanup.index("unassign.sh"))
        self.assertLess(cleanup.index("unassign.sh"), cleanup.index("az group delete"))
        self.assertNotIn("./scripts/undeploy.sh", cleanup)
        self.assertIn("intentionally remain", cleanup)

    def test_subscription_policy_deployment_is_fresh_only(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        deployment = text[text.index("## 4. Publish") : text.index("## 5. Promote")]
        self.assertLess(deployment.index("az policy definition show"), deployment.index("./scripts/deploy.sh"))
        self.assertLess(deployment.index("az policy set-definition show"), deployment.index("./scripts/deploy.sh"))
        self.assertIn("POLICY_DEFINITION_COUNT", deployment)
        self.assertIn("definition_probe_status=$?", deployment)
        self.assertIn('"$definition_probe_status" -ne 3', deployment)
        self.assertIn("initiative_probe_status=$?", deployment)
        self.assertIn('"$initiative_probe_status" -ne 3', deployment)

    def test_policy_fixture_hash_is_pinned(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        self.assertIn(
            'EXPECTED_POLICY_FIXTURE_SHA="ee6a382443881993fea49ef25f9c6c89ecaff38addb1faa04180b22fc5f0eaad"',
            text,
        )
        self.assertIn('test "$ACTUAL_POLICY_FIXTURE_SHA" = "$EXPECTED_POLICY_FIXTURE_SHA"', text)

    def test_fixture_is_retained_and_has_no_exposed_workload(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertIn("retainForInspection bool = true", text)
        self.assertIn("publicNetworkAccess: 'Disabled'", text)
        self.assertIn("allowSharedKeyAccess: false", text)
        for forbidden in (
            "Microsoft.Compute/virtualMachines",
            "Microsoft.Network/publicIPAddresses",
            "Microsoft.Sql/servers",
            "destinationPortRange: '22'",
            "destinationPortRange: '3389'",
        ):
            self.assertNotIn(forbidden, text)

    def test_guide_keeps_private_output_out_of_git(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        self.assertIn('PRIVATE_WORK="$(mktemp -d)"', text)
        self.assertIn("Do not run with", text)
        self.assertNotIn("/home/", text)
        self.assertNotIn("--debug", "\n".join(
            block for block in re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL)
        ))

    def test_automation_artifact_is_immutably_pinned(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        match = re.search(r'^AUTOMATION_REF="([0-9a-f]{40})"$', text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertNotIn("REPLACE_AFTER_AUTOMATION_COMMIT", text)

    def test_guide_has_terminal_and_rollback_gates(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        for marker in (
            "azure-asyncoperation:",
            "SEED_LOCATION_URL",
            "READER_READY",
            "APPLY_DEADLINE",
            'test("403|forbidden|authorizationfailed|does not have authorization"; "i")',
            "Automatic writer-role revocation failed",
            "test \"$(grep -c '^SUMMARY '",
            'properties.runtimeEnvironment == "PowerShell74"',
            'jobSchedules?api-version=2024-10-23',
            "final-automation-roles.json",
            "final-policy-roles.json",
            "discovery-reader-rg-role.template.json",
            'DELETE_RG_PURPOSE" = "AzurePolicyAutomationShowcase"',
        ):
            self.assertIn(marker, text)

    def test_finished_product_is_rescanned_after_automation_creation(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        final = text[text.index("## 8. Prove final invariants") :]
        self.assertLess(final.index("AUTOMATION_RESOURCE_ID"), final.index("wait_for_final_policy_state"))
        self.assertIn("RG_VAULT_RESOURCE_ID", final)
        self.assertIn("SUBSCRIPTION_VAULT_RESOURCE_ID", final)
        self.assertIn("$PRIVATE_WORK/policy-final.json", final)
        self.assertIn('.identity.type == "SystemAssigned"', final)
        self.assertIn('($direct | length) == 3', final)
        self.assertLess(final.index("DISCOVERY_ROLE="), final.index("final-automation-roles.json"))
        self.assertIn('.roleDefinitionName == $reader', final)
        self.assertIn('$reader[0].description == $e.Description', final)

    def test_writer_exit_trap_reports_revoke_failure_and_preserves_status(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        match = re.search(
            r"^(revoke_writer_on_exit\(\) \{.*?^\})$",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match)
        function = match.group(1)

        def run_with_helper(helper_status: int, original_status: int) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as temp_dir:
                helper = Path(temp_dir) / "scripts" / "ring-role.sh"
                helper.parent.mkdir()
                helper.write_text(
                    f"#!/usr/bin/env bash\nexit {helper_status}\n",
                    encoding="utf-8",
                )
                helper.chmod(0o700)
                env = os.environ.copy()
                env.update(
                    AUTOMATION_DIR=temp_dir,
                    SUBSCRIPTION_ID="sub",
                    RESOURCE_GROUP="rg",
                    AUTOMATION_PRINCIPAL="principal",
                )
                return subprocess.run(
                    ["bash"],
                    input=(
                        "set -u\n"
                        "WRITER_GRANTED=true\n"
                        f"{function}\n"
                        "trap revoke_writer_on_exit EXIT\n"
                        f"exit {original_status}\n"
                    ),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    env=env,
                )

        failing = run_with_helper(helper_status=1, original_status=0)
        self.assertEqual(1, failing.returncode, failing.stderr)
        successful = run_with_helper(helper_status=0, original_status=23)
        self.assertEqual(23, successful.returncode, successful.stderr)


if __name__ == "__main__":
    unittest.main()

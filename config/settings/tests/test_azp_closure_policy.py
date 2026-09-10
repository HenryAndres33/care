"""Verify the owner-approved local AZP policy without loading secrets or a DB."""

import ast
import unittest
from pathlib import Path


class AzpClosurePolicyTests(unittest.TestCase):
    def test_local_azp_has_exact_required_document(self):
        source = Path(__file__).resolve().parents[1] / "local.py"
        module = ast.parse(source.read_text())
        assignments = [
            node
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT"
                for target in node.targets
            )
        ]
        self.assertEqual(len(assignments), 1)
        policy = ast.literal_eval(assignments[0].value)
        self.assertEqual(
            policy["d1dd82e0-0690-4121-94d8-7605b27192ee"],
            ["urology-medisch-dossier"],
        )
        self.assertEqual(policy["urology"], ["urology-medisch-dossier"])


if __name__ == "__main__":
    unittest.main()

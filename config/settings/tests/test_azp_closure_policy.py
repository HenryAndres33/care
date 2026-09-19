"""Verify the owner-approved local AZP policy without loading secrets or a DB."""

import unittest


class AzpClosurePolicyTests(unittest.TestCase):
    def test_local_azp_has_exact_required_document(self):
        from care_suriname.policies.settings import SETTING_DEFAULTS

        policy = SETTING_DEFAULTS["local"]["CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT"]
        self.assertEqual(
            policy["d1dd82e0-0690-4121-94d8-7605b27192ee"],
            ["urology-medisch-dossier"],
        )
        self.assertEqual(policy["urology"], ["urology-medisch-dossier"])


if __name__ == "__main__":
    unittest.main()

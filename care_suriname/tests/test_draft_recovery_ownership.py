"""Ownership and content-type identity must not weaken key recovery."""

import ast
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from care.audit_log.helpers import exclude_model
from care_suriname.models.draft_recovery import DraftRecoveryKey

move = import_module("care_suriname.migrations.0002_move_draft_recovery")


class DraftRecoveryOwnershipTests(SimpleTestCase):
    def test_model_and_implementation_are_plugin_owned(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(list((root / "care/users/draft_recovery").glob("*.py")), [])
        self.assertFalse((root / "config/draft_recovery_auth.py").exists())
        self.assertIs(
            apps.get_model("care_suriname", "DraftRecoveryKey"), DraftRecoveryKey
        )
        with self.assertRaises(LookupError):
            apps.get_model("users", "DraftRecoveryKey")
        # The public metadata is Django's documented model-introspection API.
        self.assertEqual(DraftRecoveryKey._meta.db_table, "users_draftrecoverykey")  # noqa: SLF001

    @override_settings(AUDIT_LOG_DOMAIN_LEDGER_MODE=True)
    def test_moved_model_retains_generic_audit_value_exclusion(self):
        exclude_model.cache_clear()
        self.addCleanup(exclude_model.cache_clear)
        self.assertTrue(exclude_model("care_suriname.DraftRecoveryKey"))

    def test_native_consumers_are_only_the_two_existing_auth_proof_hooks(self):
        root = Path(__file__).resolve().parents[2]
        consumers = set()
        for directory in ("care", "config"):
            for path in (root / directory).rglob("*.py"):
                if "tests" in path.parts or "migrations" in path.parts:
                    continue
                for node in ast.walk(ast.parse(path.read_text())):
                    names = []
                    if isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    elif isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    for name in names:
                        self.assertFalse(name.startswith("care.users.draft_recovery"))
                        self.assertNotEqual(name, "config.draft_recovery_auth")
                        if name.startswith("care_suriname.draft_recovery"):
                            self.assertEqual(name, "care_suriname.draft_recovery.auth")
                            consumers.add(path.relative_to(root).as_posix())
        self.assertEqual(consumers, {"config/auth_views.py", "care/emr/utils/mfa.py"})


class DraftRecoveryContentTypeTests(TestCase):
    def setUp(self):
        self.editor = SimpleNamespace(connection=connection)
        self.row = ContentType.objects.get(
            app_label="care_suriname", model="draftrecoverykey"
        )
        self.permissions = list(
            Permission.objects.filter(content_type=self.row)
            .order_by("pk")
            .values_list("pk", "content_type_id", "codename")
        )

    def test_relabel_round_trip_preserves_identity_and_permissions(self):
        move.backwards(apps, self.editor)
        self.row.refresh_from_db()
        self.assertEqual(self.row.app_label, "users")
        move.forwards(apps, self.editor)
        self.row.refresh_from_db()
        self.assertEqual(self.row.app_label, "care_suriname")
        self.assertEqual(
            list(
                Permission.objects.filter(content_type=self.row)
                .order_by("pk")
                .values_list("pk", "content_type_id", "codename")
            ),
            self.permissions,
        )

    def test_duplicate_target_fails_without_relabelling(self):
        duplicate = ContentType.objects.create(
            app_label="users", model="draftrecoverykey"
        )
        with self.assertRaises(move.ContentTypeRelabelError), transaction.atomic():
            move.forwards(apps, self.editor)
        duplicate.refresh_from_db()
        self.row.refresh_from_db()
        self.assertEqual(duplicate.app_label, "users")
        self.assertEqual(self.row.app_label, "care_suriname")

    def test_missing_source_on_installed_database_fails_closed(self):
        self.row.delete()
        with self.assertRaises(move.ContentTypeRelabelError), transaction.atomic():
            move.forwards(apps, self.editor)

    def test_target_only_is_not_silently_accepted(self):
        with self.assertRaises(move.ContentTypeRelabelError), transaction.atomic():
            move.forwards(apps, self.editor)

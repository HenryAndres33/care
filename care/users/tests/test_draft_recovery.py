import base64
import json
import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.db import close_old_connections
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from care.emr.utils.mfa import create_auth_response
from care.users.draft_recovery.crypto import (
    RecoveryUnavailableError,
    load_wrapping_keys,
)
from care.users.draft_recovery.models import DraftRecoveryKey
from care.users.draft_recovery.service import get_or_create_key, rewrap_key
from care.users.models import User
from config.auth_views import TokenObtainPairSerializer, TokenRefreshSerializer
from config.draft_recovery_auth import interactive_refresh_token


@override_settings(DEBUG=False)
class DraftRecoveryTests(APITransactionTestCase):
    url = "/api/v1/users/me/draft-recovery-key/"

    def setUp(self):
        self.owner = User.objects.create_user(
            username="draft-owner", password="test-only-password"
        )
        self.other = User.objects.create_user(
            username="draft-other", password="test-only-password"
        )
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "wrapping.json"
        self.first = base64.b64encode(os.urandom(32)).decode()
        self.write_ring({"v1": self.first}, "v1")
        self.config = override_settings(DRAFT_RECOVERY_WRAPPING_KEYS_FILE=self.path)
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.authenticate(self.owner)

    def write_ring(self, keys, active):
        with self.path.open("w") as stream:
            json.dump({"active_key_id": active, "keys": keys}, stream)
        self.path.chmod(0o600)

    def authenticate(self, user, token=None):
        access = token or interactive_refresh_token(user, "password").access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def post(self, **kwargs):
        return self.client.post(self.url, {}, format="json", secure=True, **kwargs)

    def test_stable_create_owner_only_and_no_cache(self):
        first, again = self.post(), self.post()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data, again.data)
        self.assertEqual(len(base64.b64decode(first.data["key_material"])), 32)
        self.assertEqual(first.data["owner_id"], str(self.owner.external_id))
        self.assertEqual(
            first.data["key_release_expires_at"] - first.data["authenticated_at"], 900
        )
        row = DraftRecoveryKey.objects.get()
        self.assertNotEqual(
            bytes(row.ciphertext), base64.b64decode(first.data["key_material"])
        )
        self.assertIn("no-store", first["Cache-Control"])
        detail = f"{self.url}{row.id}/"
        self.authenticate(self.other)
        denied = self.client.get(detail, secure=True)
        self.assertEqual(denied.status_code, 404)
        self.assertIn("no-store", denied["Cache-Control"])
        self.other.is_superuser = True
        self.other.save(update_fields=["is_superuser"])
        self.assertEqual(self.client.get(detail, secure=True).status_code, 404)

    def test_missing_old_future_and_refresh_only_auth_rejected(self):
        cases = [RefreshToken.for_user(self.owner).access_token]
        for timestamp in (
            int(timezone.now().timestamp()) - 901,
            int(timezone.now().timestamp()) + 90,
            True,
        ):
            access = interactive_refresh_token(self.owner, "password").access_token
            access["care_authenticated_at"] = timestamp
            cases.append(access)
        for token in cases:
            self.authenticate(self.owner, token)
            response = self.post()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(
                response.data["code"], "draft_recovery_reauthentication_required"
            )
        self.assertFalse(DraftRecoveryKey.objects.exists())

    def test_native_login_mfa_and_refresh_proof(self):
        with patch("config.auth_views.ratelimit", return_value=False):
            login = TokenObtainPairSerializer(
                data={
                    "username": self.owner.username,
                    "password": "test-only-password",
                },
                context={"request": None},
            )
            self.assertTrue(login.is_valid(), login.errors)
        access = AccessToken(login.validated_data["access"])
        self.assertEqual(access["care_auth_method"], "password")
        original = access["care_authenticated_at"]
        refresh = TokenRefreshSerializer(
            data={"refresh": login.validated_data["refresh"]}
        )
        self.assertTrue(refresh.is_valid(), refresh.errors)
        refreshed = AccessToken(refresh.validated_data["access"])
        self.assertEqual(refreshed["care_authenticated_at"], original)
        self.assertEqual(refreshed["care_auth_method"], "password")
        mfa = AccessToken(create_auth_response(self.owner).data["access"])
        self.assertEqual(mfa["care_auth_method"], "mfa")
        with (
            patch.object(User, "is_mfa_enabled", return_value=True),
            patch("config.auth_views.ratelimit", return_value=False),
        ):
            pending = TokenObtainPairSerializer(
                data={
                    "username": self.owner.username,
                    "password": "test-only-password",
                },
                context={"request": None},
            )
            self.assertTrue(pending.is_valid(), pending.errors)
        temporary = RefreshToken(pending.validated_data["temp_token"])
        self.assertNotIn("care_authenticated_at", temporary)
        self.assertNotIn("care_auth_method", temporary)

    def test_invalid_configuration_fails_without_creating_or_replacing(self):
        original = self.post().data
        with override_settings(DRAFT_RECOVERY_WRAPPING_KEYS_FILE=""):
            response = self.post()
            self.assertEqual(response.status_code, 503)
            self.assertIn("no-store", response["Cache-Control"])
        self.path.chmod(0o644)
        self.assertEqual(self.post().status_code, 503)
        self.path.chmod(0o600)
        self.write_ring({"v1": base64.b64encode(os.urandom(32)).decode()}, "v1")
        self.assertEqual(self.post().status_code, 503)
        self.write_ring({"v1": self.first}, "v1")
        self.assertEqual(self.post().data["key_material"], original["key_material"])
        self.assertEqual(DraftRecoveryKey.objects.count(), 1)

    def test_file_symlink_and_malformed_key_rejected(self):
        link = self.path.with_suffix(".link")
        link.symlink_to(self.path)
        with (
            override_settings(DRAFT_RECOVERY_WRAPPING_KEYS_FILE=link),
            self.assertRaises(RecoveryUnavailableError),
        ):
            load_wrapping_keys()
        for value in ("not base64", "", base64.b64encode(os.urandom(16)).decode()):
            self.write_ring({"v1": value}, "v1")
            self.assertEqual(self.post().status_code, 503)

    def test_tampered_ciphertext_owner_and_wrapping_identity_fail(self):
        self.post()
        row = DraftRecoveryKey.objects.get()
        original = bytes(row.ciphertext)
        row.ciphertext = original[:-1] + bytes([original[-1] ^ 1])
        row.save(update_fields=["ciphertext"])
        self.assertEqual(self.post().status_code, 503)
        row.ciphertext = original
        row.owner = self.other
        row.save(update_fields=["ciphertext", "owner"])
        self.authenticate(self.other)
        self.assertEqual(self.post().status_code, 503)
        row.owner = self.owner
        row.wrapping_key_id = "v2"
        row.save(update_fields=["owner", "wrapping_key_id"])
        self.write_ring({"v1": self.first, "v2": self.first}, "v1")
        self.authenticate(self.owner)
        self.assertEqual(self.post().status_code, 503)

    def test_rewrap_and_retired_key_remain_recoverable(self):
        original = self.post().data
        row = DraftRecoveryKey.objects.get()
        second = base64.b64encode(os.urandom(32)).decode()
        self.write_ring({"v1": self.first, "v2": second}, "v2")
        rewrap_key(row.id)
        self.write_ring({"v2": second}, "v2")
        self.assertEqual(self.post().data["key_material"], original["key_material"])
        row.active = False
        row.save(update_fields=["active"])
        new_key = self.post().data
        self.assertNotEqual(new_key["key_id"], original["key_id"])
        old = self.client.get(f"{self.url}{row.id}/", secure=True)
        self.assertEqual(old.data["key_material"], original["key_material"])

    def test_account_deactivation_service_and_unauthenticated_fail(self):
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertIn(self.post().status_code, (401, 403))
        self.owner.is_active = True
        self.owner.is_service_account = True
        self.owner.save(update_fields=["is_active", "is_service_account"])
        self.assertEqual(self.post().status_code, 403)
        self.client.credentials()
        response = self.post()
        self.assertIn(response.status_code, (401, 403))
        self.assertIn("no-store", response["Cache-Control"])

    def test_tls_scope_and_method_guards(self):
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 403)
        self.assertEqual(
            self.client.post(
                self.url,
                {"owner_id": str(self.other.external_id)},
                format="json",
                secure=True,
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.url + "?user=other", {}, format="json", secure=True
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 405)
        self.assertEqual(
            self.client.post(f"{self.url}{uuid.uuid4()}/", {}, secure=True).status_code,
            405,
        )

    def test_concurrent_first_creation_returns_same_key(self):
        owner_id = self.owner.pk

        def create():
            close_old_connections()
            try:
                row, material = get_or_create_key(User.objects.get(pk=owner_id))
                return row.id, material
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(DraftRecoveryKey.objects.count(), 1)

    @override_settings(DEBUG=True, ALLOWED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    def test_debug_ipv6_loopback_exception(self):
        response = self.client.post(self.url, {}, format="json", HTTP_HOST="[::1]:9000")
        self.assertEqual(response.status_code, 200)

    def test_access_log_contains_no_key_material(self):
        with self.assertLogs("draft_recovery_access", level="INFO") as logs:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        output = " ".join(logs.output)
        self.assertNotIn(response.data["key_material"], output)
        self.assertNotIn(self.first, output)

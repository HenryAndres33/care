import base64
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from care.users.draft_recovery.crypto import RecoveryUnavailableError
from care.users.draft_recovery.models import DraftRecoveryKey
from care.users.draft_recovery.service import eligible, get_or_create_key, get_owned_key
from config.authentication import CustomJWTAuthentication

logger = logging.getLogger("draft_recovery_access")


@method_decorator(sensitive_post_parameters(), name="dispatch")
class DraftRecoveryKeyView(APIView):
    authentication_classes = [CustomJWTAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "options"]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        response["Vary"] = "Authorization"
        response["X-Content-Type-Options"] = "nosniff"
        # No key, payload, username or request/response object in audit events.
        logger.info(
            "draft-key-access actor=%s method=%s status=%s",
            getattr(request.user, "pk", None),
            request.method,
            response.status_code,
        )
        return response

    def post(self, request, key_id=None):
        if key_id is not None:
            return Response({"code": "method_not_allowed"}, status=405)
        if request.data:
            return Response({"code": "unexpected_payload"}, status=400)
        return self.release(request)

    def get(self, request, key_id=None):
        if key_id is None:
            return Response({"code": "method_not_allowed"}, status=405)
        return self.release(request, key_id)

    @sensitive_variables()
    def release(self, request, key_id=None):
        if request.query_params:
            return Response({"code": "unexpected_query"}, status=400)
        local_debug = settings.DEBUG and urlsplit(
            "//" + request.get_host()
        ).hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if not request.is_secure() and not local_debug:
            return Response({"code": "draft_recovery_tls_required"}, status=403)
        token = request.auth
        now = int(timezone.now().timestamp())
        authenticated_at = token.get("care_authenticated_at") if token else None
        age_limit = settings.DRAFT_RECOVERY_RECENT_AUTH_SECONDS
        if (
            not eligible(request.user)
            or not token
            or token.get("temp_token")
            or token.get("token_type") != "access"
            or token.get("user_id") != str(request.user.external_id)
            or token.get("care_auth_method") not in ("password", "mfa")
            or type(authenticated_at) is not int
            or not 0 <= now - authenticated_at < age_limit
        ):
            return Response(
                {"code": "draft_recovery_reauthentication_required"}, status=403
            )
        try:
            row, material = (
                get_or_create_key(request.user)
                if key_id is None
                else get_owned_key(request.user, key_id)
            )
            return Response(
                {
                    "key_id": str(row.id),
                    "owner_id": str(request.user.external_id),
                    "algorithm": "AES-GCM",
                    "key_material": base64.b64encode(material).decode("ascii"),
                    "authenticated_at": authenticated_at,
                    "key_release_expires_at": authenticated_at + age_limit,
                }
            )
        except DraftRecoveryKey.DoesNotExist:
            return Response({"code": "not_found"}, status=404)
        except (RecoveryUnavailableError, DatabaseError):
            return Response({"code": "draft_recovery_unavailable"}, status=503)

"""Recent *interactive* authentication; refreshing a JWT never renews this proof."""

from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken


def interactive_refresh_token(user, method):
    refresh = RefreshToken.for_user(user)
    refresh["care_authenticated_at"] = int(timezone.now().timestamp())
    refresh["care_auth_method"] = method
    return refresh

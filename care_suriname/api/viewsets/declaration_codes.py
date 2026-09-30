"""Correct a wrongly recorded declaration code (owner, 30 September 2026).

`POST declaration-codes/charge-items/<id>/enter-in-error/` marks one native
ChargeItem that carries a declaration code (`declaration_codes/`) as
`entered_in_error`, through CARE's own cancel handling. It stays in CARE as
history; nothing is deleted.

Who may: whoever recorded it, on the same day (Paramaribo time) - native CARE
only lets facility administrators correct charge items - and anyone holding
the native `can_cancel_charge_item` permission, at any time. Only charge items
with a declaration code are touched; other billing stays native.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from care.emr.models.charge_item import ChargeItem
from care.emr.resources.charge_item.handle_charge_item_cancel import (
    handle_charge_item_cancel,
)
from care.emr.resources.charge_item.spec import CHARGE_ITEM_CANCELLED_STATUS
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.declaration_codes.definitions import CODE_SYSTEM_BASE

ENTERED_IN_ERROR = "entered_in_error"


def is_declaration_code(item: ChargeItem) -> bool:
    system = (item.code or {}).get("system") or ""
    return system.startswith(f"{CODE_SYSTEM_BASE}/")


def may_correct(user, item: ChargeItem) -> bool:
    if AuthorizationController.call(
        "can_cancel_charge_item_in_facility", user, item.facility
    ):
        return True
    recorded_today = timezone.localdate(item.created_date) == timezone.localdate()
    return (
        item.created_by_id == user.id
        and recorded_today
        and AuthorizationController.call(
            "can_create_charge_item_in_facility", user, item.facility
        )
    )


class DeclarationCodeEnteredInErrorView(APIView):
    def post(self, request, charge_item_id):
        with transaction.atomic():
            item = get_object_or_404(
                ChargeItem.objects.select_for_update().select_related("facility"),
                external_id=charge_item_id,
            )
            if not is_declaration_code(item):
                msg = "Alleen declaratiecodes kunnen hier worden gecorrigeerd."
                raise PermissionDenied(msg)
            if not may_correct(request.user, item):
                msg = (
                    "Een declaratiecode kan alleen op dezelfde dag worden "
                    "gecorrigeerd door wie hem vastlegde."
                )
                raise PermissionDenied(msg)
            if item.status in CHARGE_ITEM_CANCELLED_STATUS:
                return Response({"id": str(item.external_id), "replayed": True})
            handle_charge_item_cancel(item)
            item.status = ENTERED_IN_ERROR
            item.updated_by = request.user
            item.save()
        return Response(
            {"id": str(item.external_id), "replayed": False},
            status=status.HTTP_200_OK,
        )

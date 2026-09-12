from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from care.emr.staff_activation import doctor_activation, require_activation_admin


class DoctorActivationRequest(serializers.Serializer):
    facility = serializers.UUIDField()


class DoctorActivationMixin:
    @action(
        detail=True,
        methods=["GET", "POST"],
        permission_classes=[IsAuthenticated],
        url_path="clinical_activation",
    )
    def clinical_activation(self, request, username=None):
        require_activation_admin(request.user)
        payload = request.data if request.method == "POST" else request.query_params
        serializer = DoctorActivationRequest(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(
            doctor_activation(
                actor=request.user,
                username=username,
                facility_id=serializer.validated_data["facility"],
                activate=request.method == "POST",
            )
        )

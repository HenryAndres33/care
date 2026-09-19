"""Read-only patient identities; facility authorization is not a chart grant."""

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from care.emr.api.viewsets.base import EMRBaseViewSet
from care.emr.models.patient import Patient
from care.facility.models.facility import Facility
from care.security.authorization import AuthorizationController
from care.utils.pagination.care_pagination import CareLimitOffsetPagination
from care.utils.shortcuts import get_object_or_404
from care_suriname.resources.patient_directory import (
    PatientDirectoryRequestSpec,
    PatientDirectorySpec,
)

MINIMUM_PATIENT_DIRECTORY_NAME_LENGTH = 2


class PatientDirectoryPagination(CareLimitOffsetPagination):
    default_limit = 25
    max_limit = 100


class PatientDirectoryViewSet(EMRBaseViewSet):
    database_model = Patient

    @extend_schema(responses={200: PatientDirectorySpec})
    @action(detail=False, methods=["GET"])
    def directory(self, request, *args, **kwargs):
        """Search patient identities for appointment and correspondence workflows."""

        request_data = PatientDirectoryRequestSpec(**request.query_params.dict())
        name = (request_data.name or "").strip()
        if not name and not request_data.date_of_birth:
            raise ValidationError("Name or date of birth is required")
        if name and len(name) < MINIMUM_PATIENT_DIRECTORY_NAME_LENGTH:
            raise ValidationError("Name must contain at least 2 characters")

        facility = get_object_or_404(Facility, external_id=request_data.facility)
        if not AuthorizationController.call(
            "can_search_patient_directory",
            self.request.user,
            facility,
        ):
            raise PermissionDenied("Cannot search patients in this facility")

        queryset = Patient.objects.all()
        if name:
            queryset = queryset.filter(name__icontains=name)
        if request_data.date_of_birth:
            queryset = queryset.filter(date_of_birth=request_data.date_of_birth)

        ordering = request_data.ordering
        ordering_fields = [ordering]
        if ordering.lstrip("-") != "external_id":
            ordering_fields.append("external_id")
        queryset = queryset.order_by(*ordering_fields)

        paginator = PatientDirectoryPagination()
        page = paginator.paginate_queryset(queryset, request)
        data = [PatientDirectorySpec.serialize(obj).to_json() for obj in page]
        return paginator.get_paginated_response(data)

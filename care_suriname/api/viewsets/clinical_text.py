from django.db import IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet, EMRListMixin
from care.facility.models import Facility
from care.security.authorization import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care_suriname.models.clinical_text import ClinicalTextResource
from care_suriname.resources.clinical_text import (
    ClinicalTextKind,
    ClinicalTextResourceReadSpec,
    ClinicalTextResourceUpdateSpec,
    ClinicalTextResourceWriteSpec,
    ClinicalTextStatus,
    serialize_clinical_text_resource,
)


class ClinicalTextResourceViewSet(
    ClinicalNoStoreResponseMixin,
    EMRListMixin,
    EMRBaseViewSet,
):
    database_model = ClinicalTextResource

    def _facility(self):
        facility_id = self.request.query_params.get("facility")
        if not facility_id:
            raise ValidationError("Facility is required")
        return get_object_or_404(Facility, external_id=facility_id)

    def _authorize_read(self, facility):
        if not AuthorizationController.call(
            "can_read_questionnaire_response_template",
            self.request.user,
            facility,
        ):
            raise PermissionDenied("Access denied to clinical text configuration")

    def _authorize_write(self, facility):
        if not AuthorizationController.call(
            "can_write_questionnaire_response_template",
            self.request.user,
            facility,
        ):
            raise PermissionDenied("Access denied to clinical text configuration")

    def get_queryset(self):
        facility = self._facility()
        self._authorize_read(facility)
        queryset = (
            ClinicalTextResource.objects.filter(facility=facility)
            .select_related("facility", "created_by", "updated_by")
            .order_by("kind", "label", "key")
        )
        kind = self.request.query_params.get("kind")
        if kind:
            if kind not in {item.value for item in ClinicalTextKind}:
                raise ValidationError("Unsupported clinical text kind")
            queryset = queryset.filter(kind=kind)
        statuses = self.request.query_params.get("status")
        if statuses:
            status_values = [item.strip() for item in statuses.split(",") if item]
            allowed_statuses = {item.value for item in ClinicalTextStatus}
            if any(value not in allowed_statuses for value in status_values):
                raise ValidationError("Unsupported clinical text status")
            queryset = queryset.filter(status__in=status_values)
        query = self.request.query_params.get("query", "").strip()
        if query:
            queryset = queryset.filter(
                Q(key__icontains=query)
                | Q(label__icontains=query)
                | Q(description__icontains=query)
            )
        return queryset

    def serialize_list(self, obj):
        return serialize_clinical_text_resource(obj)

    def retrieve(self, request, *args, **kwargs):
        # Resolve by id and authorize on the stored facility, like update():
        # single reads carry no ?facility, and get_object() would demand one.
        instance = get_object_or_404(
            ClinicalTextResource.objects.select_related(
                "facility", "created_by", "updated_by"
            ),
            external_id=self.kwargs["external_id"],
        )
        self._authorize_read(instance.facility)
        return Response(serialize_clinical_text_resource(instance))

    @extend_schema(
        request=ClinicalTextResourceWriteSpec,
        responses={201: ClinicalTextResourceReadSpec},
    )
    def create(self, request, *args, **kwargs):
        spec = ClinicalTextResourceWriteSpec.model_validate(request.data)
        facility = get_object_or_404(Facility, external_id=spec.facility)
        self._authorize_write(facility)
        try:
            with transaction.atomic():
                resource = ClinicalTextResource.objects.create(
                    facility=facility,
                    kind=spec.kind.value,
                    key=spec.key,
                    label=spec.label.strip(),
                    description=spec.description.strip(),
                    status=spec.status.value,
                    version=1,
                    payload=spec.payload,
                    created_by=request.user,
                    updated_by=request.user,
                )
        except IntegrityError:
            return Response(
                {
                    "detail": "A resource with this facility, kind and key already exists"
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            serialize_clinical_text_resource(resource),
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=ClinicalTextResourceUpdateSpec,
        responses={200: ClinicalTextResourceReadSpec},
    )
    def update(self, request, *args, **kwargs):
        spec = ClinicalTextResourceUpdateSpec.model_validate(request.data)
        with transaction.atomic():
            resource = get_object_or_404(
                ClinicalTextResource.objects.select_for_update(
                    of=("self",)
                ).select_related("facility"),
                external_id=self.kwargs["external_id"],
            )
            self._authorize_write(resource.facility)
            if (
                resource.facility.external_id != spec.facility
                or resource.kind != spec.kind.value
            ):
                raise ValidationError("Facility and kind are immutable")
            if resource.version != spec.expected_version:
                return Response(
                    {"detail": "Version conflict", "current_version": resource.version},
                    status=status.HTTP_409_CONFLICT,
                )
            resource.key = spec.key
            resource.label = spec.label.strip()
            resource.description = spec.description.strip()
            resource.status = spec.status.value
            resource.payload = spec.payload
            resource.version += 1
            resource.updated_by = request.user
            try:
                with transaction.atomic():
                    resource.save(
                        update_fields=[
                            "key",
                            "label",
                            "description",
                            "status",
                            "payload",
                            "version",
                            "updated_by",
                            "modified_date",
                        ]
                    )
            except IntegrityError:
                return Response(
                    {
                        "detail": "A resource with this facility, kind and key already exists"
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        return Response(serialize_clinical_text_resource(resource))

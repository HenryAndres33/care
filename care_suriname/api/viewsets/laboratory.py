from uuid import UUID

from django.db import transaction
from drf_spectacular.utils import extend_schema
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.patient import Patient
from care.facility.models import Facility
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care_suriname.resources.laboratory_commands.catalogue import catalogue_payload
from care_suriname.resources.laboratory_commands.errors import LaboratoryCommandError
from care_suriname.resources.laboratory_commands.execution import (
    execute_laboratory_command,
)
from care_suriname.resources.laboratory_commands.locking import (
    authorize_read,
    lock_report,
)
from care_suriname.resources.laboratory_commands.projection import (
    aggregate_fingerprint,
    command_state,
    report_payload,
    verify_aggregate,
)
from care_suriname.resources.laboratory_commands.responses import (
    LaboratoryReportResponse,
)
from care_suriname.resources.laboratory_commands.specs import (
    LABORATORY_COMMAND_CONTRACT,
    validate_laboratory_command,
)

UUID_VERSION = 4


class LaboratoryDefinitionListView(ClinicalNoStoreResponseMixin, APIView):
    def get(self, request):
        facility_id = request.query_params.get("facility")
        try:
            facility_uuid = UUID(facility_id)
            if facility_uuid.version != UUID_VERSION:
                raise ValueError
        except (TypeError, ValueError, AttributeError) as error:
            raise ValidationError("A valid facility UUID is required") from error
        facility = get_object_or_404(Facility, external_id=facility_uuid)
        if not AuthorizationController.call(
            "can_list_facility_observation_definition",
            request.user,
            facility,
        ):
            raise PermissionDenied("Cannot read this laboratory catalogue")
        return Response(catalogue_payload(facility))


class LaboratoryCommandView(ClinicalNoStoreResponseMixin, APIView):
    @extend_schema(responses={200: dict, 201: dict})
    def post(self, request):
        try:
            command = validate_laboratory_command(request.data)
            data, status_code = execute_laboratory_command(command, request.user)
        except PydanticValidationError:
            return Response(
                {
                    "errors": [
                        {
                            "type": "laboratory_payload_invalid",
                            "msg": "Laboratory command payload is invalid.",
                        }
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except LaboratoryCommandError as error:
            return _error_response(error)
        response = Response(data, status=status_code)
        response["ETag"] = _etag(command.report_id, data["command_result_version"])
        return response


class LaboratoryReportView(ClinicalNoStoreResponseMixin, APIView):
    @extend_schema(responses={200: LaboratoryReportResponse})
    def get(self, request, report_id):
        include_history = request.query_params.get("include_history", "false")
        if include_history not in {"true", "false"}:
            raise ValidationError("include_history must be true or false")
        try:
            with transaction.atomic():
                report = lock_report(report_id)
                authorize_read(request.user, report)
                verify_aggregate(report)
                state = command_state(report)
                data = LaboratoryReportResponse.model_validate(
                    {
                        "contract": LABORATORY_COMMAND_CONTRACT,
                        "command_result_version": state.get("version"),
                        "report": report_payload(
                            report,
                            include_history=include_history == "true",
                        ),
                    }
                ).model_dump(mode="json")
        except LaboratoryCommandError as error:
            return _error_response(error)
        response = Response(data, status=status.HTTP_200_OK)
        response["ETag"] = _etag(report_id, data["command_result_version"])
        return response


class LaboratoryReportListView(ClinicalNoStoreResponseMixin, APIView):
    def get(self, request):
        patient_id = _query_uuid(request, "patient")
        facility_id = _query_uuid(request, "facility")
        patient = get_object_or_404(Patient, external_id=patient_id)
        get_object_or_404(Facility, external_id=facility_id)
        if not AuthorizationController.call(
            "can_view_clinical_data", request.user, patient
        ):
            raise PermissionDenied("Cannot read this patient's clinical data")
        status_filter = request.query_params.get("status")
        if status_filter not in {None, "preliminary", "final"}:
            raise ValidationError("status must be preliminary or final")
        limit = _bounded_integer(request, "limit", default=25, minimum=1, maximum=50)
        offset = _bounded_integer(request, "offset", default=0, minimum=0)
        queryset = (
            DiagnosticReport.objects.filter(
                patient=patient,
                facility__external_id=facility_id,
                meta__care_suriname__laboratory_command__contract="v1",
                service_request__deleted=False,
            )
            .select_related("service_request", "patient", "facility", "encounter")
            .order_by("-modified_date", "-external_id")
        )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        encounter = request.query_params.get("encounter")
        if encounter:
            queryset = queryset.filter(encounter__external_id=_uuid(encounter))
        visible = []
        for report in queryset:
            try:
                authorize_read(request.user, report)
            except PermissionDenied:
                continue
            state = command_state(report)
            integrity = (
                "verified"
                if state.get("aggregate_fingerprint") == aggregate_fingerprint(report)
                else "drift"
            )
            visible.append(
                {
                    "report_id": str(report.external_id),
                    "service_request_id": str(report.service_request.external_id),
                    "encounter": str(report.encounter.external_id),
                    "status": report.status,
                    "command_result_version": state.get("version"),
                    "source": state.get("source"),
                    "modified_at": report.modified_date,
                    "integrity": integrity,
                }
            )
        return Response(
            {
                "contract": LABORATORY_COMMAND_CONTRACT,
                "count": len(visible),
                "results": visible[offset : offset + limit],
            }
        )


def _error_response(error):
    return Response(
        {"errors": [{"type": error.error_type, "msg": error.message}]},
        status=error.status_code,
    )


def _etag(report_id, version):
    return f'"{report_id}:{version}"'


def _query_uuid(request, name):
    value = request.query_params.get(name)
    if not value:
        message = f"{name} is required"
        raise ValidationError(message)
    return _uuid(value)


def _uuid(value):
    try:
        parsed = UUID(value)
        if parsed.version != UUID_VERSION:
            raise ValueError
        return parsed
    except (TypeError, ValueError, AttributeError) as error:
        raise ValidationError("Invalid UUID query parameter") from error


def _bounded_integer(request, name, *, default, minimum, maximum=None):
    try:
        value = int(request.query_params.get(name, default))
    except (TypeError, ValueError) as error:
        message = f"{name} must be an integer"
        raise ValidationError(message) from error
    if value < minimum or (maximum is not None and value > maximum):
        message = f"{name} is outside the allowed range"
        raise ValidationError(message)
    return value

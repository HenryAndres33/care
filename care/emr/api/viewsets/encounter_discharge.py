from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.api.viewsets.device import disassociate_device_from_encounter
from care.emr.models.encounter import Encounter
from care.emr.models.encounter_discharge import EncounterDischargeCommand
from care.emr.resources.encounter.discharge import (
    EncounterDischargeCommandResponseSpec,
    EncounterDischargeCommandSpec,
    EncounterDischargeConflictResponseSpec,
    EncounterDischargePreflightResponseSpec,
    EncounterDischargeSpec,
    encounter_discharge_command_hash,
    encounter_discharge_payload_hash,
)
from care.emr.resources.encounter.discharge_documentation import (
    lock_discharge_documentation,
)
from care.emr.resources.encounter.discharge_state import (
    apply_encounter_discharge,
    discharge_snapshot_matches_encounter,
    encounter_discharge_blockers,
    lock_discharge_context,
)
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404


class EncounterDischargeViewSet(ClinicalNoStoreResponseMixin, EMRBaseViewSet):
    """Atomic, permission-checked and idempotent native inpatient discharge."""

    database_model = Encounter

    @extend_schema(
        request=EncounterDischargeSpec,
        responses={200: EncounterDischargePreflightResponseSpec},
    )
    def preflight_discharge(self, request, *args, **kwargs):
        request_spec = EncounterDischargeSpec.model_validate(request.data)
        reference = self._reference_encounter()
        self._authorize_discharge(reference)
        with transaction.atomic():
            documentation_warnings, _ = lock_discharge_documentation(reference)
            context = lock_discharge_context(self.kwargs["external_id"])
            self._authorize_discharge(context["encounter"])
            blockers = encounter_discharge_blockers(context, request_spec)
            blockers = sorted(set(blockers))
        return Response(
            {
                "ready": not blockers,
                "blocker_codes": blockers,
                "warning_codes": sorted(set(documentation_warnings)),
                "checked_at": timezone.now(),
            }
        )

    @extend_schema(
        request=EncounterDischargeCommandSpec,
        responses={
            200: EncounterDischargeCommandResponseSpec,
            201: EncounterDischargeCommandResponseSpec,
            409: EncounterDischargeConflictResponseSpec,
        },
    )
    def idempotent_discharge(self, request, *args, **kwargs):
        request_spec = EncounterDischargeCommandSpec.model_validate(request.data)
        reference = self._reference_encounter()
        self._authorize_discharge(reference)
        payload_hash = encounter_discharge_payload_hash(
            request_spec,
            actor_id=request.user.external_id,
            encounter_id=reference.external_id,
        )
        if replay := self._command_replay(request_spec, reference, payload_hash):
            return replay
        require_workflow_mutations_enabled(reference.facility.external_id)

        try:
            with transaction.atomic():
                documentation_warnings, documentation = lock_discharge_documentation(
                    reference
                )
                context = lock_discharge_context(self.kwargs["external_id"])
                encounter = context["encounter"]
                self._authorize_discharge(encounter)
                if replay := self._command_replay(
                    request_spec,
                    encounter,
                    payload_hash,
                ):
                    return replay
                blockers = encounter_discharge_blockers(context, request_spec)
                blockers = sorted(set(blockers))
                if blockers:
                    return self._conflict(blockers)
                snapshot = apply_encounter_discharge(
                    context,
                    request_spec,
                    request.user,
                )
                snapshot["documentation"] = documentation
                snapshot["warning_codes"] = sorted(set(documentation_warnings))
                disassociate_device_from_encounter(
                    encounter,
                    ended_at=request_spec.discharged_at,
                )
                response_data = self._create_command(
                    encounter,
                    request_spec,
                    payload_hash,
                    snapshot,
                )
        except IntegrityError:
            if replay := self._command_replay(request_spec, reference, payload_hash):
                return replay
            return self._conflict(["discharge_commit_conflict"])

        return Response(response_data, status=status.HTTP_201_CREATED)

    def _create_command(self, encounter, request_spec, payload_hash, snapshot):
        command = EncounterDischargeCommand(
            client_request_id=request_spec.client_request_id,
            payload_hash=payload_hash,
            command_hash="",
            actor=self.request.user,
            encounter=encounter,
            result_snapshot=snapshot,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        command.command_hash = encounter_discharge_command_hash(
            self._command_hash_material(command)
        )
        command.save(force_insert=True)
        return self._response_data(
            request_spec.client_request_id,
            snapshot,
            replayed=False,
        )

    def _command_replay(self, request_spec, encounter, payload_hash):
        command = (
            EncounterDischargeCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "encounter",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if command is None:
            return None
        if not all(
            [
                not command.deleted,
                command.actor_id == self.request.user.id,
                command.encounter_id == encounter.id,
                command.payload_hash == payload_hash,
            ]
        ):
            return self._conflict(["idempotency_conflict"])
        if command.command_hash != encounter_discharge_command_hash(
            self._command_hash_material(command)
        ) or not discharge_snapshot_matches_encounter(
            command.result_snapshot,
            command.encounter,
        ):
            return self._conflict(["discharge_integrity_failed"])
        return Response(
            self._response_data(
                request_spec.client_request_id,
                command.result_snapshot,
                replayed=True,
            ),
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _command_hash_material(command):
        return {
            "actor": command.actor.external_id,
            "client_request_id": command.client_request_id,
            "encounter": command.encounter.external_id,
            "payload_hash": command.payload_hash,
            "result_snapshot": command.result_snapshot,
        }

    @staticmethod
    def _response_data(client_request_id, snapshot, *, replayed):
        return {
            "client_request_id": str(client_request_id),
            "replayed": replayed,
            "discharge": snapshot,
        }

    def _authorize_discharge(self, encounter):
        can_read = AuthorizationController.call(
            "can_view_clinical_data",
            self.request.user,
            encounter.patient,
        ) or (
            AuthorizationController.call(
                "can_view_encounter_obj",
                self.request.user,
                encounter,
            )
            and AuthorizationController.call(
                "can_view_encounter_clinical_data",
                self.request.user,
                encounter,
            )
        )
        can_write = AuthorizationController.call(
            "can_update_encounter_obj",
            self.request.user,
            encounter,
        ) and AuthorizationController.call(
            "can_update_encounter_clinical_data",
            self.request.user,
            encounter,
        )
        if not can_read or not can_write:
            raise PermissionDenied("Permission denied for encounter discharge")

    def _reference_encounter(self):
        reference = get_object_or_404(
            Encounter._base_manager.select_related("patient", "facility"),  # noqa: SLF001
            external_id=self.kwargs["external_id"],
            deleted=False,
        )
        if reference.external_id != self.kwargs["external_id"]:
            raise Http404("Encounter discharge context not found")
        return reference

    @staticmethod
    def _conflict(blocker_codes):
        return Response(
            {"blocker_codes": sorted(set(blocker_codes))},
            status=status.HTTP_409_CONFLICT,
        )

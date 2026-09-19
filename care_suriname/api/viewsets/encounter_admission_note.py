from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.extensions.base import ExtensionResource
from care.emr.models import Encounter
from care.emr.registries.extensions.registry import ExtensionRegistry
from care.emr.resources.encounter.admission_note import (
    SetEncounterAdmissionNoteSpec,
)
from care.emr.resources.encounter.constants import CLINICALLY_CLOSED_CHOICES
from care.emr.resources.encounter.spec import EncounterRetrieveSpec
from care.emr.workflow_capabilities import WorkflowCapabilityDisabled
from care.security.authorization import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.extensions.encounter_admission_note import (
    ADMISSION_NOTE_EXTENSION_NAME,
)


class EncounterAdmissionNoteViewSet(
    ClinicalNoStoreResponseMixin,
    EMRBaseViewSet,
):
    """Narrow command for the governed native admission-note extension."""

    database_model = Encounter

    @extend_schema(
        request=SetEncounterAdmissionNoteSpec,
        responses={200: EncounterRetrieveSpec},
    )
    def set_admission_note(self, request, *args, **kwargs):
        request_spec = SetEncounterAdmissionNoteSpec.model_validate(request.data)
        extension_value = self._validated_extension_value(request_spec.text)

        with transaction.atomic():
            encounter = get_object_or_404(
                Encounter._base_manager.select_for_update(of=("self",)).select_related(  # noqa: SLF001
                    "patient",
                    "facility",
                    "appointment",
                    "current_location",
                    "created_by",
                    "updated_by",
                ),
                external_id=self.kwargs["external_id"],
                deleted=False,
            )
            if not AuthorizationController.call(
                "can_update_encounter_obj",
                request.user,
                encounter,
            ):
                raise PermissionDenied("You do not have permission to update encounter")
            if encounter.status in CLINICALLY_CLOSED_CHOICES:
                raise ValidationError("Clinically closed encounters are immutable")
            self._apply_extension_value(encounter, extension_value, request.user)

        return Response(
            EncounterRetrieveSpec.serialize(
                encounter,
                request.user,
            ).to_json()
        )

    @staticmethod
    def _validated_extension_value(text):
        handler = ExtensionRegistry.get_extension_obj(
            ExtensionResource.encounter.value,
            ADMISSION_NOTE_EXTENSION_NAME,
        )
        if handler is None:
            raise WorkflowCapabilityDisabled("admission_note_extension_unavailable")
        value = {"text": text}
        try:
            handler.validate(value)
        except ValueError as error:
            raise ValidationError({"text": "Invalid admission note"}) from error
        return handler.serialize_extensions(value)

    @staticmethod
    def _apply_extension_value(encounter, value, actor):
        current_extensions = encounter.extensions or {}
        if current_extensions.get(ADMISSION_NOTE_EXTENSION_NAME) == value:
            return
        extensions = dict(current_extensions)
        extensions[ADMISSION_NOTE_EXTENSION_NAME] = value
        modified_date = timezone.now()
        Encounter._base_manager.filter(pk=encounter.pk).update(  # noqa: SLF001
            extensions=extensions,
            updated_by=actor,
            modified_date=modified_date,
        )
        encounter.extensions = extensions
        encounter.updated_by = actor
        encounter.modified_date = modified_date

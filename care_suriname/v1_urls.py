"""Suriname endpoints under `api/v1/`, included through the seam in config/urls.py.

Prefixes and basenames are unchanged from the former registrations in
config/api_router.py, so URLs and `reverse()` names stay the same.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from care_suriname.api.viewsets.clinical_term_translation import (
    ClinicalTermTranslationViewSet,
)
from care_suriname.api.viewsets.clinical_text import ClinicalTextResourceViewSet
from care_suriname.api.viewsets.consult_closure import ConsultClosureViewSet
from care_suriname.api.viewsets.correspondence import (
    CorrespondenceCompilationViewSet,
)
from care_suriname.api.viewsets.correspondence_continuity import (
    CorrespondenceContinuityViewSet,
)
from care_suriname.api.viewsets.correspondence_correction_case import (
    CorrespondenceCorrectionCaseViewSet,
)
from care_suriname.api.viewsets.correspondence_delivery import (
    CorrespondenceDeliveryViewSet,
)
from care_suriname.api.viewsets.correspondence_letter import (
    CorrespondenceLetterViewSet,
)
from care_suriname.api.viewsets.correspondence_review import (
    CorrespondenceRecipientViewSet,
    CorrespondenceReviewViewSet,
)
from care_suriname.api.viewsets.encounter_admission_note import (
    EncounterAdmissionNoteViewSet,
)
from care_suriname.api.viewsets.encounter_discharge import (
    EncounterDischargeViewSet,
)
from care_suriname.api.viewsets.patient_directory import PatientDirectoryViewSet
from care_suriname.api.viewsets.workflow_capability import (
    WorkflowCapabilityViewSet,
)
from care_suriname.draft_recovery.views import DraftRecoveryKeyView

router = SimpleRouter(trailing_slash=True)
router.register(
    "clinical_text_resource",
    ClinicalTextResourceViewSet,
    basename="clinical_text_resource",
)
router.register(
    "clinical_term_translation",
    ClinicalTermTranslationViewSet,
    basename="clinical_term_translation",
)
router.register(
    "correspondence_compilation",
    CorrespondenceCompilationViewSet,
    basename="correspondence-compilation",
)
router.register(
    "correspondence_recipient",
    CorrespondenceRecipientViewSet,
    basename="correspondence-recipient",
)
router.register(
    "correspondence_review",
    CorrespondenceReviewViewSet,
    basename="correspondence-review",
)
router.register(
    "correspondence_letter",
    CorrespondenceLetterViewSet,
    basename="correspondence-letter",
)
router.register(
    "correspondence_delivery",
    CorrespondenceDeliveryViewSet,
    basename="correspondence-delivery",
)
router.register(
    "correspondence_continuity",
    CorrespondenceContinuityViewSet,
    basename="correspondence-continuity",
)
router.register(
    "correspondence_correction_cases",
    CorrespondenceCorrectionCaseViewSet,
    basename="correspondence-correction-case",
)
router.register(
    "consult_closures",
    ConsultClosureViewSet,
    basename="consult-closure",
)
router.register(
    "workflow_capabilities",
    WorkflowCapabilityViewSet,
    basename="workflow-capability",
)

# Explicit compatibility path: native patient/<external_id> otherwise captures
# the literal "directory". Other plugin routes retain their original precedence.
priority_urlpatterns = [
    path(
        "patient/directory/",
        PatientDirectoryViewSet.as_view(
            {"get": "directory"},
            basename="patient",
            detail=False,
            **PatientDirectoryViewSet.directory.kwargs,
        ),
        name="patient-directory",
    ),
]

urlpatterns = [
    path(
        "encounter/<uuid:external_id>/set-admission-note/",
        EncounterAdmissionNoteViewSet.as_view({"post": "set_admission_note"}),
        name="encounter-set-admission-note",
    ),
    path(
        "encounter/<uuid:external_id>/preflight-discharge/",
        EncounterDischargeViewSet.as_view({"post": "preflight_discharge"}),
        name="encounter-preflight-discharge",
    ),
    path(
        "encounter/<uuid:external_id>/idempotent-discharge/",
        EncounterDischargeViewSet.as_view({"post": "idempotent_discharge"}),
        name="encounter-idempotent-discharge",
    ),
    path(
        "users/me/draft-recovery-key/",
        DraftRecoveryKeyView.as_view(),
        name="draft-recovery-key",
    ),
    path(
        "users/me/draft-recovery-key/<uuid:key_id>/",
        DraftRecoveryKeyView.as_view(),
        name="draft-recovery-key-detail",
    ),
    *router.urls,
]

from django.urls import path

from care_suriname.api.viewsets.consult_closure_medication import (
    ClosureMedicationIssuesView,
    ClosureMedicationRetractionView,
)
from care_suriname.api.viewsets.declaration_codes import (
    DeclarationCodeEnteredInErrorView,
)
from care_suriname.api.viewsets.laboratory import (
    LaboratoryCommandView,
    LaboratoryDefinitionListView,
    LaboratoryReportListView,
    LaboratoryReportView,
)
from care_suriname.scribe.views import ScribeFieldDraftView

# Mounted by CARE at `api/care_suriname/` (config/urls.py, PLUGIN_APPS loop).
urlpatterns = [
    path(
        "consult-closure/<uuid:encounter_id>/medication-issues/",
        ClosureMedicationIssuesView.as_view(),
        name="consult-closure-medication-issues",
    ),
    path(
        "consult-closure/<uuid:encounter_id>/medication-retractions/",
        ClosureMedicationRetractionView.as_view(),
        name="consult-closure-medication-retractions",
    ),
    path(
        "declaration-codes/charge-items/<uuid:charge_item_id>/enter-in-error/",
        DeclarationCodeEnteredInErrorView.as_view(),
        name="declaration-code-entered-in-error",
    ),
    path(
        "laboratory/definitions/",
        LaboratoryDefinitionListView.as_view(),
        name="laboratory-definition-list",
    ),
    path(
        "laboratory/report-commands/",
        LaboratoryCommandView.as_view(),
        name="laboratory-report-command",
    ),
    path(
        "laboratory/reports/",
        LaboratoryReportListView.as_view(),
        name="laboratory-report-list",
    ),
    path(
        "laboratory/reports/<uuid:report_id>/",
        LaboratoryReportView.as_view(),
        name="laboratory-report-detail",
    ),
    path(
        "scribe/field-drafts/",
        ScribeFieldDraftView.as_view(),
        name="scribe-field-drafts",
    ),
]

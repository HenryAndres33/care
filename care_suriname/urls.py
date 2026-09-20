from django.urls import path

from care_suriname.api.viewsets.laboratory import (
    LaboratoryCommandView,
    LaboratoryDefinitionListView,
    LaboratoryReportListView,
    LaboratoryReportView,
)

# Mounted by CARE at `api/care_suriname/` (config/urls.py, PLUGIN_APPS loop).
urlpatterns = [
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
]

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from care.emr.api.viewsets.form_submission import FormSubmissionViewSet
from care.emr.api.viewsets.medication_request import MedicationRequestViewSet
from care.emr.api.viewsets.report.report_upload import ReportUploadViewSet
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care_suriname.api.viewsets.consult_closure import ConsultClosureViewSet
from care_suriname.api.viewsets.correspondence import CorrespondenceCompilationViewSet
from care_suriname.api.viewsets.correspondence_continuity import (
    CorrespondenceContinuityViewSet,
)
from care_suriname.api.viewsets.correspondence_correction_case import (
    CorrespondenceCorrectionCaseViewSet,
)
from care_suriname.api.viewsets.correspondence_delivery import (
    CorrespondenceDeliveryViewSet,
)
from care_suriname.api.viewsets.correspondence_letter import CorrespondenceLetterViewSet
from care_suriname.api.viewsets.correspondence_review import (
    CorrespondenceRecipientViewSet,
    CorrespondenceReviewViewSet,
)


class NoStoreProbeView(ClinicalNoStoreResponseMixin, APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        if request.query_params.get("invalid"):
            raise ValidationError("invalid")
        return Response({"ok": True})


class TestClinicalNoStore(SimpleTestCase):
    def test_no_store_mixin_covers_success_and_error_responses(self):
        view = NoStoreProbeView.as_view()
        factory = APIRequestFactory()

        for path, expected_status in [
            ("/probe/", 200),
            ("/probe/?invalid=1", 400),
        ]:
            response = view(factory.get(path))

            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["Pragma"], "no-cache")
            self.assertGreaterEqual(
                set(response["Vary"].split(", ")),
                {"Authorization", "Cookie"},
            )

    def test_all_correspondence_and_closure_viewsets_use_no_store_mixin(self):
        protected_viewsets = [
            CorrespondenceCompilationViewSet,
            CorrespondenceRecipientViewSet,
            CorrespondenceReviewViewSet,
            CorrespondenceLetterViewSet,
            CorrespondenceDeliveryViewSet,
            CorrespondenceContinuityViewSet,
            CorrespondenceCorrectionCaseViewSet,
            ConsultClosureViewSet,
            FormSubmissionViewSet,
            MedicationRequestViewSet,
            ReportUploadViewSet,
        ]

        self.assertTrue(
            all(
                issubclass(viewset, ClinicalNoStoreResponseMixin)
                for viewset in protected_viewsets
            )
        )

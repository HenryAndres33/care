from http import HTTPStatus
from uuid import uuid4

from care.emr.tests.test_correspondence_review import CorrespondenceReviewTestMixin
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.models.correspondence_review import (
    CorrespondenceRecipient,
    CorrespondenceRecipientCommand,
)


class ManualRecipientAuthorizationTests(
    CorrespondenceReviewTestMixin,
    CareAPITestBase,
):
    """A manual recipient is stored as verified, so viewing is not enough."""

    def setUp(self):
        super().setUp()
        self.build_review_context()

    def _post_as(self, user):
        self.client.force_authenticate(user=user)
        return self.client.post(
            self.manual_recipient_url,
            {
                "client_request_id": str(uuid4()),
                "patient": str(self.patient.external_id),
                "facility": str(self.facility.external_id),
                "display_name": "Dr. Chigaroe",
            },
            format="json",
        )

    def _user_with(self, permissions):
        user = self.create_user(verified=True)
        role = self.create_role_with_permissions(permissions)
        self.attach_role_facility_organization_user(self.organization, user, role)
        return user

    def test_clinical_reader_cannot_store_a_verified_recipient(self):
        reader = self._user_with(
            [
                PatientPermissions.can_view_clinical_data.name,
                EncounterPermissions.can_read_encounter.name,
            ]
        )
        recipients = CorrespondenceRecipient.objects.count()

        response = self._post_as(reader)

        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(CorrespondenceRecipient.objects.count(), recipients)
        self.assertFalse(CorrespondenceRecipientCommand.objects.exists())

    def test_letter_author_can_store_a_recipient(self):
        author = self._user_with(
            [
                PatientPermissions.can_view_clinical_data.name,
                EncounterPermissions.can_write_encounter.name,
            ]
        )

        response = self._post_as(author)

        self.assertEqual(response.status_code, HTTPStatus.CREATED)

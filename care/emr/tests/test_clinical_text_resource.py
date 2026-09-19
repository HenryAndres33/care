from http import HTTPStatus

from django.urls import reverse

from care.utils.tests.base import CareAPITestBase
from care_suriname.models.clinical_text import ClinicalTextResource


class TestClinicalTextResourceAPI(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.facility = self.create_facility(user=self.user)
        self.client.force_authenticate(user=self.user)

    def template_payload(self, **overrides):
        payload = {
            "facility": str(self.facility.external_id),
            "kind": "template",
            "key": ".turp",
            "label": "TURP operatie",
            "description": "Operatieverslag",
            "status": "active",
            "payload": {
                "name": "TURP operatie",
                "description": "Operatieverslag",
                "shortcut": ".turp",
                "body": "Patiënt onder [[*ANESTHESIE|choice:Spinaal,Algeheel*]].",
                "scopes": ["urology", "operations"],
            },
        }
        payload.update(overrides)
        return payload

    def create_template(self):
        return self.client.post(
            reverse("clinical_text_resource-list"),
            self.template_payload(),
            format="json",
        )

    def test_create_list_and_audit_facility_resource(self):
        created = self.create_template()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        self.assertEqual(created.json()["version"], 1)
        self.assertEqual(created.json()["created_by"]["id"], str(self.user.external_id))

        listed = self.client.get(
            reverse("clinical_text_resource-list"),
            {"facility": str(self.facility.external_id), "kind": "template"},
        )
        self.assertEqual(listed.status_code, HTTPStatus.OK, listed.json())
        self.assertEqual(listed.json()["count"], 1)
        self.assertEqual(listed.json()["results"][0]["key"], ".turp")

    def test_duplicate_key_and_stale_update_conflict(self):
        created = self.create_template()
        duplicate = self.create_template()
        self.assertEqual(duplicate.status_code, HTTPStatus.CONFLICT)

        resource_id = created.json()["id"]
        update = self.template_payload(expected_version=1, label="TURP gewijzigd")
        updated = self.client.put(
            reverse(
                "clinical_text_resource-detail",
                kwargs={"external_id": resource_id},
            ),
            update,
            format="json",
        )
        self.assertEqual(updated.status_code, HTTPStatus.OK, updated.json())
        self.assertEqual(updated.json()["version"], 2)

        stale = self.client.put(
            reverse(
                "clinical_text_resource-detail",
                kwargs={"external_id": resource_id},
            ),
            update,
            format="json",
        )
        self.assertEqual(stale.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(stale.json()["current_version"], 2)

    def test_payload_validation_and_archive_are_server_side(self):
        invalid = self.template_payload(
            payload={
                "name": "Mismatch",
                "description": "",
                "shortcut": ".different",
                "body": "text",
                "scopes": ["urology"],
            }
        )
        rejected = self.client.post(
            reverse("clinical_text_resource-list"), invalid, format="json"
        )
        self.assertEqual(rejected.status_code, HTTPStatus.BAD_REQUEST)

        created = self.create_template().json()
        archived_payload = self.template_payload(
            expected_version=created["version"], status="archived"
        )
        archived = self.client.put(
            reverse(
                "clinical_text_resource-detail",
                kwargs={"external_id": created["id"]},
            ),
            archived_payload,
            format="json",
        )
        self.assertEqual(archived.status_code, HTTPStatus.OK, archived.json())
        self.assertEqual(archived.json()["status"], "archived")
        self.assertTrue(
            ClinicalTextResource.objects.filter(
                external_id=created["id"], deleted=False
            ).exists()
        )

    def test_retrieve_needs_no_facility_query_parameter(self):
        """The plugin reads a single resource by id before archiving it; that
        read used to answer 400 "Facility is required" (17 September 2026)."""
        created = self.client.post(
            "/api/v1/clinical_text_resource/",
            self.template_payload(),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        resource_id = created.data["id"]

        fetched = self.client.get(f"/api/v1/clinical_text_resource/{resource_id}/")
        self.assertEqual(fetched.status_code, 200, fetched.data)
        self.assertEqual(fetched.data["key"], ".turp")
        self.assertEqual(fetched.data["facility"], str(self.facility.external_id))

        unknown = self.client.get(
            "/api/v1/clinical_text_resource/00000000-0000-4000-8000-000000000000/"
        )
        self.assertEqual(unknown.status_code, 404)

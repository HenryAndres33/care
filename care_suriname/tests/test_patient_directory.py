"""Directory authorization, identity shape and counted search paging."""

import datetime

from django.urls import reverse
from rest_framework import status

from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase


class PatientDirectoryTests(CareAPITestBase):
    def test_directory_search_for_facility_secretary(self):
        creator = self.create_user()
        facility = self.create_facility(creator)
        facility_organization = self.create_facility_organization(
            facility,
            org_type="root",
        )
        secretary = self.create_user()
        secretary_role = self.create_role_with_permissions(
            permissions=[PatientPermissions.can_list_patients.name]
        )
        self.attach_role_facility_organization_user(
            facility_organization,
            secretary,
            secretary_role,
        )
        matching_patient = self.create_patient(
            name="Henry Directory Test",
            date_of_birth=datetime.date(1992, 1, 9),
        )
        self.create_patient(name="Unrelated Patient")
        self.client.force_authenticate(user=secretary)

        response = self.client.get(
            reverse("patient-directory"),
            {
                "facility": str(facility.external_id),
                "name": "henry",
                "date_of_birth": "1992-01-09",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"],
            str(matching_patient.external_id),
        )
        self.assertEqual(
            set(response.data["results"][0]),
            {
                "id",
                "name",
                "gender",
                "phone_number",
                "date_of_birth",
                "year_of_birth",
            },
        )

    def test_directory_search_rejects_user_outside_facility(self):
        creator = self.create_user()
        facility = self.create_facility(creator)
        unrelated_user = self.create_user()
        self.client.force_authenticate(user=unrelated_user)

        response = self.client.get(
            reverse("patient-directory"),
            {
                "facility": str(facility.external_id),
                "name": "patient",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_directory_search_requires_meaningful_criteria(self):
        superuser = self.create_super_user()
        facility = self.create_facility(superuser)
        self.client.force_authenticate(user=superuser)

        empty_response = self.client.get(
            reverse("patient-directory"),
            {"facility": str(facility.external_id)},
        )
        short_name_response = self.client.get(
            reverse("patient-directory"),
            {"facility": str(facility.external_id), "name": "h"},
        )

        self.assertEqual(empty_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(short_name_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_directory_search_is_counted_and_offset_paginated(self):
        creator = self.create_user()
        facility = self.create_facility(creator)
        facility_organization = self.create_facility_organization(
            facility,
            org_type="root",
        )
        secretary = self.create_user()
        secretary_role = self.create_role_with_permissions(
            permissions=[PatientPermissions.can_list_patients.name]
        )
        self.attach_role_facility_organization_user(
            facility_organization,
            secretary,
            secretary_role,
        )
        patients = [
            self.create_patient(name=f"Pagination Test {index:02d}")
            for index in range(3)
        ]
        self.create_patient(name="Unrelated Patient")
        self.client.force_authenticate(user=secretary)
        url = reverse("patient-directory")

        first_page = self.client.get(
            url,
            {
                "facility": str(facility.external_id),
                "name": "Pagination Test",
                "limit": 2,
                "offset": 0,
                "ordering": "name",
            },
        )
        second_page = self.client.get(
            url,
            {
                "facility": str(facility.external_id),
                "name": "Pagination Test",
                "limit": 2,
                "offset": 2,
                "ordering": "name",
            },
        )

        self.assertEqual(first_page.status_code, status.HTTP_200_OK)
        self.assertEqual(second_page.status_code, status.HTTP_200_OK)
        self.assertEqual(first_page.data["count"], 3)
        self.assertEqual(second_page.data["count"], 3)
        self.assertEqual(len(first_page.data["results"]), 2)
        self.assertEqual(len(second_page.data["results"]), 1)
        returned_ids = {
            result["id"]
            for result in first_page.data["results"] + second_page.data["results"]
        }
        self.assertEqual(
            returned_ids,
            {str(patient.external_id) for patient in patients},
        )

    def test_validation_bounds_and_missing_facility(self):
        user = self.create_super_user()
        facility = self.create_facility(user)
        self.client.force_authenticate(user=user)
        base = {"facility": str(facility.external_id), "name": "Synthetic"}
        for overrides in (
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {"ordering": "created_date"},
            {"date_of_birth": "invalid"},
            {"facility": "invalid"},
            {"name": " x "},
        ):
            with self.subTest(overrides=overrides):
                response = self.client.get(
                    reverse("patient-directory"), base | overrides
                )
                self.assertEqual(response.status_code, 400)
        missing = self.client.get(
            reverse("patient-directory"),
            base | {"facility": "00000000-0000-4000-8000-000000000001"},
        )
        self.assertEqual(missing.status_code, 404)

    def test_birth_date_only_ties_ordering_and_empty_offset(self):
        user = self.create_super_user()
        facility = self.create_facility(user)
        self.client.force_authenticate(user=user)
        patients = [
            self.create_patient(
                name="Synthetic Duplicate", date_of_birth=datetime.date(1991, 4, 3)
            )
            for _ in range(3)
        ]
        params = {
            "facility": str(facility.external_id),
            "date_of_birth": "1991-04-03",
            "limit": 1,
        }
        url = reverse("patient-directory")
        expected = sorted(str(patient.external_id) for patient in patients)
        for ordering, ids in (
            ("name", expected),
            ("-name", expected),
            ("external_id", expected),
            ("-external_id", expected[::-1]),
            ("date_of_birth", expected),
            ("-date_of_birth", expected),
        ):
            returned = []
            for offset in range(3):
                result = self.client.get(
                    url, params | {"ordering": ordering, "offset": offset}
                )
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.data["count"], 3)
                returned.append(result.data["results"][0]["id"])
            self.assertEqual(returned, ids)
        empty = self.client.get(url, params | {"offset": 100})
        self.assertEqual(empty.data, {"count": 3, "results": []})

    def test_authentication_method_and_unknown_url_behavior(self):
        url = reverse("patient-directory")
        self.assertEqual(self.client.get(url).status_code, 403)
        user = self.create_super_user()
        facility = self.create_facility(user)
        self.client.force_authenticate(user=user)
        metadata = self.client.options(url)
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.data["name"], "Directory")
        self.assertEqual(self.client.post(url, {}).status_code, 405)
        self.assertEqual(self.client.get(url + "unknown/").status_code, 404)
        self.assertEqual(
            self.client.get(
                "/api/v1/patient/00000000-0000-4000-8000-000000000001/"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                url, {"facility": str(facility.external_id), "name": "NoSyntheticMatch"}
            ).data,
            {"count": 0, "results": []},
        )

    def test_default_limit_and_phone_ordering(self):
        user = self.create_super_user()
        facility = self.create_facility(user)
        self.client.force_authenticate(user=user)
        patients = [
            self.create_patient(
                name="Synthetic Count", phone_number=f"+597877{index:04d}"
            )
            for index in range(26)
        ]
        params = {
            "facility": str(facility.external_id),
            "name": "Synthetic Count",
            "ordering": "phone_number",
        }
        first = self.client.get(reverse("patient-directory"), params)
        self.assertEqual(first.data["count"], 26)
        self.assertEqual(len(first.data["results"]), 25)
        self.assertEqual(first.data["results"][0]["id"], str(patients[0].external_id))
        descending = self.client.get(
            reverse("patient-directory"),
            params | {"ordering": "-phone_number", "limit": 100},
        )
        self.assertEqual(
            [row["id"] for row in descending.data["results"]],
            [str(patient.external_id) for patient in reversed(patients)],
        )

    def test_directory_permission_in_one_facility_does_not_grant_another(self):
        creator = self.create_user()
        allowed = self.create_facility(creator)
        other = self.create_facility(creator)
        organization = self.create_facility_organization(allowed, org_type="root")
        staff = self.create_user()
        role = self.create_role_with_permissions(
            permissions=[PatientPermissions.can_list_patients.name]
        )
        self.attach_role_facility_organization_user(organization, staff, role)
        self.client.force_authenticate(user=staff)
        response = self.client.get(
            reverse("patient-directory"),
            {"facility": str(other.external_id), "name": "Synthetic"},
        )
        self.assertEqual(response.status_code, 403)

from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from model_bakery import baker

from care.emr.fhir.resources.code_concept import MinimalCodeConcept
from care.emr.models import (
    ValueSet,
)
from care.utils.tests.base import CareAPITestBase
from care_suriname.models.clinical_term_translation import (
    ClinicalTermTranslation,
)
from care_suriname.resources.clinical_term_translation import parse_csv_import

SYSTEM = "http://snomed.info/sct"


class TestClinicalTermTranslationAPI(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_super_user(username="terminology-admin")
        self.user_one = self.create_user(username="clinician-one")
        self.user_two = self.create_user(username="clinician-two")
        self.list_url = reverse("clinical_term_translation-list")
        self.resolve_url = reverse("clinical_term_translation-resolve")
        self.search_url = reverse("clinical_term_translation-search")
        self.import_url = reverse("clinical_term_translation-bulk-import")
        self.export_url = reverse("clinical_term_translation-export-csv")

    def payload(self, **overrides):
        payload = {
            "system": SYSTEM,
            "code": "TEST-100",
            "language": "nl-SR",
            "source_display": "Test source term",
            "preferred_display": "Nederlandse testterm",
            "synonyms": ["testsynoniem"],
            "status": "draft",
            "source_name": "Clinically reviewed local terminology",
            "source_version": "2026.07",
            "is_active": True,
        }
        payload.update(overrides)
        return payload

    def create_and_approve(self, **overrides):
        self.client.force_authenticate(self.admin)
        created = self.client.post(
            self.list_url, self.payload(**overrides), format="json"
        )
        self.assertEqual(created.status_code, 201, created.json())
        detail_url = reverse(
            "clinical_term_translation-detail",
            kwargs={"external_id": created.json()["id"]},
        )
        in_review_payload = self.payload(**overrides, status="in_review")
        in_review = self.client.put(detail_url, in_review_payload, format="json")
        self.assertEqual(in_review.status_code, 200, in_review.json())
        approved = self.client.put(
            detail_url,
            self.payload(**overrides, status="approved"),
            format="json",
        )
        self.assertEqual(approved.status_code, 200, approved.json())
        return approved.json()

    def test_unique_key_and_bounded_synonyms(self):
        ClinicalTermTranslation.objects.create(
            **self.payload(), created_by=self.admin, updated_by=self.admin
        )
        with self.assertRaises(DjangoValidationError):
            ClinicalTermTranslation.objects.create(
                **self.payload(preferred_display="Andere term"),
                created_by=self.admin,
                updated_by=self.admin,
            )

        self.client.force_authenticate(self.admin)
        duplicate = self.client.post(self.list_url, self.payload(), format="json")
        self.assertEqual(duplicate.status_code, 409)
        rejected = self.client.post(
            self.list_url,
            self.payload(code="TEST-101", synonyms=["x"] * 26),
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        blank_display = self.client.post(
            self.list_url,
            self.payload(code="TEST-102", preferred_display="   "),
            format="json",
        )
        self.assertEqual(blank_display.status_code, 400)

    def test_management_authorization_workflow_and_audit(self):
        self.client.force_authenticate(self.user_one)
        forbidden = self.client.post(self.list_url, self.payload(), format="json")
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_authenticate(self.admin)
        created = self.client.post(self.list_url, self.payload(), format="json")
        self.assertEqual(created.status_code, 201, created.json())
        self.assertEqual(
            created.json()["created_by"]["id"], str(self.admin.external_id)
        )
        detail_url = reverse(
            "clinical_term_translation-detail",
            kwargs={"external_id": created.json()["id"]},
        )
        skipped_review = self.client.put(
            detail_url, self.payload(status="approved"), format="json"
        )
        self.assertEqual(skipped_review.status_code, 400)

        in_review = self.client.put(
            detail_url, self.payload(status="in_review"), format="json"
        )
        self.assertEqual(in_review.status_code, 200, in_review.json())
        approved = self.client.put(
            detail_url, self.payload(status="approved"), format="json"
        )
        self.assertEqual(approved.status_code, 200, approved.json())
        self.assertEqual(
            approved.json()["reviewed_by"]["id"], str(self.admin.external_id)
        )
        self.assertIsNotNone(approved.json()["reviewed_at"])

    def test_draft_and_inactive_terms_never_resolve_or_search(self):
        ClinicalTermTranslation.objects.create(
            **self.payload(code="DRAFT-1", preferred_display="Verborgen concept"),
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.create_and_approve(code="ACTIVE-1", preferred_display="Zichtbaar concept")
        approved = ClinicalTermTranslation.objects.get(code="ACTIVE-1")
        approved.is_active = False
        approved.save()

        self.client.force_authenticate(self.user_one)
        search = self.client.get(self.search_url, {"q": "concept", "language": "nl-SR"})
        self.assertEqual(search.status_code, 200, search.json())
        self.assertEqual(search.json()["results"], [])
        resolved = self.client.post(
            self.resolve_url,
            {
                "language": "nl-SR",
                "concepts": [
                    {"system": SYSTEM, "code": "DRAFT-1", "display": "Draft source"}
                ],
            },
            format="json",
        )
        self.assertEqual(resolved.json()["results"][0]["display"], "Draft source")

    def test_synonym_language_and_two_user_sessions_share_one_backend_term(self):
        approved = self.create_and_approve(
            code="SHARED-1",
            preferred_display="Niersteenlijden",
            synonyms=["nefrolithiasis"],
        )
        request_body = {
            "language": "nl-SR",
            "concepts": [
                {"system": SYSTEM, "code": "SHARED-1", "display": "Kidney calculus"}
            ],
        }
        session_results = []
        for user in (self.user_one, self.user_two):
            self.client.force_authenticate(user)
            response = self.client.post(self.resolve_url, request_body, format="json")
            self.assertEqual(response.status_code, 200, response.json())
            session_results.append(response.json()["results"][0])
        self.assertEqual(session_results[0], session_results[1])
        self.assertEqual(session_results[0]["display"], approved["preferred_display"])

        self.client.force_authenticate(self.user_two)
        synonym_search = self.client.get(
            self.search_url, {"q": "nefrolithiasis", "language": "nl-SR"}
        )
        self.assertEqual(synonym_search.json()["results"][0]["code"], "SHARED-1")
        wrong_language = self.client.post(
            self.resolve_url, {**request_body, "language": "nl-BE"}, format="json"
        )
        self.assertEqual(
            wrong_language.json()["results"][0]["display"], "Kidney calculus"
        )

    def test_exact_dutch_preferred_name_ranks_before_broader_match(self):
        self.create_and_approve(
            code="URETER-STONE",
            preferred_display="Uretersteen",
            synonyms=["uretercalculus"],
        )
        self.create_and_approve(
            code="KIDNEY-URETER-STONE",
            preferred_display="Niersteen met uretersteen",
            synonyms=["nier- en uretersteen"],
        )

        self.client.force_authenticate(self.user_one)
        response = self.client.get(
            self.search_url, {"q": "uretersteen", "language": "nl-SR"}
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["results"][0]["code"], "URETER-STONE")

    def test_controlled_csv_dry_run_import_and_export(self):
        csv_text = "\n".join(
            [
                "system,code,language,source_display,preferred_display,synonyms,status,source_name,source_version,is_active",
                f"{SYSTEM},CSV-1,nl-SR,Source term,Concept uit CSV,synoniem|alias,draft,review seed,1,true",
            ]
        )
        self.client.force_authenticate(self.admin)
        dry_run = self.client.post(
            self.import_url, {"csv_text": csv_text, "dry_run": True}, format="json"
        )
        self.assertEqual(dry_run.status_code, 200, dry_run.json())
        self.assertEqual(dry_run.json()["creates"], 1)
        self.assertFalse(ClinicalTermTranslation.objects.filter(code="CSV-1").exists())

        imported = self.client.post(
            self.import_url, {"csv_text": csv_text, "dry_run": False}, format="json"
        )
        self.assertEqual(imported.status_code, 200, imported.json())
        term = ClinicalTermTranslation.objects.get(code="CSV-1")
        self.assertEqual(term.created_by, self.admin)
        self.assertEqual(term.concept_kind, "condition")
        self.assertEqual(term.synonyms, ["synoniem", "alias"])

        exported = self.client.get(self.export_url)
        self.assertEqual(exported.status_code, 200)
        self.assertIn("CSV-1", exported.content.decode())
        self.assertIn("text/csv", exported["Content-Type"])

    def test_translation_is_global_and_not_patient_scoped(self):
        self.assertFalse(hasattr(ClinicalTermTranslation, "patient"))

    def test_user_reviewed_urology_catalog_has_verified_care_codes(self):
        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "care_suriname"
            / "fixtures"
            / "nl_sr_urology_user_reviewed_terms.csv"
        )
        specs = parse_csv_import(fixture_path.read_text(encoding="utf-8"))
        by_code = {spec.code: spec for spec in specs}

        self.assertEqual(len(specs), 56)
        self.assertEqual(len(by_code), 56)
        self.assertTrue(all(spec.status.value == "draft" for spec in specs))
        self.assertEqual(by_code["702391001"].preferred_display, "Niercelcarcinoom")
        self.assertEqual(by_code["31054009"].preferred_display, "Uretersteen")
        self.assertEqual(
            by_code["30041000119101"].preferred_display,
            "Benigne prostaathyperplasie met LUTS",
        )
        self.assertEqual(by_code["449826002"].preferred_display, "Fimosis")
        self.assertEqual(
            by_code["162116003"].preferred_display,
            "Pollakisurie / frequente mictie",
        )
        self.assertEqual(
            by_code["165232002"].preferred_display,
            "Urine-incontinentie (NOS)",
        )
        self.assertEqual(
            by_code["977021461000119108"].preferred_display,
            "Refluxnefropathie",
        )
        self.assertEqual(
            by_code["197811007"].preferred_display,
            "Vesico-ureterale reflux (VUR), algemeen",
        )
        self.assertEqual(
            by_code["765779008"].preferred_display,
            "Refluxnefropathie van de linker nier door VUR",
        )
        self.assertEqual(
            by_code["253900005"].preferred_display,
            "Congenitale kleppen in de urethra posterior (PUV)",
        )

        unsafe_supplied_codes = {
            "402517006",  # Basal cell carcinoma of upper back in CARE.
            "402516002",  # Basal cell carcinoma of abdomen in CARE.
            "266804008",  # Inactive respiratory procedure in CARE.
            "266805009",  # Inactive cardiovascular procedure in CARE.
            "271638000",  # Pulse regularly irregular in CARE.
            "282100009",  # Inactive adverse reaction concept in CARE.
            "41503000",  # Zinc compound in CARE.
            "47758006",  # Hepatitis B surface antigen measurement in CARE.
            "225577000",
            "56488005",
            "5322003",
            "75056006",
            "247353003",  # Inactive site-of-abdominal-pain concept in CARE.
            "412781008",
            "95476003",
            "86201000119100",
            "86202000119106",
            "204984004",
            "204991008",
            "95474000",  # Arteriovenous malformation of kidney in CARE.
            "12771007",
            "204870004",
            "205423005",  # Unrelated dysmorphic-feature concept in CARE.
        }
        self.assertTrue(unsafe_supplied_codes.isdisjoint(by_code))

    def test_user_reviewed_urology_procedure_catalog_has_care_codes(self):
        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "care_suriname"
            / "fixtures"
            / "nl_sr_urology_user_reviewed_procedures.csv"
        )
        specs = parse_csv_import(fixture_path.read_text(encoding="utf-8"))
        by_code = {spec.code: spec for spec in specs}

        self.assertEqual(len(specs), 12)
        self.assertEqual(len(by_code), 12)
        self.assertTrue(all(spec.concept_kind.value == "procedure" for spec in specs))
        self.assertEqual(
            by_code["90199006"].preferred_display,
            "TURP (Transurethrale resectie van de prostaat)",
        )
        self.assertEqual(
            by_code["386792000"].preferred_display,
            "TURBT (Transurethrale resectie van blaastumor)",
        )
        self.assertNotIn("—", by_code["90199006"].preferred_display)
        self.assertNotIn("—", by_code["386792000"].preferred_display)
        self.assertEqual(
            by_code["736683003"].preferred_display,
            "Plaatsen JJ-stent / Double-J",
        )

        unsafe_supplied_codes = {
            "176274000",
            "176245007",
            "429350009",
            "424911005",
            "65103006",
            "70862002",  # Contact person in CARE.
            "81723002",  # Amputation in CARE.
            "176717006",
            "176214002",
            "448455008",
        }
        self.assertTrue(unsafe_supplied_codes.isdisjoint(by_code))


class TestValueSetDutchTranslationIntegration(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.reviewer = self.create_super_user(username="reviewer")
        self.user_one = self.create_user(username="valueset-user-one")
        self.user_two = self.create_user(username="valueset-user-two")
        self.valueset = baker.make(
            ValueSet,
            slug="system-condition-code",
            name="Conditions",
            description="",
            compose={"include": [], "exclude": []},
            status="active",
            is_system_defined=True,
        )
        ClinicalTermTranslation.objects.create(
            system=SYSTEM,
            code="VS-1",
            language="nl-SR",
            source_display="Source prostate term",
            preferred_display="Centrale prostaatterm",
            synonyms=["prostaatsynoniem"],
            status="approved",
            source_name="reviewed source",
            source_version="1",
            reviewed_by=self.reviewer,
            reviewed_at=self.reviewer.date_joined,
            is_active=True,
            created_by=self.reviewer,
            updated_by=self.reviewer,
        )
        self.url = reverse("value-set-expand", kwargs={"slug": "system-condition-code"})

    @patch.object(ValueSet, "search")
    def test_existing_expand_shape_is_enriched_for_both_users(self, search):
        search.return_value = [
            MinimalCodeConcept(
                system=SYSTEM,
                code="VS-1",
                display="Source prostate term",
                designation=[],
            )
        ]
        responses = []
        for user in (self.user_one, self.user_two):
            self.client.force_authenticate(user)
            response = self.client.post(
                self.url,
                {"search": "prostate", "count": 25, "display_language": "nl-SR"},
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.json())
            responses.append(response.json())
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[0]["results"][0]["display"], "Centrale prostaatterm")
        self.assertEqual(responses[0]["results"][0]["system"], SYSTEM)
        self.assertEqual(responses[0]["results"][0]["code"], "VS-1")
        self.assertEqual(search.call_args.kwargs["display_language"], "en-gb")

    @patch.object(ValueSet, "search")
    def test_procedure_valueset_search_only_returns_procedure_translations(
        self, search
    ):
        search.return_value = []
        procedure_valueset = baker.make(
            ValueSet,
            slug="activity-definition-procedure-code",
            name="Procedures",
            description="",
            compose={"include": [], "exclude": []},
            status="active",
            is_system_defined=True,
        )
        common = {
            "system": SYSTEM,
            "language": "nl-SR",
            "synonyms": ["TURP"],
            "status": "approved",
            "source_name": "reviewed source",
            "source_version": "1",
            "reviewed_by": self.reviewer,
            "reviewed_at": self.reviewer.date_joined,
            "is_active": True,
            "created_by": self.reviewer,
            "updated_by": self.reviewer,
        }
        ClinicalTermTranslation.objects.create(
            **common,
            code="PROC-1",
            concept_kind="procedure",
            source_display="Transurethral prostatectomy",
            preferred_display="TURP (Transurethrale resectie van de prostaat)",
        )
        ClinicalTermTranslation.objects.create(
            **common,
            code="COND-1",
            concept_kind="condition",
            source_display="Unrelated condition",
            preferred_display="TURP-gerelateerde aandoening",
        )

        self.client.force_authenticate(self.user_one)
        response = self.client.post(
            reverse("value-set-expand", kwargs={"slug": procedure_valueset.slug}),
            {"search": "TURP", "count": 25, "display_language": "nl-SR"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(
            [item["code"] for item in response.json()["results"]],
            ["PROC-1"],
        )

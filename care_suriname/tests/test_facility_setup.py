import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from model_bakery import baker

from care.emr.models.activity_definition import ActivityDefinition
from care.emr.models.healthcare_service import HealthcareService
from care.emr.models.location import FacilityLocation
from care.emr.models.observation_definition import ObservationDefinition
from care.emr.models.organization import FacilityOrganization, Organization
from care.emr.models.questionnaire import Questionnaire, QuestionnaireOrganization
from care.emr.models.report.template import Template
from care.emr.models.tag_config import TagConfig
from care.facility.models import Facility
from care.security.models.permission import PermissionModel
from care.security.models.role import RoleModel, RolePermission
from care_suriname.models.clinical_term_translation import ClinicalTermTranslation
from care_suriname.models.clinical_text import ClinicalTextResource

HB_META = {"care_suriname": {"laboratory_reference": {"fingerprint": "abc"}}}


class FacilitySetupRoundTripTests(TestCase):
    def setUp(self):
        self.admin = baker.make(
            get_user_model(), username="setup-admin", is_superuser=True
        )
        self.source = baker.make(Facility, name="Bron", created_by=self.admin)
        self.department = baker.make(
            FacilityOrganization,
            facility=self.source,
            name="Urologie",
            org_type="dept",
            system_generated=False,
            parent=None,
        )
        self.make_tags()
        self.make_lab()
        self.make_places()
        baker.make(
            Template,
            facility=self.source,
            slug=Template.calculate_slug_from_facility(
                self.source.external_id, "huisartsbrief"
            ),
            name="Huisartsbrief",
            template_data="<p>{{ patient.name }}</p>",
        )
        self.make_questionnaire()
        for key, status in ((".consult", "active"), (".politest", "active")):
            baker.make(
                ClinicalTextResource,
                facility=self.source,
                kind="template",
                key=key,
                status=status,
                version=1,
                payload={"text": key},
            )
        permission = baker.make(PermissionModel, slug="can_setup_test")
        self.role = baker.make(RoleModel, name="Uroloog", is_system=False)
        baker.make(RolePermission, role=self.role, permission=permission)
        ClinicalTermTranslation.objects.create(
            system="http://snomed.info/sct",
            code="12345",
            language="nl",
            concept_kind="condition",
            source_display="Kidney stone",
            preferred_display="Niersteen",
            synonyms=[],
            source_name="SNOMED CT",
            source_version="2026-09",
            status="approved",
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )

    def make_tags(self):
        for display, status in (("Hematurie", "active"), ("DEMO-SIM", "archived")):
            baker.make(
                TagConfig,
                facility=self.source,
                facility_organization=self.department,
                organization=None,
                parent=None,
                display=display,
                status=status,
                category="clinical",
                resource="encounter",
            )

    def make_lab(self):
        self.creatinine = baker.make(
            ObservationDefinition,
            facility=self.source,
            slug=ObservationDefinition.calculate_slug_from_facility(
                self.source.external_id, "creatinine"
            ),
            title="Creatinine",
            status="active",
            meta=HB_META | {"other_plugin": {"drop": True}},
        )
        baker.make(
            ActivityDefinition,
            facility=self.source,
            slug=ActivityDefinition.calculate_slug_from_facility(
                self.source.external_id, "nierfunctie"
            ),
            title="Nierfunctie",
            latest=True,
            observation_result_requirements=[self.creatinine.id],
            specimen_requirements=[],
            charge_item_definitions=[],
            locations=[],
            tags=[],
            category=None,
            healthcare_service=None,
        )

    def make_places(self):
        ward = baker.make(
            FacilityLocation,
            facility=self.source,
            name="Afdeling Urologie",
            parent=None,
            current_encounter=None,
        )
        baker.make(
            HealthcareService,
            facility=self.source,
            name="Urologie AZP",
            locations=[ward.id],
            managing_organization=self.department,
        )

    def make_questionnaire(self):
        self.doctor_org = baker.make(
            Organization, org_type="role", name="Doctor", parent=None
        )
        self.form = baker.make(
            Questionnaire, slug="urology-medisch-dossier", tags=[], questions=[]
        )
        baker.make(
            QuestionnaireOrganization,
            questionnaire=self.form,
            organization=self.doctor_org,
        )

    def export(self, directory, *extra):
        path = Path(directory) / "setup.json"
        call_command(
            "export_facility_setup",
            "--facility-name",
            "Bron",
            "--output",
            str(path),
            "--questionnaire",
            "urology-medisch-dossier",
            "--exclude-clinical-text",
            ".politest",
            *extra,
            stdout=StringIO(),
        )
        return path

    def import_(self, path, name="Doel"):
        call_command(
            "import_facility_setup",
            "--input",
            str(path),
            "--user",
            "setup-admin",
            "--facility-name",
            name,
            stdout=StringIO(),
        )
        return Facility.objects.get(name=name)

    def test_export_leaves_demo_and_test_material_behind(self):
        with TemporaryDirectory() as directory:
            data = json.loads(self.export(directory).read_text())
        self.assertEqual([t["display"] for t in data["tag_configs"]], ["Hematurie"])
        self.assertEqual([t["key"] for t in data["clinical_text"]], [".consult"])
        self.assertEqual(data["observation_definitions"][0]["meta"], HB_META)
        self.assertNotIn("patients", data)

    def test_round_trip_rebuilds_ids_and_links(self):
        with TemporaryDirectory() as directory:
            path = self.export(directory)
            # A fresh server has no forms or translations yet.
            QuestionnaireOrganization.objects.all().delete()
            Questionnaire.objects.filter(slug="urology-medisch-dossier").delete()
            ClinicalTermTranslation.objects.all().delete()
            target = self.import_(path)

        prefix = f"f-{target.external_id}-"
        creatinine = ObservationDefinition.objects.get(slug=f"{prefix}creatinine")
        self.assertEqual(creatinine.meta, HB_META)
        panel = ActivityDefinition.objects.get(slug=f"{prefix}nierfunctie")
        self.assertEqual(panel.observation_result_requirements, [creatinine.id])
        self.assertTrue(Template.objects.filter(slug=f"{prefix}huisartsbrief").exists())

        department = FacilityOrganization.objects.get(facility=target, name="Urologie")
        tag = TagConfig.objects.get(facility=target)
        self.assertEqual(tag.facility_organization, department)
        service = HealthcareService.objects.get(facility=target)
        ward = FacilityLocation.objects.get(facility=target)
        self.assertEqual(service.locations, [ward.id])
        self.assertEqual(service.managing_organization, department)

        form = Questionnaire.objects.get(slug="urology-medisch-dossier")
        self.assertTrue(
            QuestionnaireOrganization.objects.filter(
                questionnaire=form, organization=self.doctor_org
            ).exists()
        )
        term = ClinicalTermTranslation.objects.get(code="12345")
        self.assertEqual(term.reviewed_by, self.admin)
        self.assertEqual(
            term.meta["care_suriname"]["imported_review"]["reviewed_by_username"],
            "setup-admin",
        )
        self.assertEqual(RoleModel.objects.filter(name="Uroloog").count(), 1)

    def test_conflict_rolls_back_everything(self):
        with TemporaryDirectory() as directory:
            path = self.export(directory)
            # The form still exists here, so the import must refuse it.
            before = Facility.objects.count()
            with self.assertRaisesMessage(CommandError, "Nothing was imported"):
                self.import_(path)
        self.assertEqual(Facility.objects.count(), before)
        self.assertFalse(Facility.objects.filter(name="Doel").exists())

    def test_same_facility_name_is_refused(self):
        with TemporaryDirectory() as directory:
            path = self.export(directory)
            with self.assertRaisesMessage(CommandError, "already exists"):
                self.import_(path, name="Bron")

    def test_role_with_other_permissions_is_refused(self):
        with TemporaryDirectory() as directory:
            path = self.export(directory)
            QuestionnaireOrganization.objects.all().delete()
            Questionnaire.objects.all().delete()
            RolePermission.objects.filter(role=self.role).delete()
            with self.assertRaisesMessage(CommandError, "other permissions"):
                self.import_(path)
        self.assertFalse(Facility.objects.filter(name="Doel").exists())

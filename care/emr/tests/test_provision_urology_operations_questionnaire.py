from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from model_bakery import baker

from care.emr.models.organization import Organization
from care.emr.models.questionnaire import Questionnaire, QuestionnaireOrganization


class ProvisionUrologyOperationsQuestionnaireTest(TestCase):
    def setUp(self):
        self.organization = baker.make(Organization)

    def test_command_is_idempotent(self):
        options = {"organizations": [str(self.organization.external_id)]}

        call_command("provision_urology_operations_questionnaire", **options)
        call_command("provision_urology_operations_questionnaire", **options)

        questionnaire = Questionnaire.objects.get(slug="urology-operaties")
        self.assertEqual(questionnaire.status, "active")
        self.assertEqual(questionnaire.subject_type, "encounter")
        self.assertEqual(
            QuestionnaireOrganization.objects.filter(
                questionnaire=questionnaire,
                organization=self.organization,
            ).count(),
            1,
        )

    def test_command_refuses_incompatible_existing_questionnaire(self):
        baker.make(
            Questionnaire,
            slug="urology-operaties",
            status="draft",
            subject_type="patient",
            questions=[],
        )

        with self.assertRaisesRegex(CommandError, "incompatible"):
            call_command(
                "provision_urology_operations_questionnaire",
                organizations=[str(self.organization.external_id)],
            )

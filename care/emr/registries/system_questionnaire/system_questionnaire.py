import uuid

from django.apps import apps

from care.emr.resources.questionnaire.spec import QuestionnaireStatus

BUILTIN_STRUCTURED_RESOURCE_MODELS = {
    "allergy_intolerance": "emr.AllergyIntolerance",
    "charge_item": "emr.ChargeItem",
    "diagnosis": "emr.Condition",
    "medication_administration": "emr.MedicationAdministration",
    "medication_request": "emr.MedicationRequest",
    "medication_statement": "emr.MedicationStatement",
    "service_request": "emr.ServiceRequest",
    "symptom": "emr.Condition",
}


class InternalQuestionnaireRegistry:
    _questionnaires = {}

    @classmethod
    def register(cls, view) -> None:
        cls._questionnaires[view.questionnaire_type] = view

    @classmethod
    def serialize(cls, view):
        return {
            "version": "1.0",
            "id": str(uuid.uuid4()),
            "title": view.questionnaire_title,
            "description": view.questionnaire_description,
            "slug": view.questionnaire_type,
            "type": view.questionnaire_type,
            "status": QuestionnaireStatus.active.value,
            "subject_type": view.questionnaire_subject_type,
        }

    @classmethod
    def search_questionnaire(cls, term):
        return [
            cls.serialize(cls._questionnaires[view])
            for view in cls._questionnaires
            if term in view
        ]

    @classmethod
    def check_type_exists(cls, questionnaire_type):
        return (
            questionnaire_type in cls._questionnaires
            or questionnaire_type in BUILTIN_STRUCTURED_RESOURCE_MODELS
        )

    @classmethod
    def get_resource_model(cls, questionnaire_type):
        view = cls._questionnaires.get(questionnaire_type)
        model = getattr(view, "database_model", None)
        if model:
            return model
        model_label = BUILTIN_STRUCTURED_RESOURCE_MODELS.get(questionnaire_type)
        return apps.get_model(model_label) if model_label else None

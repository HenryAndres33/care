"""Authorization methods the Suriname endpoints need, registered as plug handlers.

CARE's `AuthorizationController` looks methods up by name across all registered
handler classes, so a plug may add new `can_*` methods without editing the core
handler files. These three used to be added to core classes directly.

Not here on purpose: the completed-encounter role lookup in
`care/security/authorization/patient.py` (PATIENT_DEPARTMENT_ACCESS.md). It
stays a core patch because `care/emr/resources/permissions.py` instantiates
`PatientAccess` directly, bypassing the controller.
"""

from care.security.authorization import AuthorizationController
from care.security.authorization.base import AuthorizationHandler
from care.security.authorization.encounter import EncounterAccess
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.security.permissions.questionnaire_response_template import (
    QuestionnaireResponseTemplatePermissions,
)


class CareSurinamePatientAccess(AuthorizationHandler):
    def can_search_patient_directory(self, user, facility):
        """Allow identity-only lookup for staff appointed in this facility."""
        return self.check_permission_in_facility_organization(
            [PatientPermissions.can_list_patients.name],
            user,
            facility=facility,
        )


class CareSurinameEncounterAccess(AuthorizationHandler):
    """One new method only. Deliberately not a subclass of EncounterAccess: the
    controller registers every `can_*` method it finds on a handler instance,
    so a subclass would take over all inherited encounter methods as well."""

    def can_mark_encounter_questionnaire_entered_in_error(self, user, encounter):
        """Authorize a correction without reopening a completed encounter."""
        return EncounterAccess().check_permission_in_encounter(
            user,
            encounter,
            EncounterPermissions.can_submit_encounter_questionnaire.name,
        )


class CareSurinameQuestionnaireResponseTemplateAccess(AuthorizationHandler):
    def can_read_questionnaire_response_template(self, user, facility=None):
        permissions = [
            QuestionnaireResponseTemplatePermissions.can_read_questionnaire_response_template.name
        ]
        if facility:
            return self.check_permission_in_facility_organization(
                permissions,
                user,
                facility=facility,
            )
        return self.check_permission_in_organization(permissions, user)


HANDLERS = (
    CareSurinamePatientAccess,
    CareSurinameEncounterAccess,
    CareSurinameQuestionnaireResponseTemplateAccess,
)


def register_authorization_handlers():
    for handler in HANDLERS:
        if handler not in AuthorizationController.internal_authz_controllers:
            AuthorizationController.register_internal_controller(handler)

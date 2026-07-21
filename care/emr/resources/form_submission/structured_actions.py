import copy
import re
from collections import defaultdict
from uuid import UUID

from django.db.models import Q

from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import FormSubmission, QuestionnaireResponse
from care.emr.registries.system_questionnaire.system_questionnaire import (
    InternalQuestionnaireRegistry,
)

MAX_STRUCTURED_ACTION_LINKS = 100
MEDICATION_REQUEST_TYPE = "medication_request"
CONFIRMED_MEDICATION_STATUSES = {"active", "completed"}
UUID_VERSION_4 = 4
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InvalidStructuredClinicalActionLink(ValueError):  # noqa: N818
    pass


def clone_structured_clinical_action_links(
    *,
    source: FormSubmission,
    result: FormSubmission,
    actor,
) -> int:
    """Clone validated structured action links to a new immutable form version."""
    candidate_links = list(_structured_action_links(source).order_by("pk"))
    if len(candidate_links) > MAX_STRUCTURED_ACTION_LINKS:
        raise InvalidStructuredClinicalActionLink(
            "Finalized form has too many structured clinical-action links"
        )
    if not candidate_links:
        return 0

    parsed_links = [_parse_link(link, source) for link in candidate_links]
    locked_resources = _lock_linked_resources(parsed_links)

    locked_links = list(
        QuestionnaireResponse._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        )
        .filter(pk__in=[link.pk for link in candidate_links])
        .order_by("pk")
    )
    if [link.pk for link in locked_links] != [link.pk for link in candidate_links]:
        raise InvalidStructuredClinicalActionLink(
            "Structured clinical-action linkage changed during amendment"
        )

    reparsed_links = [_parse_link(link, source) for link in locked_links]
    if [item.identity for item in reparsed_links] != [
        item.identity for item in parsed_links
    ]:
        raise InvalidStructuredClinicalActionLink(
            "Structured clinical-action linkage changed during amendment"
        )

    seen = set()
    clones = []
    for parsed in reparsed_links:
        if parsed.identity in seen:
            raise InvalidStructuredClinicalActionLink(
                "Structured clinical-action linkage is ambiguous"
            )
        seen.add(parsed.identity)
        resource = locked_resources.get(parsed.identity)
        if not resource:
            raise InvalidStructuredClinicalActionLink(
                "Structured clinical-action target is unavailable"
            )
        target_provenance = _validate_linked_resource(
            parsed.response_type,
            resource,
            source,
        )
        clones.append(
            _clone_link(
                parsed.link,
                source=source,
                result=result,
                actor=actor,
                target_provenance=target_provenance,
            )
        )

    QuestionnaireResponse.objects.bulk_create(clones)
    return len(clones)


def _structured_action_links(source):
    return QuestionnaireResponse._base_manager.filter(  # noqa: SLF001
        Q(structured_response_type__isnull=False) | ~Q(structured_responses={}),
        form_submission=source,
    )


class _ParsedLink:
    def __init__(self, link, response_type, resource_id):
        self.link = link
        self.response_type = response_type
        self.resource_id = resource_id
        self.identity = (response_type, resource_id)


def _parse_link(link, source):
    response_type = link.structured_response_type
    structured = link.structured_responses
    if (
        link.deleted
        or link.status != "completed"
        or link.patient_id != source.patient_id
        or link.encounter_id != source.encounter_id
        or link.subject_id != source.patient.external_id
        or not isinstance(response_type, str)
        or not response_type.strip()
        or response_type != response_type.strip()
        or not isinstance(structured, dict)
        or set(structured) != {response_type}
    ):
        raise InvalidStructuredClinicalActionLink(
            "Structured clinical-action linkage is invalid"
        )
    action = structured[response_type]
    if (
        not isinstance(action, dict)
        or set(action) != {"id", "submit_type"}
        or action.get("submit_type") != "CREATE"
    ):
        raise InvalidStructuredClinicalActionLink(
            "Structured clinical-action payload is malformed"
        )
    try:
        resource_id = UUID(str(action.get("id")))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidStructuredClinicalActionLink(
            "Structured clinical-action target identifier is malformed"
        ) from exc
    return _ParsedLink(link, response_type, resource_id)


def _lock_linked_resources(parsed_links):
    requested_by_type = defaultdict(set)
    for parsed in parsed_links:
        requested_by_type[parsed.response_type].add(parsed.resource_id)

    locked = {}
    for response_type in sorted(requested_by_type):
        model = InternalQuestionnaireRegistry.get_resource_model(response_type)
        if not model or not hasattr(model, "_base_manager"):
            raise InvalidStructuredClinicalActionLink(
                "Structured clinical-action type is unsupported"
            )
        resources = list(
            model._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(external_id__in=requested_by_type[response_type])
            .order_by("external_id")
        )
        for resource in resources:
            locked[(response_type, resource.external_id)] = resource
    return locked


def _validate_linked_resource(response_type, resource, source):
    if (
        resource.deleted
        or not hasattr(resource, "patient_id")
        or resource.patient_id != source.patient_id
        or not hasattr(resource, "encounter_id")
        or resource.encounter_id != source.encounter_id
    ):
        raise InvalidStructuredClinicalActionLink(
            "Structured clinical-action target context is invalid"
        )
    provenance = {
        "id": str(resource.external_id),
        "type": response_type,
    }
    if response_type == MEDICATION_REQUEST_TYPE:
        if not isinstance(resource, MedicationRequest):
            raise InvalidStructuredClinicalActionLink(
                "Medication action target is invalid"
            )
        request_id = resource.client_request_id
        payload_hash = resource.client_request_payload_hash
        if (
            resource.status not in CONFIRMED_MEDICATION_STATUSES
            or resource.intent != "order"
            or resource.do_not_perform
            or not request_id
            or request_id.version != UUID_VERSION_4
            or not isinstance(payload_hash, str)
            or not SHA256_PATTERN.fullmatch(payload_hash)
        ):
            raise InvalidStructuredClinicalActionLink(
                "Medication action provenance is invalid"
            )
        provenance.update(
            {
                "client_request_id": str(request_id),
                "client_request_payload_hash": payload_hash,
            }
        )
    return provenance


def _clone_link(link, *, source, result, actor, target_provenance):
    meta = copy.deepcopy(link.meta)
    prior_lineage = meta.get("form_submission_linkage_lineage", {})
    root_response = prior_lineage.get(
        "root_questionnaire_response", str(link.external_id)
    )
    meta["form_submission_linkage_lineage"] = {
        "contract": "form-submission-structured-action-lineage-v1",
        "root_questionnaire_response": root_response,
        "source_form_submission": str(source.external_id),
        "source_questionnaire_response": str(link.external_id),
        "target": target_provenance,
    }
    return QuestionnaireResponse(
        questionnaire=link.questionnaire,
        subject_id=link.subject_id,
        responses=copy.deepcopy(link.responses),
        structured_responses=copy.deepcopy(link.structured_responses),
        structured_response_type=link.structured_response_type,
        patient=result.patient,
        encounter=result.encounter,
        form_submission=result,
        status="completed",
        meta=meta,
        created_by=actor,
        updated_by=actor,
    )

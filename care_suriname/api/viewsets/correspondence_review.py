import logging
from hashlib import sha256

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet, EMRRetrieveMixin
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.correspondence.author import (
    InvalidVerifiedAuthorError,
    verified_author_snapshot,
)
from care.emr.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    lock_current_finalized_form_series,
)
from care.emr.correspondence.recipient import (
    MAX_VERIFIED_RECIPIENT_RESULTS,
    InvalidVerifiedRecipientError,
    recipient_content_hash,
    recipient_snapshot,
    validate_verified_recipient,
)
from care.emr.correspondence.review import (
    correspondence_review_hash,
    reviewed_binding_frozen_integrity_valid,
)
from care.emr.correspondence.source import compilation_sources_available
from care.emr.models.encounter import Encounter
from care.emr.models.organization import (
    FacilityOrganization,
    FacilityOrganizationUser,
)
from care.emr.models.patient import Patient
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care.emr.resources.correspondence_review import (
    BindCorrespondenceReviewResponseSpec,
    BindCorrespondenceReviewSpec,
    CorrespondenceRecipientReadSpec,
    CorrespondenceReviewReadSpec,
    CreateManualCorrespondenceRecipientResponseSpec,
    CreateManualCorrespondenceRecipientSpec,
    RecipientDiscoveryResponseSpec,
    RecipientDiscoverySpec,
    canonical_manual_recipient_command_hash,
    canonical_review_command_hash,
)
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.facility.models import Facility
from care.security.authorization.base import AuthorizationController
from care.security.models import RoleModel
from care.users.models import User
from care.utils.shortcuts import get_object_or_404
from care_suriname.models.correspondence import CorrespondenceCompilation
from care_suriname.models.correspondence_review import (
    CorrespondenceRecipient,
    CorrespondenceRecipientCommand,
    CorrespondenceReview,
    CorrespondenceReviewCommand,
)

logger = logging.getLogger(__name__)


class CorrespondenceRecipientViewSet(ClinicalNoStoreResponseMixin, EMRBaseViewSet):
    database_model = CorrespondenceRecipient

    @extend_schema(
        request=CreateManualCorrespondenceRecipientSpec,
        responses={
            200: CreateManualCorrespondenceRecipientResponseSpec,
            201: CreateManualCorrespondenceRecipientResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-manual")
    def idempotent_manual(self, request, *args, **kwargs):
        request_spec = CreateManualCorrespondenceRecipientSpec.model_validate(
            request.data
        )
        patient = get_object_or_404(Patient, external_id=request_spec.patient)
        facility = get_object_or_404(
            Facility,
            external_id=request_spec.facility,
            is_active=True,
        )
        _authorize_clinical_read(request.user, patient)
        _require_facility_membership(request.user, facility)
        payload_hash = canonical_manual_recipient_command_hash(
            request_spec,
            actor_id=request.user.external_id,
        )
        if response := self._manual_command_replay(request_spec, payload_hash):
            return response
        require_workflow_mutations_enabled(facility.external_id)

        try:
            with transaction.atomic():
                if response := self._manual_command_replay(request_spec, payload_hash):
                    return response
                locked_patient = get_object_or_404(
                    Patient._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
                    pk=patient.pk,
                    deleted=False,
                )
                locked_facility = get_object_or_404(
                    Facility._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
                    pk=facility.pk,
                    deleted=False,
                    is_active=True,
                )
                _require_facility_membership(request.user, locked_facility)
                normalized_key = sha256(
                    request_spec.display_name.casefold().encode("utf-8")
                ).hexdigest()[:40]
                source_reference = f"manual-recipient-v1:{normalized_key}"
                recipient = (
                    CorrespondenceRecipient._base_manager.select_for_update(  # noqa: SLF001
                        of=("self",)
                    )
                    .select_related("patient", "facility", "verified_by")
                    .filter(
                        patient=locked_patient,
                        facility=locked_facility,
                        source_type="manual_clinical_entry",
                        source_reference=source_reference,
                        deleted=False,
                    )
                    .first()
                )
                if recipient is None:
                    verified_at = timezone.now()
                    recipient = CorrespondenceRecipient(
                        patient=locked_patient,
                        facility=locked_facility,
                        recipient_kind="healthcare_professional",
                        display_name=request_spec.display_name,
                        professional_role="Zorgverlener",
                        organization_name="Handmatig geadresseerd",
                        postal_address={
                            "address_status": "not_supplied",
                            "recipient_line": request_spec.display_name,
                        },
                        channel_type="postal",
                        channel_identifier=f"manual-postal:{normalized_key}",
                        source_type="manual_clinical_entry",
                        source_reference=source_reference,
                        source_provenance={
                            "contract": "manual-correspondence-recipient-v1",
                            "entered_by": str(request.user.external_id),
                            "entry_mode": "authenticated_clinical_user",
                        },
                        active=True,
                        verified=True,
                        verified_by=request.user,
                        verified_at=verified_at,
                        created_by=request.user,
                        updated_by=request.user,
                    )
                    recipient.save(force_insert=True)
                elif recipient.display_name != request_spec.display_name:
                    return self._manual_source_conflict()
                CorrespondenceRecipientCommand.objects.create(
                    client_request_id=request_spec.client_request_id,
                    payload_hash=payload_hash,
                    actor=request.user,
                    patient=locked_patient,
                    facility=locked_facility,
                    result_recipient=recipient,
                    created_by=request.user,
                    updated_by=request.user,
                )
        except IntegrityError as exc:
            if (
                _constraint_name(exc)
                == CorrespondenceRecipientCommand.IDEMPOTENCY_CONSTRAINT_NAME
            ):
                if response := self._manual_command_replay(request_spec, payload_hash):
                    return response
                return self._manual_idempotency_conflict()
            raise

        return self._manual_response(
            request_spec.client_request_id,
            recipient,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        parameters=[RecipientDiscoverySpec],
        responses={200: RecipientDiscoveryResponseSpec},
    )
    @action(detail=False, methods=["GET"], url_path="verified")
    def verified(self, request, *args, **kwargs):
        query = RecipientDiscoverySpec.model_validate(
            dict(request.query_params.items())
        )
        patient = get_object_or_404(Patient, external_id=query.patient)
        facility = get_object_or_404(
            Facility,
            external_id=query.facility,
            is_active=True,
        )
        _authorize_clinical_read(request.user, patient)
        _require_facility_membership(request.user, facility)
        recipients = list(
            CorrespondenceRecipient.objects.select_related(
                "patient",
                "facility",
                "organization",
                "healthcare_service",
                "verified_by",
            )
            .filter(
                patient=patient,
                facility=facility,
                active=True,
                verified=True,
                recipient_kind="healthcare_professional",
            )
            .order_by("display_name", "external_id")[
                : MAX_VERIFIED_RECIPIENT_RESULTS + 1
            ]
        )
        if len(recipients) > MAX_VERIFIED_RECIPIENT_RESULTS:
            return Response(
                {
                    "errors": [
                        {
                            "type": "recipient_discovery_overflow",
                            "msg": (
                                "Verified recipient directory has too many matches; "
                                "directory governance must resolve the ambiguity"
                            ),
                        }
                    ]
                },
                status=status.HTTP_409_CONFLICT,
            )
        results = []
        for recipient in recipients:
            try:
                validate_verified_recipient(recipient)
            except InvalidVerifiedRecipientError:
                continue
            results.append(
                CorrespondenceRecipientReadSpec.serialize(recipient).to_json()
            )
        return Response({"results": results})

    def _manual_command_replay(self, request_spec, payload_hash):
        command = (
            CorrespondenceRecipientCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "patient",
                "facility",
                "result_recipient__patient",
                "result_recipient__facility",
                "result_recipient__organization",
                "result_recipient__healthcare_service",
                "result_recipient__verified_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if command is None:
            return None
        if not all(
            [
                not command.deleted,
                command.payload_hash == payload_hash,
                command.actor_id == self.request.user.id,
                command.patient.external_id == request_spec.patient,
                command.facility.external_id == request_spec.facility,
            ]
        ):
            return self._manual_idempotency_conflict()
        try:
            validate_verified_recipient(command.result_recipient)
        except InvalidVerifiedRecipientError:
            return self._manual_source_conflict()
        return self._manual_response(
            request_spec.client_request_id,
            command.result_recipient,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    @staticmethod
    def _manual_response(client_request_id, recipient, *, replayed, response_status):
        return Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "recipient": CorrespondenceRecipientReadSpec.serialize(
                    recipient
                ).to_json(),
            },
            status=response_status,
        )

    @staticmethod
    def _manual_idempotency_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "idempotency_conflict",
                        "msg": "client_request_id was already used for another manual recipient",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _manual_source_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "correspondence_recipient_source_stale",
                        "msg": "The stored manual recipient is no longer available",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )


class CorrespondenceReviewViewSet(
    ClinicalNoStoreResponseMixin, EMRRetrieveMixin, EMRBaseViewSet
):
    database_model = CorrespondenceReview
    pydantic_read_model = CorrespondenceReviewReadSpec
    pydantic_retrieve_model = CorrespondenceReviewReadSpec

    def get_queryset(self):
        return super().get_queryset().select_related(*self._related_fields())

    def authorize_retrieve(self, model_instance):
        _authorize_compilation_read(self.request.user, model_instance.compilation)

    def retrieve(self, request, *args, **kwargs):
        review = self.get_object()
        self.authorize_retrieve(review)
        if not self._binding_available(review):
            return self._source_conflict("correspondence_review_unavailable")
        response = Response(CorrespondenceReviewReadSpec.serialize(review).to_json())
        response["ETag"] = f'"{review.external_id}:{review.review_hash}"'
        return response

    @extend_schema(
        request=BindCorrespondenceReviewSpec,
        responses={
            200: BindCorrespondenceReviewResponseSpec,
            201: BindCorrespondenceReviewResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-bind")
    def idempotent_bind(self, request, *args, **kwargs):  # noqa: PLR0911, PLR0912
        request_spec = BindCorrespondenceReviewSpec.model_validate(request.data)
        compilation = self._get_compilation(request_spec.compilation)
        _authorize_compilation_read(request.user, compilation)
        payload_hash = canonical_review_command_hash(
            request_spec,
            actor_id=request.user.external_id,
        )
        if response := self._command_replay(request_spec, compilation, payload_hash):
            return response
        require_workflow_mutations_enabled(compilation.facility.external_id)
        if compilation.compiled_hash != request_spec.compilation_hash:
            return self._source_conflict("correspondence_review_source_stale")
        self._validate_route_context(request_spec, compilation)

        try:
            with transaction.atomic():
                if response := self._command_replay(
                    request_spec, compilation, payload_hash
                ):
                    return response
                self._lock_current_compilation_source(compilation)
                compilation = self._lock_compilation(compilation.pk)
                _authorize_compilation_read(request.user, compilation)
                self._validate_route_context(request_spec, compilation)
                locked = self._lock_and_validate_sources(request_spec, compilation)
                existing = (
                    CorrespondenceReview._base_manager.select_related(  # noqa: SLF001
                        *self._related_fields()
                    )
                    .filter(compilation=compilation)
                    .first()
                )
                if existing:
                    if (
                        existing.source_fingerprint != payload_hash
                        or not self._binding_available(existing)
                    ):
                        return self._source_conflict("correspondence_already_reviewed")
                    self._create_command(
                        request_spec, compilation, existing, payload_hash
                    )
                    return self._response(
                        request_spec.client_request_id,
                        existing,
                        replayed=True,
                        response_status=status.HTTP_200_OK,
                    )
                review = self._build_review(
                    compilation,
                    locked,
                    source_fingerprint=payload_hash,
                )
                review.save(force_insert=True)
                self._create_command(request_spec, compilation, review, payload_hash)
        except IntegrityError as exc:
            constraint = _constraint_name(exc)
            if constraint == CorrespondenceReviewCommand.IDEMPOTENCY_CONSTRAINT_NAME:
                if response := self._command_replay(
                    request_spec, compilation, payload_hash
                ):
                    return response
                return self._idempotency_conflict()
            if constraint == CorrespondenceReview.COMPILATION_CONSTRAINT_NAME:
                return self._attach_existing(request_spec, compilation, payload_hash)
            raise
        except _StaleReviewSourceError:
            return self._source_conflict("correspondence_review_source_stale")
        except _InvalidReviewSourceError as exc:
            return self._source_invalid(str(exc))
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logger.warning(
                "Correspondence review failed compilation=%s error=%s",
                compilation.external_id,
                type(exc).__name__,
            )
            return self._failed()

        return self._response(
            request_spec.client_request_id,
            review,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    def _lock_and_validate_sources(self, request_spec, compilation):
        patient = get_object_or_404(
            Patient._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=compilation.patient_id,
            deleted=False,
        )
        encounter = get_object_or_404(
            Encounter._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=compilation.encounter_id,
            deleted=False,
        )
        facility = get_object_or_404(
            Facility._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=compilation.facility_id,
            deleted=False,
            is_active=True,
        )
        department = get_object_or_404(
            FacilityOrganization._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=compilation.department_id,
            deleted=False,
            active=True,
            facility=facility,
        )
        author = get_object_or_404(
            User._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=self.request.user.pk,
            deleted=False,
            is_active=True,
        )
        compilation.patient = patient
        compilation.encounter = encounter
        compilation.facility = facility
        compilation.department = department
        _authorize_compilation_read(self.request.user, compilation)
        write_report_authorizer(
            self.request.user,
            compilation.form_artifact.report_type,
            compilation.form_artifact.associating_id,
        )
        if (
            not compilation_sources_available(compilation)
            or compilation.compiled_hash != request_spec.compilation_hash
        ):
            raise _StaleReviewSourceError

        membership, role = _lock_author_membership(author, department)
        try:
            author_snapshot = verified_author_snapshot(
                user=author,
                membership=membership,
                role=role,
                facility=facility,
                department=department,
            )
        except InvalidVerifiedAuthorError as exc:
            raise _InvalidReviewSourceError(str(exc)) from exc

        recipient = get_object_or_404(
            CorrespondenceRecipient._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).select_related(
                "patient",
                "facility",
                "organization",
                "healthcare_service",
                "verified_by",
            ),
            external_id=request_spec.recipient,
        )
        if recipient.patient_id != patient.id or recipient.facility_id != facility.id:
            raise Http404("Correspondence review context not found")
        if recipient.deleted:
            raise _StaleReviewSourceError
        try:
            validate_verified_recipient(recipient, lock_verifier=True)
        except InvalidVerifiedRecipientError as exc:
            raise _InvalidReviewSourceError(str(exc)) from exc
        if (
            recipient.resource_version != request_spec.recipient_version
            or recipient.content_hash != request_spec.recipient_hash
            or recipient_content_hash(recipient) != recipient.content_hash
        ):
            raise _StaleReviewSourceError
        if (
            recipient.healthcare_service_id
            and recipient.healthcare_service.facility_id
            and recipient.healthcare_service.facility_id != facility.id
        ):
            raise _InvalidReviewSourceError(
                "Recipient healthcare-service provenance is cross-facility"
            )
        return {
            "author": author,
            "author_snapshot": author_snapshot,
            "recipient": recipient,
            "recipient_snapshot": recipient_snapshot(recipient),
        }

    def _build_review(self, compilation, locked, *, source_fingerprint):
        reviewed_at = timezone.now()
        review = CorrespondenceReview(
            compilation=compilation,
            compilation_hash=compilation.compiled_hash,
            source_fingerprint=source_fingerprint,
            patient=compilation.patient,
            encounter=compilation.encounter,
            facility=compilation.facility,
            department=compilation.department,
            author=locked["author"],
            reviewer=locked["author"],
            recipient=locked["recipient"],
            recipient_version=locked["recipient"].resource_version,
            recipient_hash=locked["recipient"].content_hash,
            author_snapshot=locked["author_snapshot"],
            recipient_snapshot=locked["recipient_snapshot"],
            reviewed_at=reviewed_at,
            created_by=locked["author"],
            updated_by=locked["author"],
        )
        review.review_hash = correspondence_review_hash(review)
        return review

    def _command_replay(self, request_spec, compilation, payload_hash):
        command = (
            CorrespondenceReviewCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "compilation",
                "patient",
                "encounter",
                "result_review__compilation__patient",
                "result_review__compilation__encounter",
                "result_review__compilation__facility",
                "result_review__compilation__department",
                "result_review__compilation__encounter_reason",
                "result_review__compilation__form_submission__questionnaire",
                "result_review__compilation__form_artifact",
                "result_review__compilation__template",
                "result_review__recipient__patient",
                "result_review__recipient__facility",
                "result_review__recipient__organization",
                "result_review__recipient__healthcare_service",
                "result_review__recipient__verified_by",
                "result_review__author",
                "result_review__reviewer",
                "result_review__patient",
                "result_review__encounter",
                "result_review__facility",
                "result_review__department",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        review = command.result_review
        matches = all(
            [
                not command.deleted,
                command.payload_hash == payload_hash,
                command.actor_id == self.request.user.id,
                command.compilation_id == compilation.id,
                command.patient_id == compilation.patient_id,
                command.encounter_id == compilation.encounter_id,
                self._binding_available(review),
            ]
        )
        if not matches:
            return self._idempotency_conflict()
        self.authorize_retrieve(review)
        return self._response(
            request_spec.client_request_id,
            review,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    @staticmethod
    def _binding_available(review):
        return reviewed_binding_frozen_integrity_valid(review)

    def _create_command(self, request_spec, compilation, review, payload_hash):
        CorrespondenceReviewCommand.objects.create(
            client_request_id=request_spec.client_request_id,
            payload_hash=payload_hash,
            actor=self.request.user,
            compilation=compilation,
            patient=compilation.patient,
            encounter=compilation.encounter,
            result_review=review,
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def _attach_existing(self, request_spec, compilation, payload_hash):
        try:
            with transaction.atomic():
                if response := self._command_replay(
                    request_spec, compilation, payload_hash
                ):
                    return response
                self._lock_current_compilation_source(compilation)
                compilation = self._lock_compilation(compilation.pk)
                _authorize_compilation_read(self.request.user, compilation)
                locked = self._lock_and_validate_sources(request_spec, compilation)
                review = get_object_or_404(
                    CorrespondenceReview._base_manager.select_related(  # noqa: SLF001
                        *self._related_fields()
                    ),
                    compilation=compilation,
                )
                if (
                    review.source_fingerprint != payload_hash
                    or not self._binding_available(review)
                ):
                    return self._source_conflict("correspondence_already_reviewed")
                self._create_command(request_spec, compilation, review, payload_hash)
                del locked
        except IntegrityError:
            if response := self._command_replay(
                request_spec, compilation, payload_hash
            ):
                return response
            return self._idempotency_conflict()
        except _StaleReviewSourceError:
            return self._source_conflict("correspondence_review_source_stale")
        return self._response(
            request_spec.client_request_id,
            review,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    @staticmethod
    def _validate_route_context(request_spec, compilation):
        if not all(
            [
                compilation.external_id == request_spec.compilation,
                compilation.patient.external_id == request_spec.patient,
                compilation.encounter.external_id == request_spec.encounter,
                compilation.facility.external_id == request_spec.facility,
                compilation.department.external_id == request_spec.department,
                compilation.author.external_id == request_spec.author,
            ]
        ):
            raise Http404("Correspondence review context not found")

    @staticmethod
    def _lock_current_compilation_source(compilation):
        try:
            _head, current = lock_current_finalized_form_series(
                compilation.form_submission
            )
        except FormSubmissionSeriesHeadIntegrityError as exc:
            raise _StaleReviewSourceError from exc
        if current.pk != compilation.form_submission_id:
            raise _StaleReviewSourceError
        return current

    @staticmethod
    def _get_compilation(external_id):
        return get_object_or_404(
            CorrespondenceCompilation._base_manager.select_related(  # noqa: SLF001
                *CorrespondenceReviewViewSet._compilation_related_fields()
            ),
            external_id=external_id,
        )

    @staticmethod
    def _lock_compilation(pk):
        return (
            CorrespondenceCompilation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related(*CorrespondenceReviewViewSet._compilation_related_fields())
            .get(pk=pk)
        )

    @staticmethod
    def _compilation_related_fields():
        return [
            "patient",
            "encounter",
            "facility",
            "department",
            "encounter_reason",
            "form_submission__questionnaire",
            "form_artifact",
            "template",
            "author",
        ]

    @staticmethod
    def _related_fields():
        return [
            "compilation__patient",
            "compilation__encounter",
            "compilation__facility",
            "compilation__department",
            "compilation__encounter_reason",
            "compilation__form_submission__questionnaire",
            "compilation__form_artifact",
            "compilation__template",
            "compilation__author",
            "patient",
            "encounter",
            "facility",
            "department",
            "author",
            "reviewer",
            "recipient__patient",
            "recipient__facility",
            "recipient__organization",
            "recipient__healthcare_service",
            "recipient__verified_by",
        ]

    @staticmethod
    def _response(client_request_id, review, *, replayed, response_status):
        response = Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "review_binding": CorrespondenceReviewReadSpec.serialize(
                    review
                ).to_json(),
            },
            status=response_status,
        )
        response["ETag"] = f'"{review.external_id}:{review.review_hash}"'
        return response

    @staticmethod
    def _idempotency_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "idempotency_conflict",
                        "msg": (
                            "client_request_id was already used with different "
                            "review sources or context"
                        ),
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _source_conflict(error_type):
        return Response(
            {
                "errors": [
                    {
                        "type": error_type,
                        "msg": "Correspondence review sources are stale or unavailable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _source_invalid(message):
        return Response(
            {
                "errors": [
                    {"type": "correspondence_review_source_invalid", "msg": message}
                ]
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @staticmethod
    def _failed():
        return Response(
            {
                "errors": [
                    {
                        "type": "correspondence_review_failed",
                        "msg": (
                            "No review binding was committed; retry with the same "
                            "client_request_id"
                        ),
                    }
                ]
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def _authorize_clinical_read(user, patient):
    if not (
        AuthorizationController.call("can_view_clinical_data", user, patient)
        or AuthorizationController.call(
            "can_view_patient_questionnaire_responses", user, patient
        )
    ):
        raise PermissionDenied("Permission denied for correspondence review")


def _authorize_compilation_read(user, compilation):
    _authorize_clinical_read(user, compilation.patient)
    read_report_authorizer(
        user,
        compilation.form_artifact.report_type,
        compilation.form_artifact.associating_id,
    )


def _require_facility_membership(user, facility):
    exists = FacilityOrganizationUser.objects.filter(
        user=user,
        organization__facility=facility,
        organization__active=True,
        role__deleted=False,
        role__is_archived=False,
    ).exists()
    if not exists:
        raise PermissionDenied("Verified facility membership is required")


def _lock_author_membership(author, department):
    memberships = list(
        FacilityOrganizationUser._base_manager.select_for_update(of=("self",)).filter(  # noqa: SLF001
            deleted=False,
            organization=department,
            user=author,
        )[:2]
    )
    if len(memberships) != 1:
        raise _InvalidReviewSourceError(
            "Authenticated author department membership is missing or ambiguous"
        )
    role = (
        RoleModel._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(
            pk=memberships[0].role_id,
            deleted=False,
            is_archived=False,
        )
        .first()
    )
    if not role:
        raise _InvalidReviewSourceError(
            "Authenticated author professional role is unavailable"
        )
    return memberships[0], role


class _InvalidReviewSourceError(Exception):
    pass


class _StaleReviewSourceError(Exception):
    pass


def _constraint_name(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)

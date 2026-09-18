import hashlib
import logging

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    lock_current_finalized_form_series,
)
from care.emr.correspondence.letter import (
    correspondence_artifact_frozen_integrity_valid,
    correspondence_revision_artifact_status,
    correspondence_revision_frozen_integrity_valid,
)
from care.emr.correspondence.review import (
    reviewed_binding_available,
    reviewed_binding_frozen_integrity_valid,
)
from care.emr.models.correspondence import CorrespondenceCompilation
from care.emr.models.correspondence_letter import (
    CorrespondenceLetter,
    CorrespondenceLetterCommand,
    CorrespondenceLetterRevision,
)
from care.emr.models.correspondence_review import (
    CorrespondenceRecipient,
    CorrespondenceReview,
)
from care.emr.models.report.report_upload import ReportUpload
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care.emr.reports.correspondence_letter import (
    CorrespondenceLetterRenderError,
    build_correspondence_letter_html,
    render_correspondence_letter_pdf,
)
from care.emr.resources.correspondence_letter import (
    DEFAULT_CORRESPONDENCE_LETTER_PAGE_SIZE,
    MAX_CORRESPONDENCE_LETTER_PAGE_SIZE,
    CorrespondenceLetterCommandResponseSpec,
    CorrespondenceLetterListSpec,
    CreateCorrespondenceLetterSpec,
    FinalizeCorrespondenceLetterSpec,
    ReviseCorrespondenceLetterSpec,
    canonical_letter_command_hash,
    correspondence_letter_body_hash,
    correspondence_letter_revision_hash,
)
from care.emr.resources.form_submission.artifact import has_unresolved_placeholder
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.security.authorization.base import AuthorizationController
from care.utils.pagination.care_pagination import CareLimitOffsetPagination
from care.utils.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)
SHA256_HEX_LENGTH = 64


class CorrespondenceLetterPagination(CareLimitOffsetPagination):
    default_limit = DEFAULT_CORRESPONDENCE_LETTER_PAGE_SIZE
    max_limit = MAX_CORRESPONDENCE_LETTER_PAGE_SIZE


class CorrespondenceLetterViewSet(ClinicalNoStoreResponseMixin, EMRBaseViewSet):
    database_model = CorrespondenceLetterRevision

    def get_queryset(self):
        return super().get_queryset().select_related(*self._revision_related_fields())

    def list(self, request, *args, **kwargs):
        query = CorrespondenceLetterListSpec.model_validate(
            dict(request.query_params.items())
        )
        review = self._get_review(query.review_binding)
        self._authorize_read(review)
        if not all(
            [
                review.patient.external_id == query.patient,
                review.encounter.external_id == query.encounter,
            ]
        ):
            raise Http404("Correspondence letter context not found")
        if not reviewed_binding_frozen_integrity_valid(review):
            return self._source_conflict("correspondence_review_stale")
        revisions = self.get_queryset().filter(
            letter__review=review,
            letter__patient__external_id=query.patient,
            letter__encounter__external_id=query.encounter,
        )
        paginator = CorrespondenceLetterPagination()
        page = paginator.paginate_queryset(
            revisions.order_by("-resource_version", "-created_date"), request
        )
        for item in page:
            self._authorize_read(item.letter.review)
            if not self._revision_available(item):
                return self._source_conflict("correspondence_letter_unavailable")
        data = [self._serialize_revision(item) for item in page]
        return paginator.get_paginated_response(data)

    def retrieve(self, request, *args, **kwargs):
        revision = self.get_object()
        self._authorize_read(revision.letter.review)
        if not self._revision_available(revision):
            return self._source_conflict("correspondence_letter_unavailable")
        response = Response(self._serialize_revision(revision, include_download=True))
        response["ETag"] = f'"{revision.external_id}:{revision.revision_hash}"'
        return response

    @extend_schema(
        request=CreateCorrespondenceLetterSpec,
        responses={
            200: CorrespondenceLetterCommandResponseSpec,
            201: CorrespondenceLetterCommandResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-create")
    def idempotent_create(self, request, *args, **kwargs):  # noqa: PLR0911
        request_spec = CreateCorrespondenceLetterSpec.model_validate(request.data)
        review = self._get_review(request_spec.review_binding)
        self._authorize_read(review)
        if hasattr(review, "replacement_attempt"):
            return self._source_conflict(
                "correspondence_replacement_requires_case_command"
            )
        payload_hash = canonical_letter_command_hash(
            request_spec,
            command_type="create",
            actor_id=request.user.external_id,
            target_revision_id=None,
        )
        if response := self._command_replay(
            request_spec,
            review,
            payload_hash,
            command_type="create",
            target=None,
        ):
            return response
        self._validate_context(request_spec, review)
        try:
            with transaction.atomic():
                if response := self._command_replay(
                    request_spec,
                    review,
                    payload_hash,
                    command_type="create",
                    target=None,
                ):
                    return response
                self._lock_current_review_source(review)
                review = self._lock_review(review)
                self._authorize_read(review)
                self._validate_context(request_spec, review)
                self._authorize_new_mutation(review)
                self._validate_review_source(request_spec, review)
                letter = (
                    CorrespondenceLetter._base_manager.select_for_update(  # noqa: SLF001
                        of=("self",)
                    )
                    .filter(review=review)
                    .first()
                )
                body_hash = correspondence_letter_body_hash(request_spec.body)
                if letter:
                    if letter.deleted:
                        return self._source_conflict(
                            "correspondence_letter_unavailable"
                        )
                    revision = self._latest_revision(letter)
                    if not all(
                        [
                            revision,
                            revision.status == "draft",
                            revision.resource_version == 1,
                            revision.body_hash == body_hash,
                            revision.source_review_hash == review.review_hash,
                            self._revision_hash_valid(revision),
                        ]
                    ):
                        return self._source_conflict(
                            "correspondence_letter_already_exists"
                        )
                    self._create_command(
                        request_spec,
                        payload_hash,
                        "create",
                        letter,
                        review,
                        None,
                        revision,
                        None,
                    )
                    return self._response(
                        request_spec.client_request_id,
                        revision,
                        replayed=True,
                        response_status=status.HTTP_200_OK,
                    )
                letter = self._create_letter(review)
                revision = self._create_revision(
                    letter,
                    body=request_spec.body,
                    version=1,
                    previous=None,
                    status_value="draft",
                )
                self._create_command(
                    request_spec,
                    payload_hash,
                    "create",
                    letter,
                    review,
                    None,
                    revision,
                    None,
                )
        except IntegrityError as exc:
            return self._integrity_response(
                exc,
                request_spec,
                review,
                payload_hash,
                command_type="create",
                target=None,
            )
        except _StaleLetterSourceError:
            return self._source_conflict("correspondence_review_stale")
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logger.warning(
                "Correspondence letter create failed review=%s error=%s",
                review.external_id,
                type(exc).__name__,
            )
            return self._failed()
        return self._response(
            request_spec.client_request_id,
            revision,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=ReviseCorrespondenceLetterSpec,
        responses={
            200: CorrespondenceLetterCommandResponseSpec,
            201: CorrespondenceLetterCommandResponseSpec,
        },
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-revise")
    def idempotent_revise(self, request, *args, **kwargs):
        request_spec = ReviseCorrespondenceLetterSpec.model_validate(request.data)
        return self._execute_revision_command(request_spec, command_type="revise")

    @extend_schema(
        request=FinalizeCorrespondenceLetterSpec,
        responses={
            200: CorrespondenceLetterCommandResponseSpec,
            201: CorrespondenceLetterCommandResponseSpec,
        },
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-finalize")
    def idempotent_finalize(self, request, *args, **kwargs):
        request_spec = FinalizeCorrespondenceLetterSpec.model_validate(request.data)
        return self._execute_revision_command(request_spec, command_type="finalize")

    def _execute_revision_command(  # noqa: PLR0911, PLR0912
        self, request_spec, *, command_type
    ):
        target = self._get_revision(self.kwargs["external_id"])
        review = target.letter.review
        self._authorize_read(review)
        if hasattr(review, "replacement_attempt"):
            return self._source_conflict(
                "correspondence_replacement_requires_case_command"
            )
        payload_hash = canonical_letter_command_hash(
            request_spec,
            command_type=command_type,
            actor_id=self.request.user.external_id,
            target_revision_id=target.external_id,
        )
        if response := self._command_replay(
            request_spec,
            review,
            payload_hash,
            command_type=command_type,
            target=target,
        ):
            return response
        if command_type == "finalize":
            require_workflow_mutations_enabled(review.compilation.facility.external_id)
        self._validate_context(request_spec, review)
        uploaded_artifact = None
        try:
            with transaction.atomic():
                if response := self._command_replay(
                    request_spec,
                    review,
                    payload_hash,
                    command_type=command_type,
                    target=target,
                ):
                    return response
                self._lock_current_review_source(review)
                review = self._lock_review(review)
                self._authorize_read(review)
                letter = CorrespondenceLetter._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                ).get(pk=target.letter_id)
                target = self._lock_revision(target.pk)
                self._validate_context(request_spec, review)
                self._authorize_new_mutation(review)
                self._validate_review_source(request_spec, review)
                latest = self._latest_revision(letter)
                if (
                    not latest
                    or latest.pk != target.pk
                    or target.resource_version != request_spec.expected_version
                ):
                    return self._version_conflict(
                        latest.resource_version if latest else target.resource_version
                    )
                if (
                    letter.deleted
                    or target.deleted
                    or target.status != "draft"
                    or not self._revision_hash_valid(target)
                ):
                    return self._source_conflict("correspondence_letter_immutable")
                if command_type == "revise":
                    revision = self._create_revision(
                        letter,
                        body=request_spec.body,
                        version=target.resource_version + 1,
                        previous=target,
                        status_value="draft",
                    )
                else:
                    if has_unresolved_placeholder(target.body):
                        return self._source_invalid(
                            "Correspondence body contains unresolved placeholders"
                        )
                    revision = self._create_revision(
                        letter,
                        body=target.body,
                        version=target.resource_version + 1,
                        previous=target,
                        status_value="finalized",
                        commit=False,
                    )
                    uploaded_artifact = self._build_and_upload_artifact(revision)
                    uploaded_artifact.save(force_insert=True, skip_internal_name=True)
                    self._insert_revision_with_artifact(revision, uploaded_artifact)
                self._create_command(
                    request_spec,
                    payload_hash,
                    command_type,
                    letter,
                    review,
                    target,
                    revision,
                    uploaded_artifact,
                )
        except IntegrityError as exc:
            self._delete_uploaded_artifact(uploaded_artifact)
            return self._integrity_response(
                exc,
                request_spec,
                review,
                payload_hash,
                command_type=command_type,
                target=target,
            )
        except _StaleLetterSourceError:
            self._delete_uploaded_artifact(uploaded_artifact)
            return self._source_conflict("correspondence_review_stale")
        except (CorrespondenceLetterRenderError, ValueError) as exc:
            self._delete_uploaded_artifact(uploaded_artifact)
            return self._source_invalid(str(exc))
        except (Http404, PermissionDenied):
            self._delete_uploaded_artifact(uploaded_artifact)
            raise
        except Exception as exc:
            self._delete_uploaded_artifact(uploaded_artifact)
            logger.warning(
                "Correspondence letter command failed revision=%s error=%s",
                target.external_id,
                type(exc).__name__,
            )
            return self._failed()
        return self._response(
            request_spec.client_request_id,
            revision,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
            include_download=command_type == "finalize",
        )

    def _lock_review(self, review):
        compilation = (
            CorrespondenceCompilation._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related(
                "patient",
                "encounter",
                "facility",
                "department",
                "encounter_reason",
                "form_submission__questionnaire",
                "form_artifact",
                "template",
                "author",
            )
            .get(pk=review.compilation_id)
        )
        locked = (
            CorrespondenceReview._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related(*self._review_related_fields())
            .get(pk=review.pk)
        )
        recipient = (
            CorrespondenceRecipient._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related(
                "patient",
                "facility",
                "organization",
                "healthcare_service",
                "verified_by",
            )
            .get(pk=locked.recipient_id)
        )
        locked.compilation = compilation
        locked.recipient = recipient
        return locked

    @staticmethod
    def _lock_current_review_source(review):
        try:
            _head, current = lock_current_finalized_form_series(
                review.compilation.form_submission
            )
        except FormSubmissionSeriesHeadIntegrityError as exc:
            raise _StaleLetterSourceError from exc
        if current.pk != review.compilation.form_submission_id:
            raise _StaleLetterSourceError
        return current

    def _validate_review_source(self, request_spec, review):
        if review.review_hash != request_spec.review_hash:
            raise _StaleLetterSourceError
        if not reviewed_binding_available(review, lock_verifier=True):
            raise _StaleLetterSourceError

    def _authorize_new_mutation(self, review):
        if (
            self.request.user.id != review.author_id
            or self.request.user.deleted
            or not self.request.user.is_active
            or not self.request.user.verified
            or self.request.user.is_service_account
        ):
            raise PermissionDenied("Verified correspondence author is required")
        write_report_authorizer(
            self.request.user,
            review.compilation.form_artifact.report_type,
            review.compilation.form_artifact.associating_id,
        )

    def _authorize_read(self, review):
        self._authorize_clinical_read(review.patient)
        read_report_authorizer(
            self.request.user,
            review.compilation.form_artifact.report_type,
            review.compilation.form_artifact.associating_id,
        )

    def _authorize_clinical_read(self, patient):
        if not (
            AuthorizationController.call(
                "can_view_clinical_data", self.request.user, patient
            )
            or AuthorizationController.call(
                "can_view_patient_questionnaire_responses",
                self.request.user,
                patient,
            )
        ):
            raise PermissionDenied("Permission denied for correspondence letter")

    def _validate_context(self, request_spec, review):
        if not all(
            [
                review.external_id == request_spec.review_binding,
                review.patient.external_id == request_spec.patient,
                review.encounter.external_id == request_spec.encounter,
                review.facility.external_id == request_spec.facility,
                review.department.external_id == request_spec.department,
                review.author.external_id == request_spec.author,
                self.request.user.external_id == request_spec.author,
            ]
        ):
            raise Http404("Correspondence letter context not found")

    def _create_letter(self, review):
        letter = CorrespondenceLetter(
            review=review,
            review_hash=review.review_hash,
            patient=review.patient,
            encounter=review.encounter,
            facility=review.facility,
            department=review.department,
            author=review.author,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        letter.save(force_insert=True)
        return letter

    def _create_revision(
        self, letter, *, body, version, previous, status_value, commit=True
    ):
        """Build (and by default insert) an immutable revision.

        Revisions are append-only, so a finalized revision must carry its
        `final_artifact` at insert time: callers pass `commit=False`, build and
        save the artifact from the in-memory revision, then insert it with
        `_insert_revision_with_artifact`.
        """
        finalized_at = timezone.now() if status_value == "finalized" else None
        revision = CorrespondenceLetterRevision(
            letter=letter,
            previous_revision=previous,
            resource_version=version,
            status=status_value,
            source_review_hash=letter.review_hash,
            body=body,
            body_hash=correspondence_letter_body_hash(body),
            finalized_at=finalized_at,
            finalized_by=self.request.user if finalized_at else None,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        revision.revision_hash = correspondence_letter_revision_hash(revision)
        if commit:
            revision.save(force_insert=True)
        return revision

    @staticmethod
    def _insert_revision_with_artifact(revision, artifact):
        revision.final_artifact = artifact
        revision.save(force_insert=True)
        return revision

    def _build_and_upload_artifact(self, revision):
        generated_at = timezone.now()
        artifact = ReportUpload(
            template=None,
            name="",
            internal_name="",
            associating_id=str(revision.letter.encounter.external_id),
            upload_completed=True,
            report_type="encounter_report",
            patient=revision.letter.patient,
            encounter=revision.letter.encounter,
            source_version=revision.resource_version,
            source_snapshot_hash=revision.revision_hash,
            generated_at=generated_at,
            generated_by=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
            meta={
                "artifact_kind": "final_correspondence_letter",
                "mime_type": "application/pdf",
            },
        )
        artifact.internal_name = f"{artifact.external_id}.pdf"
        artifact.name = f"Final correspondence {artifact.external_id}"
        html = build_correspondence_letter_html(
            artifact_id=artifact.external_id,
            revision=revision,
            generated_at=generated_at,
        )
        pdf = render_correspondence_letter_pdf(html)
        if not pdf or not pdf.startswith(b"%PDF"):
            raise CorrespondenceLetterRenderError(
                "PDF renderer returned an invalid final correspondence artifact"
            )
        artifact.artifact_sha256 = hashlib.sha256(pdf).hexdigest()
        artifact.files_manager.put_object(
            artifact,
            pdf,
            ContentType="application/pdf",
        )
        return artifact

    def _create_command(
        self,
        request_spec,
        payload_hash,
        command_type,
        letter,
        review,
        target,
        result,
        artifact,
    ):
        CorrespondenceLetterCommand.objects.create(
            client_request_id=request_spec.client_request_id,
            payload_hash=payload_hash,
            command_type=command_type,
            expected_version=getattr(request_spec, "expected_version", None),
            actor=self.request.user,
            letter=letter,
            review=review,
            patient=review.patient,
            encounter=review.encounter,
            target_revision=target,
            result_revision=result,
            result_artifact=artifact,
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def _command_replay(
        self, request_spec, review, payload_hash, *, command_type, target
    ):
        command = (
            CorrespondenceLetterCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "review",
                "patient",
                "encounter",
                "result_revision__letter__review__compilation__form_artifact",
                "result_revision__letter__review__compilation__form_submission__questionnaire",
                "result_revision__letter__review__compilation__template",
                "result_revision__letter__review__recipient__patient",
                "result_revision__letter__review__recipient__facility",
                "result_revision__letter__review__recipient__organization",
                "result_revision__letter__review__recipient__healthcare_service",
                "result_revision__letter__review__recipient__verified_by",
                "result_revision__letter__patient",
                "result_revision__letter__encounter",
                "result_revision__letter__facility",
                "result_revision__letter__department",
                "result_revision__letter__author",
                "result_revision__previous_revision",
                "result_revision__finalized_by",
                "result_artifact__generated_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        target_id = target.id if target else None
        if not all(
            [
                not command.deleted,
                command.payload_hash == payload_hash,
                command.command_type == command_type,
                command.actor_id == self.request.user.id,
                command.review_id == review.id,
                command.patient_id == review.patient_id,
                command.encounter_id == review.encounter_id,
                command.target_revision_id == target_id,
                self._revision_available(command.result_revision),
                command.result_artifact_id
                == self._artifact_id_for_revision(command.result_revision),
            ]
        ):
            return self._idempotency_conflict()
        self._authorize_read(command.result_revision.letter.review)
        return self._response(
            request_spec.client_request_id,
            command.result_revision,
            replayed=True,
            response_status=status.HTTP_200_OK,
            include_download=command.command_type == "finalize",
        )

    def _revision_available(self, revision):
        return correspondence_revision_frozen_integrity_valid(revision)

    @staticmethod
    def _revision_hash_valid(revision):
        return correspondence_revision_frozen_integrity_valid(revision)

    @staticmethod
    def _artifact_integrity_valid(artifact, revision):
        return correspondence_artifact_frozen_integrity_valid(artifact, revision)

    @classmethod
    def _artifact_available(cls, artifact, revision):
        del cls
        return (
            correspondence_revision_artifact_status(
                revision,
                artifact=artifact,
            )
            == "available"
        )

    @staticmethod
    def _artifact_for_revision(revision):
        return (
            ReportUpload._base_manager.select_related("generated_by")  # noqa: SLF001
            .filter(letter_revision=revision)
            .first()
        )

    def _artifact_id_for_revision(self, revision):
        artifact = self._artifact_for_revision(revision)
        return artifact.id if artifact else None

    @staticmethod
    def _latest_revision(letter):
        return (
            CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related("letter__review", "previous_revision", "finalized_by")
            .filter(letter=letter)
            .order_by("-resource_version")
            .first()
        )

    def _serialize_revision(self, revision, *, include_download=False):
        artifact = self._artifact_for_revision(revision)
        artifact_data = None
        artifact_status = correspondence_revision_artifact_status(
            revision,
            artifact=artifact,
        )
        artifact_integrity_valid = self._artifact_integrity_valid(artifact, revision)
        if artifact_integrity_valid:
            download_url = None
            if include_download and artifact_status == "available":
                try:
                    download_url = artifact.files_manager.read_signed_url(artifact)
                except Exception as exc:
                    logger.warning(
                        "Correspondence artifact URL unavailable revision=%s error=%s",
                        revision.external_id,
                        type(exc).__name__,
                    )
                    artifact_status = "unavailable"
            artifact_data = {
                "id": str(artifact.external_id),
                "sha256": artifact.artifact_sha256,
                "generated_at": artifact.generated_at,
                "generated_by": str(artifact.generated_by.external_id),
                "mime_type": "application/pdf",
                "download_url": download_url,
            }
        letter = revision.letter
        return {
            "id": str(revision.external_id),
            "letter": str(letter.external_id),
            "review_binding": str(letter.review.external_id),
            "review_hash": letter.review_hash,
            "patient": str(letter.patient.external_id),
            "encounter": str(letter.encounter.external_id),
            "facility": str(letter.facility.external_id),
            "department": str(letter.department.external_id),
            "author": str(letter.author.external_id),
            "previous_revision": (
                str(revision.previous_revision.external_id)
                if revision.previous_revision_id
                else None
            ),
            "resource_version": revision.resource_version,
            "status": revision.status,
            "body": revision.body,
            "body_hash": revision.body_hash,
            "revision_hash": revision.revision_hash,
            "finalized_at": revision.finalized_at,
            "finalized_by": (
                str(revision.finalized_by.external_id)
                if revision.finalized_by_id
                else None
            ),
            "artifact_status": artifact_status,
            "artifact": artifact_data,
        }

    def _response(
        self,
        client_request_id,
        revision,
        *,
        replayed,
        response_status,
        include_download=False,
    ):
        try:
            data = self._serialize_revision(
                revision,
                include_download=include_download,
            )
        except Exception as exc:
            logger.warning(
                "Correspondence artifact URL unavailable revision=%s error=%s",
                revision.external_id,
                type(exc).__name__,
            )
            return self._download_unavailable(client_request_id, replayed)
        response = Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "correspondence": data,
            },
            status=response_status,
        )
        response["ETag"] = f'"{revision.external_id}:{revision.revision_hash}"'
        return response

    def _integrity_response(
        self,
        exc,
        request_spec,
        review,
        payload_hash,
        *,
        command_type,
        target,
    ):
        if (
            _constraint_name(exc)
            == CorrespondenceLetterCommand.IDEMPOTENCY_CONSTRAINT_NAME
        ):
            if response := self._command_replay(
                request_spec,
                review,
                payload_hash,
                command_type=command_type,
                target=target,
            ):
                return response
            return self._idempotency_conflict()
        if _constraint_name(exc) in {
            CorrespondenceLetter.REVIEW_CONSTRAINT_NAME,
            CorrespondenceLetterRevision.VERSION_CONSTRAINT_NAME,
            CorrespondenceLetterRevision.FINAL_CONSTRAINT_NAME,
        }:
            return self._source_conflict("correspondence_letter_concurrency_conflict")
        raise exc

    @staticmethod
    def _get_review(external_id):
        return get_object_or_404(
            CorrespondenceReview._base_manager.select_related(  # noqa: SLF001
                *CorrespondenceLetterViewSet._review_related_fields()
            ),
            external_id=external_id,
        )

    @staticmethod
    def _get_revision(external_id):
        return get_object_or_404(
            CorrespondenceLetterRevision._base_manager.select_related(  # noqa: SLF001
                *CorrespondenceLetterViewSet._revision_related_fields()
            ),
            external_id=external_id,
        )

    @staticmethod
    def _lock_revision(pk):
        return (
            CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related(*CorrespondenceLetterViewSet._revision_related_fields())
            .get(pk=pk)
        )

    @staticmethod
    def _review_related_fields():
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
    def _revision_related_fields():
        return [
            "letter__review__compilation__patient",
            "letter__review__compilation__encounter",
            "letter__review__compilation__facility",
            "letter__review__compilation__department",
            "letter__review__compilation__encounter_reason",
            "letter__review__compilation__form_submission__questionnaire",
            "letter__review__compilation__form_artifact",
            "letter__review__compilation__template",
            "letter__review__compilation__author",
            "letter__review__patient",
            "letter__review__encounter",
            "letter__review__facility",
            "letter__review__department",
            "letter__review__author",
            "letter__review__reviewer",
            "letter__review__recipient__patient",
            "letter__review__recipient__facility",
            "letter__review__recipient__organization",
            "letter__review__recipient__healthcare_service",
            "letter__review__recipient__verified_by",
            "letter__patient",
            "letter__encounter",
            "letter__facility",
            "letter__department",
            "letter__author",
            "previous_revision",
            "finalized_by",
        ]

    @staticmethod
    def _idempotency_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "idempotency_conflict",
                        "msg": (
                            "client_request_id was already used with different "
                            "correspondence content or context"
                        ),
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _version_conflict(current_version):
        return Response(
            {
                "errors": [
                    {
                        "type": "version_conflict",
                        "msg": "Correspondence draft has changed",
                    }
                ],
                "current_version": current_version,
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
                        "msg": "Correspondence letter source is stale or unavailable",
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
                    {"type": "correspondence_letter_source_invalid", "msg": message}
                ]
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @staticmethod
    def _download_unavailable(client_request_id, replayed):
        return Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "errors": [
                    {
                        "type": "correspondence_artifact_download_unavailable",
                        "msg": "Final artifact is stored; retry the exact command",
                    }
                ],
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @staticmethod
    def _failed():
        return Response(
            {
                "errors": [
                    {
                        "type": "correspondence_letter_failed",
                        "msg": "No correspondence mutation was committed; retry safely",
                    }
                ]
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @staticmethod
    def _delete_uploaded_artifact(artifact):
        if not artifact:
            return
        try:
            artifact.files_manager.delete_object(artifact, quiet=True)
        except Exception as exc:
            logger.error(
                "Correspondence artifact compensation failed artifact=%s error=%s",
                artifact.external_id,
                type(exc).__name__,
            )


class _StaleLetterSourceError(Exception):
    pass


def _constraint_name(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)

import csv
import io

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet, EMRListMixin
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care_suriname.models.clinical_term_translation import ClinicalTermTranslation
from care_suriname.resources.clinical_term_translation import (
    CSV_FIELDS,
    ClinicalTermCsvImportRequest,
    ClinicalTermKind,
    ClinicalTermResolveRequest,
    ClinicalTermStatus,
    ClinicalTermTranslationReadSpec,
    ClinicalTermTranslationUpdateSpec,
    ClinicalTermTranslationWriteSpec,
    normalize_language_tag,
    parse_csv_import,
    resolve_concepts,
    search_approved_translation_concepts,
    serialize_clinical_term,
    validate_status_transition,
)


class ClinicalTermTranslationViewSet(
    ClinicalNoStoreResponseMixin,
    EMRListMixin,
    EMRBaseViewSet,
):
    database_model = ClinicalTermTranslation

    def permissions_controller(self, request):
        if self.action in {"list", "retrieve", "search", "resolve"}:
            return True
        return request.user.is_superuser

    def get_queryset(self):
        queryset = ClinicalTermTranslation.objects.select_related(
            "created_by", "updated_by", "reviewed_by"
        )
        if not self.request.user.is_superuser:
            queryset = queryset.filter(status="approved", is_active=True)
        language = self.request.query_params.get("language")
        if language:
            try:
                queryset = queryset.filter(language=normalize_language_tag(language))
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        term_status = self.request.query_params.get("status")
        if term_status:
            if not self.request.user.is_superuser:
                raise ValidationError(
                    "Status filtering is a terminology management action"
                )
            if term_status not in {item.value for item in ClinicalTermStatus}:
                raise ValidationError("Unsupported terminology status")
            queryset = queryset.filter(status=term_status)
        concept_kind = self.request.query_params.get("concept_kind")
        if concept_kind:
            if concept_kind not in {item.value for item in ClinicalTermKind}:
                raise ValidationError("Unsupported terminology concept kind")
            queryset = queryset.filter(concept_kind=concept_kind)
        return queryset.order_by("language", "preferred_display", "system", "code")

    def serialize_list(self, obj):
        return serialize_clinical_term(obj)

    def retrieve(self, request, *args, **kwargs):
        return Response(serialize_clinical_term(self.get_object()))

    @extend_schema(
        request=ClinicalTermTranslationWriteSpec,
        responses={201: ClinicalTermTranslationReadSpec},
    )
    def create(self, request, *args, **kwargs):
        spec = ClinicalTermTranslationWriteSpec.model_validate(request.data)
        key_exists = ClinicalTermTranslation.objects.filter(
            system=spec.system,
            code=spec.code,
            language=spec.language,
        ).exists()
        if key_exists:
            return self._duplicate_response()
        try:
            term = ClinicalTermTranslation.objects.create(
                **self._model_values(spec),
                created_by=request.user,
                updated_by=request.user,
            )
        except IntegrityError:
            return self._duplicate_response()
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict) from exc
        return Response(serialize_clinical_term(term), status=status.HTTP_201_CREATED)

    @extend_schema(
        request=ClinicalTermTranslationUpdateSpec,
        responses={200: ClinicalTermTranslationReadSpec},
    )
    def update(self, request, *args, **kwargs):
        spec = ClinicalTermTranslationUpdateSpec.model_validate(request.data)
        with transaction.atomic():
            term = get_object_or_404(
                ClinicalTermTranslation.objects.select_for_update(of=("self",)),
                external_id=self.kwargs["external_id"],
            )
            if (term.system, term.code, term.language) != (
                spec.system,
                spec.code,
                spec.language,
            ):
                raise ValidationError("System, code and language are immutable")
            try:
                validate_status_transition(term.status, spec.status.value)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            for field, value in self._model_values(spec).items():
                setattr(term, field, value)
            if spec.status == ClinicalTermStatus.approved:
                term.reviewed_by = request.user
                term.reviewed_at = timezone.now()
            elif spec.status in {
                ClinicalTermStatus.draft,
                ClinicalTermStatus.in_review,
            }:
                term.reviewed_by = None
                term.reviewed_at = None
            term.updated_by = request.user
            term.save()
        return Response(serialize_clinical_term(term))

    @action(detail=False, methods=["get"])
    def search(self, request, *args, **kwargs):
        query = request.query_params.get("q", "")
        language = request.query_params.get("language", "nl-SR")
        concept_kind = request.query_params.get("concept_kind")
        try:
            count = min(max(int(request.query_params.get("count", 25)), 1), 100)
            if concept_kind and concept_kind not in {
                item.value for item in ClinicalTermKind
            }:
                raise ValueError("Unsupported terminology concept kind")
            results = search_approved_translation_concepts(
                query,
                language,
                count,
                concept_kind,
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(str(exc)) from exc
        return Response({"results": results})

    @extend_schema(request=ClinicalTermResolveRequest, responses={200: None})
    @action(detail=False, methods=["post"])
    def resolve(self, request, *args, **kwargs):
        spec = ClinicalTermResolveRequest.model_validate(request.data)
        concepts = [concept.model_dump() for concept in spec.concepts]
        return Response({"results": resolve_concepts(concepts, language=spec.language)})

    @extend_schema(request=ClinicalTermCsvImportRequest, responses={200: None})
    @action(detail=False, methods=["post"])
    def bulk_import(self, request, *args, **kwargs):
        import_spec = ClinicalTermCsvImportRequest.model_validate(request.data)
        try:
            specs = parse_csv_import(import_spec.csv_text)
            summary = self._import_summary(specs)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if import_spec.dry_run:
            return Response({**summary, "dry_run": True})
        with transaction.atomic():
            for spec in specs:
                term = (
                    ClinicalTermTranslation.objects.select_for_update(of=("self",))
                    .filter(
                        system=spec.system,
                        code=spec.code,
                        language=spec.language,
                    )
                    .first()
                )
                values = self._model_values(spec)
                if term:
                    validate_status_transition(term.status, spec.status.value)
                    for field, value in values.items():
                        setattr(term, field, value)
                    term.updated_by = request.user
                    term.save()
                else:
                    ClinicalTermTranslation.objects.create(
                        **values,
                        created_by=request.user,
                        updated_by=request.user,
                    )
        return Response({**summary, "dry_run": False})

    @action(detail=False, methods=["get"])
    def export_csv(self, request, *args, **kwargs):
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[*CSV_FIELDS, "reviewed_by", "reviewed_at"],
            lineterminator="\n",
        )
        writer.writeheader()
        for term in ClinicalTermTranslation.objects.select_related(
            "reviewed_by"
        ).order_by("language", "system", "code"):
            writer.writerow(
                {
                    "system": term.system,
                    "code": term.code,
                    "language": term.language,
                    "concept_kind": term.concept_kind,
                    "source_display": term.source_display,
                    "preferred_display": term.preferred_display,
                    "synonyms": "|".join(term.synonyms),
                    "status": term.status,
                    "source_name": term.source_name,
                    "source_version": term.source_version,
                    "is_active": str(term.is_active).lower(),
                    "reviewed_by": term.reviewed_by.username
                    if term.reviewed_by
                    else "",
                    "reviewed_at": term.reviewed_at.isoformat()
                    if term.reviewed_at
                    else "",
                }
            )
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="clinical-term-translations.csv"'
        )
        return response

    @staticmethod
    def _model_values(spec):
        values = spec.model_dump()
        values["status"] = spec.status.value
        return values

    @staticmethod
    def _duplicate_response():
        return Response(
            {"detail": "A translation for this system, code and language exists"},
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _import_summary(specs):
        keys = [(spec.system, spec.code, spec.language) for spec in specs]
        existing = {
            (term.system, term.code, term.language): term.status
            for term in ClinicalTermTranslation.objects.filter(
                system__in={key[0] for key in keys},
                code__in={key[1] for key in keys},
                language__in={key[2] for key in keys},
            )
        }
        for spec in specs:
            current_status = existing.get((spec.system, spec.code, spec.language))
            if current_status:
                validate_status_transition(current_status, spec.status.value)
        updates = sum(key in existing for key in keys)
        return {"rows": len(specs), "creates": len(specs) - updates, "updates": updates}

import logging
from uuid import UUID

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from care.emr.models.encounter import Encounter
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care_suriname.scribe.config import load_scribe_config
from care_suriname.scribe.contract import (
    CONTRACT,
    ScribeRequestError,
    audio_mime_type,
    filter_answers,
    parse_fields,
)
from care_suriname.scribe.gemini import ScribeUpstreamError, generate_field_answers
from care_suriname.scribe.pricing import estimate_cost_usd
from care_suriname.scribe.prompt import build_prompt

logger = logging.getLogger(__name__)


def _error(code: str, http_status: int) -> Response:
    return Response({"errors": [{"type": code}]}, status=http_status)


class ScribeFieldDraftView(ClinicalNoStoreResponseMixin, APIView):
    """Consultation audio in, suggested answers for the note's open fields out.

    Nothing is written: the clinician reviews the answers in the editor and
    saves through the normal note commands. The audio is not stored.
    """

    parser_classes = [MultiPartParser]

    def get(self, request):
        """Lets the editor show the microphone only when the scribe is on."""
        config = load_scribe_config()
        return Response(
            {
                "contract": CONTRACT,
                "enabled": config is not None,
                "model": config.model if config else None,
            }
        )

    def post(self, request):
        config = load_scribe_config()
        if config is None:
            return _error("scribe_disabled", status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            encounter_id = UUID(str(request.data.get("encounter")))
        except ValueError as error:
            raise ValidationError("A valid encounter is required") from error
        encounter = get_object_or_404(Encounter, external_id=encounter_id)
        if not AuthorizationController.call(
            "can_submit_encounter_questionnaire_obj", request.user, encounter
        ):
            raise PermissionDenied("Cannot write notes in this encounter")

        audio = request.FILES.get("audio")
        try:
            fields = parse_fields(request.data.get("fields"))
            if audio is None or audio.size == 0:
                raise ScribeRequestError("scribe_audio_missing")
            if audio.size > config.max_audio_bytes:
                raise ScribeRequestError("scribe_audio_too_large")
            mime_type = audio_mime_type(audio.content_type)
            result = generate_field_answers(
                config, audio.read(), mime_type, build_prompt(fields)
            )
        except ScribeRequestError as error:
            return _error(error.code, status.HTTP_400_BAD_REQUEST)
        except ScribeUpstreamError as error:
            logger.warning(
                "scribe call failed encounter=%s code=%s",
                encounter.external_id,
                error.code,
            )
            return _error(error.code, status.HTTP_502_BAD_GATEWAY)

        answers = filter_answers(fields, result.payload.get("answers"))
        output_tokens = result.output_tokens + result.thinking_tokens
        cost = estimate_cost_usd(config.model, result.input_tokens, output_tokens)
        # Usage only: never transcript, answers or audio in the log.
        logger.info(
            "scribe call encounter=%s user=%s model=%s audio_bytes=%s "
            "input_tokens=%s audio_tokens=%s output_tokens=%s thinking_tokens=%s "
            "fields=%s answered=%s est_cost_usd=%s",
            encounter.external_id,
            request.user.id,
            config.model,
            audio.size,
            result.input_tokens,
            result.audio_tokens,
            result.output_tokens,
            result.thinking_tokens,
            len(fields),
            len(answers),
            cost,
        )
        return Response(
            {
                "contract": CONTRACT,
                "encounter": str(encounter.external_id),
                "transcript": result.payload["transcript"],
                "answers": answers,
                "model": config.model,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "audio_tokens": result.audio_tokens,
                    "output_tokens": result.output_tokens,
                    "thinking_tokens": result.thinking_tokens,
                    "estimated_cost_usd": cost,
                },
            }
        )

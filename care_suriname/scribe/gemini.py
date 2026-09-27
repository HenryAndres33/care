"""One generateContent call to Gemini on Vertex AI.

Authentication uses Google Application Default Credentials: the VM's service
account on the server, the owner's `gcloud auth application-default login` file
on the laptop. The audio is sent inline and is not kept anywhere.
"""

import base64
import json
import threading
from dataclasses import dataclass

import requests

from care_suriname.scribe.config import ScribeConfig
from care_suriname.scribe.prompt import RESPONSE_SCHEMA

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_credentials = None
_credentials_lock = threading.Lock()


class ScribeUpstreamError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GeminiResult:
    payload: dict
    input_tokens: int
    audio_tokens: int
    output_tokens: int
    thinking_tokens: int


def _access_token() -> str:
    # Imported here so a missing google-auth can only disable the scribe,
    # never break CARE's URL loading.
    try:
        import google.auth
        import google.auth.exceptions
        import google.auth.transport.requests
    except ImportError as error:
        raise ScribeUpstreamError("scribe_credentials_unavailable") from error

    global _credentials
    with _credentials_lock:
        try:
            if _credentials is None:
                _credentials, _ = google.auth.default(scopes=SCOPES)
            if not _credentials.valid:
                _credentials.refresh(google.auth.transport.requests.Request())
        except google.auth.exceptions.GoogleAuthError as error:
            raise ScribeUpstreamError("scribe_credentials_unavailable") from error
        return _credentials.token


def _usage(metadata: dict) -> tuple[int, int, int, int]:
    audio = sum(
        detail.get("tokenCount", 0)
        for detail in metadata.get("promptTokensDetails", [])
        if detail.get("modality") == "AUDIO"
    )
    return (
        metadata.get("promptTokenCount", 0),
        audio,
        metadata.get("candidatesTokenCount", 0),
        metadata.get("thoughtsTokenCount", 0),
    )


def _payload(body: dict) -> dict:
    candidates = body.get("candidates") or []
    if not candidates or candidates[0].get("finishReason") != "STOP":
        raise ScribeUpstreamError("scribe_model_incomplete")
    text = "".join(
        part.get("text", "")
        for part in candidates[0].get("content", {}).get("parts", [])
        if not part.get("thought")
    )
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise ScribeUpstreamError("scribe_model_malformed") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("transcript"), str):
        raise ScribeUpstreamError("scribe_model_malformed")
    return payload


def generate_field_answers(
    config: ScribeConfig, audio: bytes, mime_type: str, prompt: str
) -> GeminiResult:
    request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(audio).decode("ascii"),
                        }
                    },
                    {"text": prompt},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    try:
        response = requests.post(
            config.endpoint,
            json=request_body,
            headers={"Authorization": f"Bearer {_access_token()}"},
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as error:
        raise ScribeUpstreamError("scribe_upstream_unreachable") from error
    if response.status_code != 200:  # noqa: PLR2004
        code = f"scribe_upstream_http_{response.status_code}"
        raise ScribeUpstreamError(code)
    body = response.json()
    return GeminiResult(_payload(body), *_usage(body.get("usageMetadata", {})))

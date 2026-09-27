"""Scribe settings, read from the environment by the plug.

Kept out of CARE's native settings modules on purpose. The scribe is off unless
SCRIBE_ENABLED is true and a Google Cloud project is named.
"""

import os
from dataclasses import dataclass

TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ScribeConfig:
    project: str
    location: str
    model: str
    max_audio_bytes: int
    timeout_seconds: int

    @property
    def endpoint(self) -> str:
        host = (
            "aiplatform.googleapis.com"
            if self.location == "global"
            else f"{self.location}-aiplatform.googleapis.com"
        )
        return (
            f"https://{host}/v1/projects/{self.project}/locations/{self.location}"
            f"/publishers/google/models/{self.model}:generateContent"
        )


def load_scribe_config() -> ScribeConfig | None:
    if os.environ.get("SCRIBE_ENABLED", "").strip().lower() not in TRUE_VALUES:
        return None
    project = os.environ.get("SCRIBE_GCP_PROJECT", "").strip()
    if not project:
        return None
    return ScribeConfig(
        project=project,
        location=os.environ.get("SCRIBE_GCP_LOCATION", "global").strip() or "global",
        model=os.environ.get("SCRIBE_MODEL", "gemini-3.8-flash").strip(),
        max_audio_bytes=int(os.environ.get("SCRIBE_MAX_AUDIO_MB", "20")) * 1024 * 1024,
        timeout_seconds=int(os.environ.get("SCRIBE_TIMEOUT_SECONDS", "180")),
    )

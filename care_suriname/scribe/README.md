# AI scribe (backend)

Approved by the owner on 27 September 2026 (local build first; no server
deployment yet).

## What it does

`POST /api/care_suriname/scribe/field-drafts/` takes the audio of one
consultation and the list of open SmartText fields (`[[*NAAM*]]`) in the
clinician's Verslag. It sends both to Gemini on Vertex AI and returns a
transcript plus suggested answers for those fields.

Request (multipart): `encounter` (UUID), `audio` (webm/ogg/wav/mp3/mp4/aac/flac,
max `SCRIBE_MAX_AUDIO_MB`), `fields` (JSON list of `{name, type, options}` as
parsed by the frontend's `parseSmartTextPlaceholders`).

`GET` on the same path returns `{contract, enabled, model}` so the editor
shows the microphone only when the scribe is on.

Response `care-suriname-scribe-v1`: `transcript`, `answers`
(`{name, value, evidence}`), `model`, `usage` (tokens and an estimated USD cost).

## Clinical and data boundaries

- **Nothing is written.** The endpoint stores no audio, transcript or answer.
  The frontend puts answers into the open fields of the editor; the clinician
  reviews them and saves through the normal FormSubmission commands. Fields the
  model did not answer stay open, so `Definitief maken` stays blocked.
- **Authorization:** the caller must be allowed to submit notes in the
  encounter (`can_submit_encounter_questionnaire_obj`, which also refuses
  closed encounters), checked before any model call.
- **What leaves the server:** the audio and the field names, types and options.
  No patient name, dossier number or record text. The audio itself contains
  whatever was said.
- **Answer filtering** (`contract.py`): only requested field names, first
  answer per name, choice/multi values limited to the offered options, named
  facility lists without inline options never answered.
- **Log:** one line per call with encounter id, user id, model, token counts
  and estimated cost. Never transcript, answers or audio.

## Configuration

Read from the environment by `config.py` (not from CARE's settings modules).
The scribe is off (HTTP 503 `scribe_disabled`) unless both are set:

| Variable | Default | Meaning |
|---|---|---|
| `SCRIBE_ENABLED` | off | `true` to enable |
| `SCRIBE_GCP_PROJECT` | — | Google Cloud project billed for Vertex AI |
| `SCRIBE_GCP_LOCATION` | `global` | Vertex AI location |
| `SCRIBE_MODEL` | `gemini-3.8-flash` | e.g. `gemini-3.5-flash-lite` |
| `SCRIBE_MAX_AUDIO_MB` | `20` | upload limit (inline Gemini request) |
| `SCRIBE_TIMEOUT_SECONDS` | `180` | Vertex call timeout |

Authentication is Google Application Default Credentials via `google-auth`
(added to `Pipfile`, owner-approved, pinned `==2.58.1`):

- **Laptop:** `docker-compose.local.yaml` mounts the owner's
  `~/.config/gcloud/application_default_credentials.json` read-only and sets
  `GOOGLE_APPLICATION_CREDENTIALS`. The laptop's Docker has no BuildKit, so the
  dev image cannot be rebuilt there; after the backend container is recreated,
  install the pinned package into it:
  `docker exec care-backend-1 pip install google-auth==2.58.1`.
  Without it the scribe answers 502 `scribe_credentials_unavailable`; the rest
  of CARE is unaffected (the import is lazy).
- **Server (not yet done):** a dedicated `care-scribe` service account with the
  Vertex AI User role attached to the VM, plus `SCRIBE_*` in the server `.env`.
  The prod image installs `google-auth` from the lock. The gunicorn worker
  timeout must exceed `SCRIBE_TIMEOUT_SECONDS`: the server runs
  `--timeout=120` (27 September 2026), so set `SCRIBE_TIMEOUT_SECONDS=100`
  there or raise the gunicorn timeout.

## Rollback

Set `SCRIBE_ENABLED=false` (or remove it) and restart the backend: `GET` reports
`enabled: false`, the editor hides the microphone and `POST` answers 503. To remove the feature: delete
`care_suriname/scribe/`, the `scribe/field-drafts/` route in
`care_suriname/urls.py`, the `google-auth` line in `Pipfile` with its three lock
entries (`google-auth`, `pyasn1`, `pyasn1-modules`), and the scribe lines in
`docker-compose.local.yaml`. No model, migration or native Python code is involved.

## Verification

```bash
docker exec care-test-backend-1 sh -c 'DJANGO_SETTINGS_MODULE=config.settings.test python manage.py test care_suriname.scribe --keepdb'
docker exec care-test-backend-1 ruff check care_suriname/scribe
```

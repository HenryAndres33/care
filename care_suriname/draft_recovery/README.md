# Owner-bound draft recovery keys

Owner approved 12 September 2026: encrypted local drafts survive crashes; after
restart the original clinician must reconnect and authenticate to unlock them.
This module stores wrapped random encryption keys, never clinical draft content.
It does not implement the separate history-summary or encounter command proposals.

## API and authentication

- POST `/api/v1/users/me/draft-recovery-key/` with an empty object returns the
  current owner's active key. Concurrent calls serialize on that user row and
  return the same key; a database constraint enforces one active key per owner.
- GET `/api/v1/users/me/draft-recovery-key/<key_id>/` retrieves that owner's exact
  retained key, including a retired key. Another user's ID returns 404, even to
  a superuser. Query/body identity overrides are rejected. No retire/delete API.
- Both return HTTP 200 with `key_id`, `owner_id` (native user external UUID),
  `algorithm: AES-GCM`, `key_material` (standard base64 of exactly 32 bytes),
  `authenticated_at` and `key_release_expires_at` (integer UTC epoch seconds).
- JWT access authentication only; current account must be active, not soft
  deleted and not a service account. TLS is required except DEBUG localhost.
- Password login after authentication, or successful native MFA completion, puts
  `care_authenticated_at` and `care_auth_method` in the signed refresh/access
  tokens. Temporary MFA tokens have neither proof. Refresh preserves the original
  values and never extends recency. `last_login` is deliberately not used, because
  native refresh updates it. Existing tokens need a native login to gain proof.
- Keys are released only within 900 seconds of interactive authentication.
  `key_release_expires_at` is that same release deadline, not a new lease on each GET. An already imported
  memory-only key remains usable in the same uninterrupted verified user session;
  pause/logout/account change/provider teardown/process loss invalidates it. There
  is no periodic password/MFA requirement while that session remains unlocked. Missing,
  old or future proof returns 403 `draft_recovery_reauthentication_required`.
  Missing/insecure wrapping configuration, bad ciphertext or unavailable storage
  returns 503 `draft_recovery_unavailable`. No fallback or silent rekey on failure.
- Every response, including errors, is private/no-store. Application access audit
  logs actor database ID/method/status only, never a key or payload. Generic model-value audit is
  excluded for this model. Deployment tracing/proxies must also exclude response
  bodies. DEBUG tooling must not be exposed in clinical production.

## Protected configuration and activation

`DRAFT_RECOVERY_WRAPPING_KEYS_FILE` points to a deployment-managed JSON keyring:
`{"active_key_id":"v1","keys":{"v1":"<base64 32 random bytes>"}}`.
There is deliberately no default key, SECRET_KEY reuse, token derivation or
plaintext fallback. Use an OS secret facility or protected file outside the repo,
image and clinical database backups. The file must be a regular non-symlink,
owned by the service user or root, with no group/other permissions (0600 or 0400).
The service must be able to read it. Do not paste key values into commands,
terminal output, logs, tickets or this document. Back up the protected wrapping
key separately from the database; loss makes retained ciphertext unrecoverable.

Activation requires a reviewed secret provisioned on the deployment, migration
the immutable table-creation migration `users.0028_draftrecoverykey`, followed by
`users.0029_move_draft_recovery_to_care_suriname` and
`care_suriname.0002_move_draft_recovery`, actual authenticated endpoint checks and a
coordinated service rollout. No migration/config activation on the shared
clinical stack is claimed merely from local source tests. Without configuration,
the application returns unavailable and must not show local protection enabled.

AES-GCM wraps each random draft key using a new random 12-byte nonce; associated
data binds schema, owner external UUID, draft-key UUID and wrapping-key ID. Copying
ciphertext to another owner/key or modifying it fails authentication. See the
[cryptography AEAD reference](https://cryptography.io/en/latest/hazmat/primitives/aead/).
This protects stored ciphertext, not an active compromised application/session.

## Rotation, backup and rollback

To rotate wrapping keys, add a new keyring entry and select it as active, retaining
all previous wrapping entries. Run `manage.py rewrap_draft_recovery_keys`; each
retained record rewraps transactionally without changing its native key ID or
plaintext key. An interrupted run can be retried. Keep old wrapping keys until
all live records AND retained database backups have a tested recovery path. Do not
remove old secrets solely because a current-database rewrap finished.

Database backup includes wrapped key rows; a separately protected keyring backup
is also needed. Verify restoration in an isolated database before release.
Deactivating/deleting an account prevents online key release; model PROTECT blocks
hard deletion of an owner with keys. Neither action deletes encrypted local drafts
or wrapped key records. Authorized unsent-work disposition must be resolved before
any eventual irreversible key deletion. No automatic seven-day deletion exists.

Rollback first disables frontend integration and key release, retaining keys and
local ciphertext for original-owner recovery. Do not reverse the additive table
migration or delete the wrapping file while unsent drafts may exist. Code rollback
is not data deletion. The API has no key-destruction or clinical write commands.

## Verification

Backend tests cover API identity/TLS/authentication, login versus refresh proof,
concurrent first creation, hostile configuration and tampered ciphertext, retained
keys and wrapping rotation. Run them only on a disposable test database:
`DJANGO_SETTINGS_MODULE=config.settings.test DJANGO_TEST_DATABASE_NAME=test_care_draft_keys_codex python manage.py test care_suriname.tests.test_draft_recovery care_suriname.tests.test_draft_recovery_ownership --noinput`.
Use test-stack PostgreSQL, never the development database or a care_test reset.

Pending separate acceptance: independent code/security review, real deployment
secret/backup restore, native authenticated live response, editor integration,
account/session transitions and real browser-disk crash recovery. A passing API
suite alone does not certify Phase 1 or any locally protected badge.

## Plugin ownership — 19 September 2026

This is the Urology encrypted-draft client contract introduced in custom commit
`530eafb9e`, absent at CARE fork `ece71a878`. The implementation and auth-proof
helper live here; the model is `care_suriname.models.draft_recovery.DraftRecoveryKey`.
Its existing table remains `users_draftrecoverykey`. Applied users migrations are
immutable. The new paired migrations change model state and relabel the existing
content type in place; no table/column/index/constraint/row-copy operation occurs.
Content-type ID and permission IDs survive. The new permission label is
`care_suriname.<action>_draftrecoverykey`; no application caller checks these Django
permissions (the endpoint still enforces owner identity and recent authentication).

The only native implementation callers are the existing password-login and MFA
proof hooks in `config/auth_views.py` and `care/emr/utils/mfa.py`. They import
`auth.interactive_refresh_token`; CARE has no shared post-interactive-auth hook.
Refresh and temporary MFA token behavior are unchanged. Settings keep their exact
environment names and defaults; only the generic audit-exclusion model label moves.

For a code rollback, on a backed-up/quiesced stack first run
`manage.py migrate users 0028` with this revision's migration files available,
then restore the previous code. This reverses plugin0002 and users0029 only;
never reverse users0028. Resume writes after verifying the users content type and
same key rows. Forward migration is `manage.py migrate`; duplicate target or missing
installed content types fail closed. Empty installs get their content type from
post_migrate. Do not delete duplicate identities to force a live migration through.

Rehearsal, laptop state and exact verification are recorded in
[the ownership report](../../docs/development/2026-09-19-draft-recovery-ownership.md).

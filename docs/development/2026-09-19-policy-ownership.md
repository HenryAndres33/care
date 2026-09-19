# Backend policy ownership — 19 September 2026

## Status and decision

Implemented and browser-verified. The owner explicitly accepts the reproduced
baseline permission failure for this batch: the commit gate is **no new failures**.
Authorization behavior remains unchanged. The exact staged-tree checks below
cover this commit; no deployment or clinical certification is implied.

Baseline: `ed77ad934458cbc5cce08a6154618c47d0bff02d`, branch
`codex/suriname-clinical-workflows`. Upstream/fork:
`ece71a878b3764a476d713a163a2f5515db57581`. Push destination remains
`henry-fork/codex/suriname-clinical-workflows`; push explicitly after staged checks.

## What moved and what stayed

1. Native `care/emr/api/viewsets/valueset.py` loses specialty slugs, Dutch locale
   selection, approved-translation lookup, merge/dedup/count policy and its direct
   plugin import. `care_suriname/policies/terminology.py` owns the same algorithm.
   Native request validation, object lookup, authentication, action/URL and native
   search fallback remain. One generic optional callback returns expansion items;
   it does not duplicate the viewset or replace permission checks.
2. The entire existing `urology_recent_patients` JSON schema moves from native
   `config/settings/config.py` to plugin `policies/preferences.py`. It is merged
   with unchanged native defaults before the existing environment replacement.
   Max 50 facility keys / eight entries per facility, identity-only fields,
   UUID bounds and validation remain identical. No new runtime value or secret.
3. Required-form maps move from base, local **and test** settings to plugin
   `policies/settings.py`. Base still parses the same JSON environment variable;
   local/test explicit overrides still take precedence over it. The two existing
   local department UUIDs and form slug are preserved. The AZP documentation/test
   now point to the owning map. No deployment setting was edited.
4. `ClinicalDomainChoices` moves to plugin `policies/condition.py`; native specs
   use a generic single-provider type contribution. The enum values, order,
   default instance, Pydantic errors and generated schemas are unchanged.
   `care/emr/models/condition.py` is **untouched**: its open CharField, maximum
   length 64 and `general` default remain the persisted-table contract. There
   were no model choices to move. Native status/category/onset/modified fields,
   idempotency constraints and command orchestration remain unchanged.

Native direct imports fall 12 files / 28 statements → **11 / 27**. No new reverse
plugin dependency. Full callback and startup contract:
[`care_suriname/policies/README.md`](../../care_suriname/policies/README.md).

## Generic seam

`plugs/contributions.py` discovers optional `contributions.py` modules through
CARE's existing plug manager, before Django app readiness. The module exports a
mapping, not model-dependent registration side effects. Missing optional modules
are allowed; missing dependencies within a provider propagate. Single-provider
conflicts and duplicate mapping keys fail closed; schemas are schema-validated.
Base settings may not override host names. Profile overrides may target only
plugin-contributed names. Defaults are copied. Plugins remain trusted Python code.

The terminology adapter imports its model-dependent implementation only on
invocation. The condition type is available during native spec construction.
An app-ready registration would be too late for settings and Pydantic schemas.
The existing extension registry covers extension payloads, not these top-level
fields or expansion results. A full copied viewset or broad middleware was avoided.

Without contributions, native expansion/default preferences remain unchanged;
the retained condition field accepts strings. This is a seam-level absence test,
not a claim that the other eleven native plugin consumers support removal yet.
These small seams are upstream PR candidates, not upstream-approved interfaces.

## Exact comparison and verification method

Commands used (scratch outputs are outside the repository):

```sh
git rev-parse HEAD
git status --short
git diff ece71a878 -- care/emr/api/viewsets/valueset.py care/emr/resources/condition/spec.py
git diff --numstat ed77ad934
git diff -U0 ece71a878 -- care config
git checkout-index --all --prefix=/tmp/care-policy-ownership-20260919/staged/
```

Baseline and candidate are isolated source exports. Tests run in one-shot
`care_local` containers on `care-test`, against the task-created
`test_care_policy_codex` restored from the isolated stack. The harness skips all
migration setup, asserts the disposable database name/host and uses temporary
MinIO buckets with credentials held only in memory. No live fixture reset.
Native `check` and `makemigrations --check --dry-run` run read-only.

- 16 focused contribution/policy/ownership/AZP tests pass without database setup.
- Combined candidate: 121 tests, **120 pass / one baseline failure**.
- Exact baseline: 91 tests, **90 pass / the same failure**.
- Existing translation API tests cover approved/draft terms, user-independent
  lookup, procedure/condition isolation, source language and shared results.
- Existing user tests cover valid recency and rejection of clinical fields.
- Diagnosis tests cover idempotent create, exact replay, changed replay and active
  duplicates. Closure tests cover required-document selection and fail-closed
  finalization. Generic tests cover absence, broken modules, conflicts, schema
  validation, default copying and environment/profile precedence.
- Pydantic create/update/chronic schemas, native field deconstruction, preference
  schema and required-form settings are byte-identical to the baseline.
- DRF OpenAPI for every value-set and diagnosis route is byte-identical.
- Six configuration comparisons pass: base/local/test with defaults and explicit
  replacement environment values. Environment overrides were synthetic, not
  deployment values. No secret settings were inspected or emitted.
- Scoped Ruff and format, system check, migration drift and diff checks pass.
  No model edit, migration file, schema SQL or row transformation.

The initial restored fixture contained `system-condition-code`, which two tests
unconditionally create. Both baseline and candidate hit the same unique-key
error. Removing those two seeded value-set slugs **only in the disposable clone**
before each test run resolves that harness collision. No source test weakened.
Host compileall also hit existing root-owned cache permissions; runtime syntax
and imports were verified in the container instead, without permission changes.

### Known baseline failure accepted for this batch

`care.emr.tests.test_diagnosis_idempotent_api.TestDiagnosisUpdateAuthorizationRegression.test_read_only_user_cannot_update_chronic_condition`
expects 403, receives 200. Reproduced before and after fixture cleanup at baseline
`ed77ad934`, and in the candidate. This is an unresolved permission/fixture
mismatch, **not certified harmless** and not an extraction regression. No code
outside the policy claim was changed. After reviewing the baseline reproduction,
the owner explicitly authorized commit/push with this known failure documented.
This does not certify the existing permission behavior as correct.

## Signed-in browser evidence

In-app Browser skill; unchanged previously verified frontend build served only on
`http://127.0.0.1:4001`, existing local API on port 9000. The existing backend uses
a bind-mounted source tree and Django reload; no service restart was issued.
Port 4000 was neither opened, rebuilt nor restarted. Account credentials were
used only for authorized sign-in and never put in files/evidence.

Facility `77d446f3-659d-40d4-a69a-bfc0f5b7a255`; designated synthetic patient
`78fd6f11-e0f1-4be5-a988-04a351c7cc58` (DEMO-SIM-0912-S05).

| Browser path/action | Observed result |
|---|---|
| `/facility/<facility>/urology/patients/search` | Synthetic lookup and chart opening succeed |
| `/facility/<facility>/patient/<patient>/urology/chart?view=diagnosis` | Search `prostaat` returns approved Dutch labels, including Benigne prostaathyperplasie; expand POST 200 |
| Select diagnosis and save explicitly marked synthetic note | Idempotent-create POST 201; `clinical_domain=urology`, `replayed=false` |
| Diagnosis list and hard refresh | GET 200; one diagnosis, Dutch label, Urologische voorgeschiedenis and exact synthetic note persist |
| `/` → Recent geopende patiënten | Synthetic patient appears; preference save 201 and read 200 |
| Recent patient → chart | Correct synthetic chart; one urological history item |
| Expand history → Verwijderen → confirm | Native PUT 200; active list returns to zero, no physical/audit deletion |

Synthetic diagnosis audit identity: `3a4784fb-f5f4-40bc-a858-a26ed01e52e2`.
Note: “SYNTHETISCHE TEST policy ownership 2026-09-19; geen echte klinische diagnose.”
The supported UI removes it from the active list while retaining medical/audit
history. That marked synthetic record and its command/audit history intentionally
remain; no real-patient mutation. Recency preference changes also remain.

No HTTP failure or console error. Three canceled navigation requests reported
`ERR_ABORTED`; these were cancellations, not server failures. CORS OPTIONS 200s
are not extra clinical writes. Existing mixed English shell/category labels were
not changed by this backend batch. No PDF workflow was changed or exercised.
Browser tab closed and the owned port-4001 process stopped; socket check empty.

## Ownership and remaining work

Candidate native care/config non-test Python: **37 files / 179 hunks /
+3004/−249** relative to fork. Root generic plug files are outside that inventory.
No newly restored byte-identical core file; the prior eight remain restored.
Native Condition model is byte-identical to the task baseline (its historical
invariant delta relative to fork intentionally remains).

Candidate F implementation incidence: **8 → 3 native files** — condition,
form_submission and medication_request viewsets. Remaining groups are diagnosis
commands, form/artifact command orchestration, and medication commands. Including
the previous directory/constants batch, 5/8 finite backlog groups are implemented
(62.5%); this is neither a weighted overall percentage nor 100% completion.
Historical migration ownership and all 35 custom models remain unchanged.

All 1,346 unclaimed tracked backend hashes match the start snapshot. Frontend
source, environment files, prior work and deployment are unchanged; only the
shared append-only BUS receives this task's claim/release. Final staged metrics
and verification are recorded below. No production VM action.

## Exact staged-tree result and production file sizes

The isolated staged tree repeats **121 tests: 120 pass, only the accepted baseline
permission failure**. Scoped Ruff/format (17 Python files), system check and
read-only migration drift pass. All model and migration files are unchanged.
The index contains only the 23 claimed task files; added-text credential/private-key
scans and both staged/worktree diff checks pass. No dependency on excluded source.

| Production file | Current lines |
|---|---:|
| `care/emr/api/viewsets/valueset.py` | 240 |
| `care/emr/resources/condition/spec.py` | 157 |
| `care_suriname/contributions.py` | 20 |
| `care_suriname/policies/__init__.py` | 0 |
| `care_suriname/policies/condition.py` | 8 |
| `care_suriname/policies/preferences.py` | 60 |
| `care_suriname/policies/settings.py` | 22 |
| `care_suriname/policies/terminology.py` | 44 |
| `config/settings/base.py` | 756 |
| `config/settings/config.py` | 334 |
| `config/settings/local.py` | 74 |
| `config/settings/test.py` | 122 |
| `plugs/contributions.py` | 71 |

Existing native settings files exceed the module-size guideline; this extraction
reduces their policy content. Every new production module is below 300 lines.
No new byte-identical upstream file in this batch; no runtime compatibility shim.

Commit gate disposition: owner explicitly authorizes the known baseline failure;
no new failures in candidate or exact staged tree. Authorization remediation is
a separate task. Frontend source/build, production deployment and port 4000 are
unchanged. The task-owned restored database and temporary MinIO buckets are
removed after verification. Shared BUS release is outside this backend commit.

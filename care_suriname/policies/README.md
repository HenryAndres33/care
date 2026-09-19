# Suriname policy contributions

`care_suriname.contributions.CONTRIBUTIONS` is the early, model-free declaration
surface consumed by `plugs.contributions`. The package remains plugin-owned;
CARE contains no locale, specialty slug, recent-patient schema or department map.

- `terminology.expand` preserves the authenticated native expansion endpoint.
  Only `nl` / `nl-SR` (case-insensitive) selects the Dutch overlay. Native search
  receives `en-gb`; approved local matches precede resolved source results.
  `(system, code)` deduplication and count truncation preserve the former order.
  Other languages retain native results, including duplicates. Condition and
  procedure slugs retain separate concept kinds. This is not a copied viewset.
- `preferences.PREFERENCE_SCHEMAS` owns the version-one identity-only recent list:
  50 facilities, eight patients per facility, UUID/name/MRN/date-time shapes.
  Native `facility_quick_links` stays native. Contributions merge into defaults
  **before** `env.json("PREFERENCE_SCHEMA", ...)`; an explicit environment value
  still replaces the entire map. No validation behavior or payload bound changed.
- `settings.SETTING_DEFAULTS` owns required-document policy for base/local/test.
  Base reads the existing JSON environment variable at the original point.
  Local and test explicitly replace base (including its environment override),
  exactly as before. Local includes the two existing department UUIDs; production
  must not assume those UUIDs exist. No secret or deployment value moved.
- `condition.ClinicalDomainChoices` owns `general` / `urology`. Native API specs
  select this type through a generic single-provider contribution; schema,
  Pydantic enum errors, default enum instance and command hashes are unchanged.
  The native Condition column remains an open CharField with the existing
  `general` default: that persisted-table compatibility sentinel is not relocated.
  It has no model choices, so no model import, metadata or migration change.

## Host seam and lifecycle

An installed plug package may export `CONTRIBUTIONS` from `contributions.py`.
Only a missing optional module is ignored; an import error within it propagates.
Providers must be importable before Django initialization, without model imports.
The terminology callback imports model-dependent code only on invocation.

`single` rejects competing providers. `merge_mapping` rejects duplicate keys,
including collisions with host defaults; preference schemas are schema-validated.
`apply_settings` permits base additions and explicit profile overrides only for
plugin-contributed setting names. It does not give a plugin a host-setting override.
Defaults are copied. Missing contributions preserve native expansion and preference
defaults; the retained clinical-domain field falls back to an open string. This
unit-level absence guarantee is not a claim that all other backend plugin imports
have already been removed.

These narrow callbacks/mappings are upstream candidates, not accepted upstream
APIs. A full viewset replacement or app-ready monkey patch is unnecessary.
Roll back this code-only batch as a unit; no database rollback or migration.

Tests: `plugs.tests.test_contributions`,
`care_suriname.tests.test_policy_contributions`, native translation/user/diagnosis
and closure tests. See [verification](../../docs/development/2026-09-19-policy-ownership.md)
for baseline failures and the synthetic browser artifact audit identity.

## Completed-consultation patient scope — 19 September 2026

`patient_access.completed_department_ids` owns the completed-encounter policy.
The early `patient_organization_ids` contribution lazily imports it on invocation.
It returns the union of existing facility_organization_cache IDs for this patient's
completed encounters (including native ancestor/root IDs), or an empty set when
the unchanged environment-overridable feature switch is disabled. The ORM filter,
manager, selected column and query count are unchanged. Cancelled/discontinued/
entered-in-error encounters do not contribute; native active encounter behavior
continues independently.

The generic `plugs.authorization.patient_organization_ids` host helper accepts
exactly one optional callable and a set of positive native integer organization
IDs. Duplicate providers fail before either runs; invalid results/provider errors
abort the role lookup, never produce an allow or fallback. Absence adds nothing.
Native PatientAccess still filters membership by the requesting user and checks
RolePermission. The contribution cannot overwrite native roles or return a
boolean access decision. Set union is order independent; there is no provider
priority. A trusted plugin can expand scope, so its policy needs security tests.

This shared role lookup also feeds direct permission serialization and existing
write-permission consumers: restricting this hook to reads would change baseline
behavior. It adds no permission a user's matching role does not already hold.
Native list filtering, direct patient links, geographical organization access,
active encounters and superuser handling are untouched. Baseline inactive/soft-
deleted filtering stays the native default manager's responsibility.

Roll back the code-only extraction as one commit, or disable the existing policy
switch to use native behavior. No model, migration, URL, settings default or
frontend change. See [verification and final audit](../../docs/development/2026-09-19-patient-access-ownership.md).

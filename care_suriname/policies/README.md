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

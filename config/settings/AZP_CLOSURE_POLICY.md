# AZP local required-document policy

Owner approved 10 September 2026. `local.py` now maps AZP's Urologie department
UUID `d1dd82e0-0690-4121-94d8-7605b27192ee` to `urology-medisch-dossier`.
The existing generic and historical fixture mappings are preserved. This fixes
`department_invalid` caused by the current department being absent from the
consult-closure policy. It does not disable token, PDF, permission or finalization
checks, and does not close or alter any patient visit.

Only local development settings changed. Production still requires an explicit
`CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT` deployment configuration; do not
assume development UUIDs exist in another database.

Upstream review: retain exact department/form identity, verify the policy setting
name and backend `_required_form_slugs` behavior, and rerun live preflight.
Rollback: remove this one AZP mapping; existing policies remain unchanged, but
AZP closure will again be blocked. No database rollback or migration is needed.

Verification:

```bash
python config/settings/tests/test_azp_closure_policy.py
ruff check config/settings/local.py config/settings/tests/test_azp_closure_policy.py
ruff format --check config/settings/local.py config/settings/tests/test_azp_closure_policy.py
```

The running `care-backend-1` settings read confirmed `config.settings.local` and
the expected single required form. Follow with browser preflight against the
synthetic AZP visit; missing later prerequisites must remain explicit blockers.

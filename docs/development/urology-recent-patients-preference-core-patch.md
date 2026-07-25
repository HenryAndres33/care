# Urology recent-patients preference core patch

## Rationale

The urology dashboard promises a per-user list of recently opened patient
charts. Browser storage is not an acceptable production source for patient
identity metadata, and the previous development-only storage gate left the
production list permanently empty. CARE already owns authenticated user
preferences, so the plugin now stores only bounded navigation metadata there.

## Controlled core change

- `config/settings/config.py` adds the `urology_recent_patients` JSON schema.
- `care/emr/tests/test_user_api.py` verifies an accepted preference and rejects
  additional clinical fields.

The schema permits at most 50 facilities and eight patients per facility. Each
entry contains only the CARE patient id, display name, MRN, and last-opened
timestamp. Diagnoses, encounters, notes, medications, and other clinical
content are rejected.

## Upgrade review

When updating upstream CARE:

1. confirm `POST /api/v1/users/set_preferences/` still writes only the
   authenticated user's preference;
2. confirm `PREFERENCE_SCHEMA` remains the authoritative validation registry;
3. rerun the focused user API and frontend patient-recency tests;
4. review whether CARE has introduced a dedicated recent-patient resource that
   should replace this preference.

## Rollback

Remove the `urology_recent_patients` schema entry and switch the plugin dashboard
to an honest unavailable state. Do not restore browser-local patient identity
storage.

## Verification

```bash
docker compose exec -T backend python manage.py test \
  care.emr.tests.test_user_api.UserviewTestCase.test_sets_valid_urology_recent_patient_preference \
  care.emr.tests.test_user_api.UserviewTestCase.test_rejects_clinical_content_in_urology_recent_patient_preference
npx tsx --test \
  src/Plugins/urology/patient-recency/tests/patientRecencyPreference.test.ts
```

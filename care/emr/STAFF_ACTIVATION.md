# Doctor activation

Owner-approved additive extension, 12 September 2026.

`GET/POST /api/v1/users/{username}/clinical_activation/` accepts a facility UUID
(query for GET, JSON for POST). Only active human system administrators may
inspect or activate an ordinary doctor. GET returns username, verified, and
configured/missing/review. POST rejects review cases without changes.

The service uses native User.verified and OrganizationUser. It requires an
active named human doctor, an active facility/department with the native system
Doctor appointment, the unique active Doctor role organization, and the normal
system Member role with questionnaire read/submit permissions. It never grants
administrative roles, replaces an alternate membership, or changes clinical
authorization. Department-specific and patient-specific checks still apply.

POST locks the native user and configuration records, repairs the missing Member
link, verifies identity and writes a Django admin LogEntry in one transaction.
Repeated activation is idempotent. Audit failure rolls back both changes. Audit
records identify the approving administrator, target user, facility and changes;
no credentials or patient information are recorded. Verification is global in
CARE, so facility administrators are deliberately not allowed to set it here.

The staff wizard calls this after native account creation and department
assignment. Existing Artsentoegang invokes the same operation. If activation
or its read-back fails, retain the account and show repair guidance. Do not
delete an account whose successful activation response may have been lost.

Core footprint: one import and mixin on UserViewSet. No migration, new user
model, new clinical store, or changes to correspondence author validation.

Rollback: remove the frontend activation calls and UserViewSet mixin/import,
then remove the two extension modules. Preserve native memberships, verified
identities and audit history; do not bulk unverify doctors. Any revocation must
be an explicit administrator decision. Existing clinical records remain intact.

Tests: `python manage.py test care.emr.tests.test_doctor_activation
--settings=config.settings.test --keepdb --noinput` in the isolated test stack.

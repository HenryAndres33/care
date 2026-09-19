# Final backend separation audit: exact diff inventory

Audited base `ece71a878b3764a476d713a163a2f5515db57581` → HEAD `db5051c64368310fcf8622cb119a1d541e3ec513` on 19 September 2026.

This appendix records every native Python production hunk plus test settings and root plugin wiring. Hunk headers use Git unified-zero old/new coordinates; counts are added/deleted lines, not whole-file ownership. See the [decision report](2026-09-19-final-backend-separation-audit.md) for findings and verification.

Categories: **A** intentional native data/clinical safety; **B** plugin integration (necessity assessed individually); **C** generic upstream candidate; **D** configuration/generated/non-production; **E** possible obsolete/redundant patch pending proof; **F** remaining custom implementation/policy. Mixed files have multiple categories. No E item is certified safe to delete.

## Native source inventory

| Path | + / − | Categories | Intent and disposition |
|---|---:|---|---|
| `care/audit_log/helpers.py` | 6 / 0 | A/C/E | Domain-ledger audit exclusion before normal filters. Existing AUDIT_LOG.models.exclude.models supports equivalent scopes; candidate redundant branch only after configuration/secret-exclusion parity tests. |
| `care/emr/api/viewsets/condition.py` | 152 / 8 | A/B/F | Native update/read authorization tightening; custom idempotent-create action, duplicate-active checks, replay and transaction orchestration remain here. Move action to plugin only after route/permission/locking parity proof. |
| `care/emr/api/viewsets/device.py` | 37 / 26 | A/C | Atomic encounter/device association locks; closed-encounter veto; disassociation end handling. Keep native write protection; upstream transaction/transition hook candidate. |
| `care/emr/api/viewsets/encounter.py` | 140 / 28 | A/B | Closed encounter filters, locked update/restart/booking and active-inpatient uniqueness; plugin admission mixins and ConsultClosure restart veto. New mixin actions may be remounted; native legacy-write guards cannot simply disappear. |
| `care/emr/api/viewsets/form_submission.py` | 1511 / 16 | A/B/F | Legacy authorization, version/immutability guards plus six custom command actions and PDF/artifact/ledger/series/Urology validation orchestration. Standalone helpers moved, command engine did not. Highest-risk remaining ownership work; retain all native safety vetoes. |
| `care/emr/api/viewsets/location.py` | 78 / 24 | A/C | Lock encounter/location/association before validation and mutation; closed encounter veto and discharge closing. Native safety patch, upstream atomicity candidate. |
| `care/emr/api/viewsets/medication_request.py` | 290 / 3 | A/B/F | Native create/update/destroy locks and closed-encounter checks; no-store mixin; custom idempotent-create/reconcile and replay/prescription orchestration. Commands separable only with exact transaction and side-effect parity. |
| `care/emr/api/viewsets/patient.py` | 69 / 2 | C/F | Generic date-of-birth filter plus custom directory action, minimum-name rule, request DTO and capped pagination. Native PatientViewSet.directory is absent at fork; explicit plugin route can own it once native action is removed. |
| `care/emr/api/viewsets/report/report_upload.py` | 29 / 2 | A/B/C | Clinical no-store, provenance eager loading, generated artifact archive refusal. Generic caching/provenance candidates; native archive veto remains required. |
| `care/emr/api/viewsets/scheduling/booking.py` | 103 / 52 | A/B/C | Plugin OperationPlanMixin plus terminal-state immutability and authoritative booking/slot/queue locks. Custom mixin routes may be remounted; retain native safety checks. |
| `care/emr/api/viewsets/scheduling/schedule.py` | 36 / 4 | A/B/C | Resource locks and plugin overlap validation across schedules, atomic update/destroy. Generic overlap validation/transaction hook candidate; requires all native write paths. |
| `care/emr/api/viewsets/scheduling/token.py` | 48 / 11 | A/C | Terminal token immutability, row locks and queue serialization; authorize_destroy recursion fix. Recursion fix already in cached origin/develop; merge instead of duplicate PR. |
| `care/emr/api/viewsets/user.py` | 8 / 2 | A/B/C | Plugin doctor activation action; ProtectedError hard-delete fallback to soft delete. Action potentially plugin-mounted; deletion fallback generic but must preserve caller expectations. |
| `care/emr/api/viewsets/valueset.py` | 39 / 2 | B/F | Dutch/nl-SR locale policy, condition/procedure slug mapping, approved translation lookup and merge/deduplication in native expand. Plugin owns resolver/data but not orchestration. Future generic expansion resolver hook; move policy behind narrow plugin boundary first. |
| `care/emr/models/__init__.py` | 2 / 0 | C | Discover native ReportUpload and Template models. Native registration, not custom model ownership. |
| `care/emr/models/condition.py` | 14 / 0 | A/F | Native Condition clinical_domain, request UUID/hash and unique constraint; schema retained in native app. Urology domain contract remains coupled; do not remove schema in ownership-only work. |
| `care/emr/models/encounter.py` | 16 / 0 | A/C | Partial unique constraint for one active inpatient encounter per patient. Native table invariant. |
| `care/emr/models/medication_request.py` | 27 / 0 | A | Idempotency UUID/hash pair and uniqueness constraints on native medication rows. Native table invariant. |
| `care/emr/models/questionnaire.py` | 122 / 1 | A/F | FormSubmission version/series/amendment/finalization/entered-in-error fields, constraints and save guard. Plugin-only command constraint-name constant remains at line 13; relocate independently without changing constraint string. |
| `care/emr/models/report/report_upload.py` | 93 / 1 | A/F | Nullable template, native provenance FKs/hashes/generated metadata, uniqueness/checks and immutability guard. Plugin-only artifact-command constraint-name constant remains at line 14; not dead code. |
| `care/emr/models/report/template.py` | 23 / 0 | A/B/C | Native resource version/hash maintained on save through plugin helper. Generic versioned-template hashing hook or upstream implementation candidate. |
| `care/emr/registries/system_questionnaire/system_questionnaire.py` | 26 / 1 | C | Builtin structured-resource model fallback and get_resource_model for structured action cloning. Generic registry capability suitable for upstream review. |
| `care/emr/resources/condition/spec.py` | 8 / 0 | A/F | Adds only ClinicalDomainChoices (general/urology) and clinical_domain on create/read/update. Status/category/onset metadata are upstream fields, not part of this delta. Custom vocabulary coupled to native Condition schema; not an extracted command DTO. |
| `care/emr/resources/encounter/constants.py` | 9 / 0 | A | Shared discharged/completed clinically-closed set prevents administrative status interpretation from reopening clinical writes. |
| `care/emr/resources/encounter/spec.py` | 3 / 2 | C | ExtensionListRenderer and super() include registered extensions in list/read rendering. Generic extension correctness fix. |
| `care/emr/resources/form_submission/spec.py` | 50 / 8 | A/C | Versioned draft/update/read contract, extra-field rejection and patient/encounter consistency; native resource remains native. Shared native model contract is not an independently movable custom model. |
| `care/emr/resources/medication/request/spec.py` | 26 / 21 | C | Extract existing prescription construction into reusable resolve_created_prescription; no copied native model. Generic reuse refactor. |
| `care/emr/resources/patient/spec.py` | 17 / 0 | F | Custom PatientDirectorySpec identity-only response DTO; native identifiers reused. Move with directory API descriptor/view, preserve response shape. |
| `care/emr/resources/report/report_upload/spec.py` | 18 / 2 | A/C | Expose native generated-artifact provenance and optional template/uploader fields; keep aligned with native table schema. |
| `care/emr/resources/report/template/spec.py` | 2 / 0 | A/C | Expose native template resource_version/content_hash used by immutable artifact provenance. |
| `care/emr/resources/scheduling/schedule/spec.py` | 2 / 2 | C | Strict interval overlap allows adjacent intervals. Generic validation fix. |
| `care/emr/utils/mfa.py` | 2 / 1 | B | Successful interactive MFA obtains plugin authentication-proof token. Missing common post-auth claim hook; removing it breaks encrypted draft recovery recency proof. |
| `care/security/authorization/patient.py` | 9 / 1 | B | Completed-encounter department access behind setting. Native permissions directly instantiate PatientAccess, bypassing a plugin override; generic role-resolution hook candidate. Authorization policy, not a write-time model guard. |
| `config/auth_views.py` | 2 / 1 | B | Successful password login obtains plugin authentication-proof token; same missing generic hook as MFA. |
| `config/settings/base.py` | 50 / 4 | D/F | Workflow/facility/Urology policy and key-recovery settings; audit exclusions, timezone and Celery UTC. Configuration, including custom policy values, remains native; no secret values reproduced here. |
| `config/settings/config.py` | 62 / 1 | D/F | Department-access flag and bounded urology_recent_patients preference schema. Schema already configurable with env.json; plugin/config ownership may remove embedded Urology policy without a new core hook, subject to merge/validation parity. |
| `config/settings/local.py` | 13 / 0 | D | Local synthetic workflow gates and department mapping. Development configuration, not extracted domain implementation. |
| `config/settings/test.py` | 11 / 0 | D | Test database name and synthetic gates. Non-production test settings; excluded from production count. |
| `config/urls.py` | 7 / 0 | C | Generic optional plug v1_urls mount after native routes. Existing native matches win; no same-key override. Suitable upstream seam; moving actions must remove native route before plugin can own identical URL. |
| `plug_config.py` | 13 / 1 | D | LocalPlugManager registers care_suriname through get_apps despite plugs=[]; local integration wiring, not an upstream core model/service. |

## Exact source hunk coordinates

### `care/audit_log/helpers.py`

+6 / −0; 1 hunks; 120 current lines.

```diff
@@ -86,0 +87,6 @@ def exclude_model(model_name):
```

### `care/emr/api/viewsets/condition.py`

+152 / −8; 8 hunks; 297 current lines.

```diff
@@ -0,0 +1 @@
@@ -2,0 +4 @@ from django_filters.rest_framework import DjangoFilterBackend
@@ -3,0 +6,2 @@ from rest_framework import filters as rest_framework_filters
@@ -4,0 +9 @@ from rest_framework.exceptions import PermissionDenied, ValidationError
@@ -9,0 +15 @@ from care.emr.models.encounter import Encounter
@@ -22,0 +29,5 @@ from care.utils.shortcuts import get_object_or_404
@@ -138,8 +149 @@ class DiagnosisViewSet(
@@ -151,0 +156,140 @@ class DiagnosisViewSet(
```

### `care/emr/api/viewsets/device.py`

+37 / −26; 6 hunks; 478 current lines.

```diff
@@ -43 +43 @@ from care.emr.resources.device.spec import (
@@ -180,4 +179,0 @@ class DeviceViewSet(EMRModelViewSet):
@@ -185,18 +181 @@ class DeviceViewSet(EMRModelViewSet):
@@ -203,0 +183,32 @@ class DeviceViewSet(EMRModelViewSet):
@@ -455,2 +466,2 @@ class DeviceServiceHistoryViewSet(
@@ -467 +478 @@ def disassociate_device_from_encounter(instance):
```

### `care/emr/api/viewsets/encounter.py`

+140 / −28; 17 hunks; 549 current lines.

```diff
@@ -4 +4 @@ from django.conf import settings
@@ -28,0 +29,5 @@ from care.emr.models import (
@@ -31 +36,6 @@ from care.emr.models.patient import PatientIdentifier, PatientIdentifierConfig
@@ -43,0 +54 @@ from care.emr.resources.patient_identifier.default_expression_evaluator import (
@@ -51,0 +63,7 @@ from care.utils.time_util import care_now
@@ -60 +78 @@ class LiveFilter(filters.CharFilter):
@@ -62 +80 @@ class LiveFilter(filters.CharFilter):
@@ -108,0 +127,2 @@ class EncounterViewSet(
@@ -130,0 +151,42 @@ class EncounterViewSet(
@@ -131,0 +194,8 @@ class EncounterViewSet(
@@ -132,0 +203,14 @@ class EncounterViewSet(
@@ -138 +222 @@ class EncounterViewSet(
@@ -144,6 +228,6 @@ class EncounterViewSet(
@@ -164,0 +249,11 @@ class EncounterViewSet(
@@ -179,2 +273,0 @@ class EncounterViewSet(
@@ -261,5 +354,9 @@ class EncounterViewSet(
@@ -267,10 +364,25 @@ class EncounterViewSet(
```

### `care/emr/api/viewsets/form_submission.py`

+1511 / −16; 21 hunks; 1589 current lines.

```diff
@@ -0,0 +1,6 @@
@@ -1,0 +8,3 @@ from django_filters import rest_framework as filters
@@ -2,0 +12 @@ from rest_framework.exceptions import PermissionDenied, ValidationError
@@ -13 +23,12 @@ from care.emr.models.patient import Patient
@@ -15,0 +37 @@ from care.emr.resources.form_submission.spec import (
@@ -22,0 +45,54 @@ from care.utils.shortcuts import get_object_or_404
@@ -34,0 +111 @@ class FormSubmissionViewSet(
@@ -47,0 +125,6 @@ class FormSubmissionViewSet(
@@ -49 +132,2 @@ class FormSubmissionViewSet(
@@ -51,3 +135,7 @@ class FormSubmissionViewSet(
@@ -55 +143 @@ class FormSubmissionViewSet(
@@ -58,0 +147 @@ class FormSubmissionViewSet(
@@ -60,3 +149,3 @@ class FormSubmissionViewSet(
@@ -66 +155,32 @@ class FormSubmissionViewSet(
@@ -68 +188 @@ class FormSubmissionViewSet(
@@ -72 +192 @@ class FormSubmissionViewSet(
@@ -76 +196,7 @@ class FormSubmissionViewSet(
@@ -79 +205,13 @@ class FormSubmissionViewSet(
@@ -86 +224 @@ class FormSubmissionViewSet(
@@ -92 +230 @@ class FormSubmissionViewSet(
@@ -94,0 +233,1357 @@ class FormSubmissionViewSet(
```

### `care/emr/api/viewsets/location.py`

+78 / −24; 7 hunks; 578 current lines.

```diff
@@ -19 +19,4 @@ from care.emr.models.organization import FacilityOrganization, FacilityOrganizat
@@ -312,4 +315,8 @@ class FacilityLocationEncounterViewSet(EMRModelViewSet):
@@ -364,6 +371,19 @@ class FacilityLocationEncounterViewSet(EMRModelViewSet):
@@ -372,6 +392,20 @@ class FacilityLocationEncounterViewSet(EMRModelViewSet):
@@ -380,6 +414,22 @@ class FacilityLocationEncounterViewSet(EMRModelViewSet):
@@ -402,0 +453,4 @@ class FacilityLocationEncounterViewSet(EMRModelViewSet):
@@ -501 +555 @@ def close_related_location_from_encounter(instance):
```

### `care/emr/api/viewsets/medication_request.py`

+290 / −3; 9 hunks; 387 current lines.

```diff
@@ -1 +1 @@
@@ -2,0 +3 @@ from django_filters import rest_framework as filters
@@ -4 +5,4 @@ from rest_framework import filters as rest_framework_filters
@@ -12,0 +17 @@ from care.emr.registries.system_questionnaire.system_questionnaire import (
@@ -17,0 +23 @@ from care.emr.resources.medication.request.spec import (
@@ -24,0 +31,7 @@ from care.utils.shortcuts import get_object_or_404
@@ -61 +74,4 @@ class MedicationRequestViewSet(
@@ -77,0 +94,56 @@ class MedicationRequestViewSet(
@@ -98,0 +171,215 @@ class MedicationRequestViewSet(
```

### `care/emr/api/viewsets/patient.py`

+69 / −2; 8 hunks; 584 current lines.

```diff
@@ -0,0 +1,3 @@
@@ -5 +8 @@ from django.utils import timezone
@@ -8 +11 @@ from drf_spectacular.utils import extend_schema
@@ -20,0 +24 @@ from care.emr.resources.patient.spec import (
@@ -40,0 +45 @@ from care.utils.lock import ObjectLocked
@@ -42,0 +48,7 @@ from care.utils.shortcuts import get_object_or_404
@@ -46,0 +59 @@ class PatientFilters(FilterSet):
@@ -199,0 +213,54 @@ class PatientViewSet(EMRModelViewSet):
```

### `care/emr/api/viewsets/report/report_upload.py`

+29 / −2; 4 hunks; 208 current lines.

```diff
@@ -30,0 +31 @@ from care.utils.shortcuts import get_object_or_404
@@ -58 +59,3 @@ class GenerateReportRequest(BaseModel):
@@ -68 +71,13 @@ class ReportUploadViewSet(EMRRetrieveMixin, EMRListMixin, EMRBaseViewSet):
@@ -165,0 +181,12 @@ class ReportUploadViewSet(EMRRetrieveMixin, EMRListMixin, EMRBaseViewSet):
```

### `care/emr/api/viewsets/scheduling/booking.py`

+103 / −52; 9 hunks; 446 current lines.

```diff
@@ -38,0 +39 @@ from care.emr.resources.scheduling.slot.spec import (
@@ -54,0 +56 @@ from care.utils.shortcuts import get_object_or_404
@@ -89,0 +92 @@ class TokenBookingViewSet(
@@ -112,0 +116,20 @@ class TokenBookingViewSet(
@@ -182,2 +204,0 @@ class TokenBookingViewSet(
@@ -184,0 +206,21 @@ class TokenBookingViewSet(
@@ -291,2 +332,0 @@ class TokenBookingViewSet(
@@ -294,46 +334,7 @@ class TokenBookingViewSet(
@@ -341,2 +342,52 @@ class TokenBookingViewSet(
```

### `care/emr/api/viewsets/scheduling/schedule.py`

+36 / −4; 5 hunks; 390 current lines.

```diff
@@ -38,0 +39,3 @@ from care.utils.shortcuts import get_object_or_404
@@ -162 +165,9 @@ class ScheduleViewSet(EMRModelViewSet):
@@ -170 +181,10 @@ class ScheduleViewSet(EMRModelViewSet):
@@ -174,0 +195 @@ class ScheduleViewSet(EMRModelViewSet):
@@ -331,2 +352,13 @@ class AvailabilityViewSet(EMRCreateMixin, EMRDestroyMixin, EMRBaseViewSet):
```

### `care/emr/api/viewsets/scheduling/token.py`

+48 / −11; 10 hunks; 221 current lines.

```diff
@@ -7,0 +8 @@ from rest_framework.filters import OrderingFilter
@@ -24,0 +26,6 @@ from care.utils.shortcuts import get_object_or_404
@@ -80,0 +88,6 @@ class TokenViewSet(EMRModelViewSet):
@@ -94,0 +108,11 @@ class TokenViewSet(EMRModelViewSet):
@@ -115,6 +139,14 @@ class TokenViewSet(EMRModelViewSet):
@@ -136 +168 @@ class TokenViewSet(EMRModelViewSet):
@@ -170 +201,0 @@ class TokenViewSet(EMRModelViewSet):
@@ -172,2 +202,0 @@ class TokenViewSet(EMRModelViewSet):
@@ -174,0 +204,8 @@ class TokenViewSet(EMRModelViewSet):
@@ -176 +213 @@ class TokenViewSet(EMRModelViewSet):
```

### `care/emr/api/viewsets/user.py`

+8 / −2; 4 hunks; 299 current lines.

```diff
@@ -2,0 +3 @@ from django.db import IntegrityError, transaction
@@ -39,0 +41 @@ from care.utils.shortcuts import get_object_or_404
@@ -94 +96 @@ class UserFilter(filters.FilterSet):
@@ -168 +170,5 @@ class UserViewSet(EMRModelViewSet):
```

### `care/emr/api/viewsets/valueset.py`

+39 / −2; 3 hunks; 272 current lines.

```diff
@@ -18,0 +19,4 @@ from care.emr.resources.valueset.spec import ValueSetReadSpec, ValueSetSpec
@@ -38,0 +43,4 @@ class ValueSetViewSet(EMRModelViewSet):
@@ -74,2 +82,31 @@ class ValueSetViewSet(EMRModelViewSet):
```

### `care/emr/models/__init__.py`

+2 / −0; 1 hunks; 35 current lines.

```diff
@@ -24,0 +25,2 @@ from .questionnaire import *  # noqa F403
```

### `care/emr/models/condition.py`

+14 / −0; 1 hunks; 34 current lines.

```diff
@@ -20,0 +21,14 @@ class Condition(EMRBaseModel):
```

### `care/emr/models/encounter.py`

+16 / −0; 2 hunks; 101 current lines.

```diff
@@ -9,0 +10,3 @@ from care.emr.resources.patient_identifier.default_expression_evaluator import (
@@ -42,0 +46,13 @@ class Encounter(EMRBaseModel):
```

### `care/emr/models/medication_request.py`

+27 / −0; 3 hunks; 91 current lines.

```diff
@@ -7,0 +8,2 @@ from care.emr.models.base import EMRBaseModel
@@ -31,0 +34,2 @@ class MedicationRequest(EMRBaseModel):
@@ -64,0 +69,23 @@ class MedicationRequest(EMRBaseModel):
```

### `care/emr/models/questionnaire.py`

+122 / −1; 5 hunks; 314 current lines.

```diff
@@ -3,0 +4 @@ from django.contrib.postgres.fields import ArrayField
@@ -10,0 +12,2 @@ MAX_QUESTIONNAIRE_TAGS_COUNT = 1000
@@ -33 +36 @@ class QuestionnaireTag(EMRBaseModel):
@@ -83,0 +87,2 @@ class FormSubmission(EMRBaseModel):
@@ -90,0 +96,116 @@ class FormSubmission(EMRBaseModel):
```

### `care/emr/models/report/report_upload.py`

+93 / −1; 6 hunks; 148 current lines.

```diff
@@ -3,0 +4 @@ from uuid import uuid4
@@ -11,0 +13,3 @@ from care.utils.models.validators import parse_file_extension
@@ -14 +18,5 @@ class ReportUpload(EMRBaseModel):
@@ -21,0 +30,34 @@ class ReportUpload(EMRBaseModel):
@@ -35,0 +78,42 @@ class ReportUpload(EMRBaseModel):
@@ -50,0 +135,8 @@ class ReportUpload(EMRBaseModel):
```

### `care/emr/models/report/template.py`

+23 / −0; 1 hunks; 44 current lines.

```diff
@@ -21,0 +22,23 @@ class Template(SlugBaseModel):
```

### `care/emr/registries/system_questionnaire/system_questionnaire.py`

+26 / −1; 3 hunks; 61 current lines.

```diff
@@ -2,0 +3,2 @@ import uuid
@@ -4,0 +7,11 @@ from care.emr.resources.questionnaire.spec import QuestionnaireStatus
@@ -36 +49,13 @@ class InternalQuestionnaireRegistry:
```

### `care/emr/resources/condition/spec.py`

+8 / −0; 4 hunks; 158 current lines.

```diff
@@ -47,0 +48,5 @@ class SeverityChoices(str, Enum):
@@ -87,0 +93 @@ class ConditionSpec(BaseConditionSpec):
@@ -114,0 +121 @@ class ConditionReadSpec(BaseConditionSpec):
@@ -142,0 +150 @@ class ConditionUpdateSpec(BaseConditionSpec):
```

### `care/emr/resources/encounter/constants.py`

+9 / −0; 1 hunks; 97 current lines.

```diff
@@ -22,0 +23,9 @@ COMPLETED_CHOICES = [
```

### `care/emr/resources/encounter/spec.py`

+3 / −2; 3 hunks; 219 current lines.

```diff
@@ -8 +8 @@ from care.emr.extensions.base import ExtensionResource
@@ -120 +120 @@ class EncounterUpdateSpec(ExtensionValidator, EncounterSpecBase):
@@ -154,0 +155 @@ class EncounterListSpec(EncounterSpecBase):
```

### `care/emr/resources/form_submission/spec.py`

+50 / −8; 8 hunks; 106 current lines.

```diff
@@ -4 +4 @@ from enum import Enum
@@ -28,3 +28 @@ class BaseFormSubmissionSpec(EMRResource):
@@ -35 +33,9 @@ class FormSubmissionUpdateSpec(BaseFormSubmissionSpec):
@@ -42,0 +49,5 @@ class FormSubmissionWriteSpec(FormSubmissionUpdateSpec):
@@ -46,2 +57,5 @@ class FormSubmissionWriteSpec(FormSubmissionUpdateSpec):
@@ -50 +64 @@ class FormSubmissionWriteSpec(FormSubmissionUpdateSpec):
@@ -56,0 +71,14 @@ class FormSubmissionReadSpec(FormSubmissionUpdateSpec):
@@ -63,0 +92,14 @@ class FormSubmissionReadSpec(FormSubmissionUpdateSpec):
```

### `care/emr/resources/medication/request/spec.py`

+26 / −21; 2 hunks; 329 current lines.

```diff
@@ -209,0 +210,23 @@ class CreatePrescription(BaseModel):
@@ -264,21 +287,3 @@ class MedicationRequestSpec(BaseMedicationRequestSpec):
```

### `care/emr/resources/patient/spec.py`

+17 / −0; 1 hunks; 312 current lines.

```diff
@@ -234,0 +235,17 @@ class PatientListSpec(ExtensionListRenderer, PatientBaseSpec):
```

### `care/emr/resources/report/report_upload/spec.py`

+18 / −2; 4 hunks; 70 current lines.

```diff
@@ -20 +20 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
@@ -30 +30 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
@@ -31,0 +32,8 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
@@ -39,0 +48,8 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
```

### `care/emr/resources/report/template/spec.py`

+2 / −0; 1 hunks; 121 current lines.

```diff
@@ -101,0 +102,2 @@ class TemplateReadSpec(TemplateBaseSpec):
```

### `care/emr/resources/scheduling/schedule/spec.py`

+2 / −2; 1 hunks; 258 current lines.

```diff
@@ -254,2 +254,2 @@ def has_overlapping_availability(availabilities: list[AvailabilityDateTimeSpec])
```

### `care/emr/utils/mfa.py`

+2 / −1; 2 hunks; 62 current lines.

```diff
@@ -12,0 +13 @@ from care.users.models import User
@@ -49 +50 @@ def create_auth_response(user: User) -> Response:
```

### `care/security/authorization/patient.py`

+9 / −1; 2 hunks; 139 current lines.

```diff
@@ -6 +6 @@ from care.emr.models.organization import FacilityOrganizationUser, OrganizationU
@@ -32,0 +33,8 @@ class PatientAccess(AuthorizationHandler):
```

### `config/auth_views.py`

+2 / −1; 2 hunks; 236 current lines.

```diff
@@ -17,0 +18 @@ from rest_framework_simplejwt.views import TokenVerifyView, TokenViewBase
@@ -134 +135 @@ class TokenObtainPairSerializer(TokenObtainSerializer):
```

### `config/settings/base.py`

+50 / −4; 8 hunks; 758 current lines.

```diff
@@ -21 +21 @@ from plug_config import manager
@@ -38,0 +39,3 @@ if READ_DOT_ENV_FILE := env.bool("DJANGO_READ_DOT_ENV_FILE", default=False):
@@ -41 +44 @@ SECRET_KEY = env(
@@ -46,0 +50,23 @@ DEBUG = env.bool("DJANGO_DEBUG", False)
@@ -51 +77 @@ DEBUG = env.bool("DJANGO_DEBUG", False)
@@ -419 +445 @@ if USE_TZ:
@@ -471,0 +498,16 @@ AUDIT_LOG_ENABLED = env.bool("AUDIT_LOG_ENABLED", default=False)
@@ -712,0 +755,4 @@ FILE_UPLOAD_EXPIRY_HOURS = env.int("FILE_UPLOAD_EXPIRY_HOURS", default=24)
```

### `config/settings/config.py`

+62 / −1; 2 hunks; 386 current lines.

```diff
@@ -288,0 +289,5 @@ PATIENT_GLOBAL_EDIT_ACCESS_ENABLED = env.bool(
@@ -319 +324,57 @@ PREFERENCE_SCHEMA = env.json(
```

### `config/settings/local.py`

+13 / −0; 1 hunks; 78 current lines.

```diff
@@ -49,0 +50,13 @@ DISABLE_RATELIMIT = True
```

### `config/settings/test.py`

+11 / −0; 2 hunks; 124 current lines.

```diff
@@ -40,0 +41,2 @@ DATABASES = {"default": env.db("DATABASE_URL", default="postgres:///care-test")}
@@ -109,0 +112,9 @@ DISABLE_RATELIMIT = True
```

### `config/urls.py`

+7 / −0; 2 hunks; 119 current lines.

```diff
@@ -0,0 +1,2 @@
@@ -112,0 +115,5 @@ for plug in settings.PLUGIN_APPS:
```

### `plug_config.py`

+13 / −1; 1 hunks; 18 current lines.

```diff
@@ -6 +6,13 @@ plugs = []
```

## Other fork differences: complete non-plugin inventory

Historical migrations, tests, documentation and deployment files are not counted as native production Python. Historical custom tests still under native test directories are test-ownership debt, not production implementations. Environment values are deliberately omitted. No generated runtime artifact is included in this audit commit.

| Path | + / − | Classification |
|---|---:|---|
| `.dockerignore` | 8 / 0 | D: deployment/build/configuration |
| `.gitignore` | 3 / 0 | D: deployment/build/configuration |
| `CLAUDE.md` | 21 / 0 | D: documentation |
| `care/emr/migrations/0078_medicationrequest_idempotency.py` | 45 / 0 | D: historical immutable migration |
| `care/emr/migrations/0079_formsubmission_versioned_workflow.py` | 283 / 0 | D: historical immutable migration |
| `care/emr/migrations/0080_form_submission_artifact.py` | 233 / 0 | D: historical immutable migration |
| `care/emr/migrations/0081_correspondence_compilation.py` | 299 / 0 | D: historical immutable migration |
| `care/emr/migrations/0082_correspondencerecipient_correspondencereview_and_more.py` | 394 / 0 | D: historical immutable migration |
| `care/emr/migrations/0083_correspondencerecipient_kind_constraint.py` | 17 / 0 | D: historical immutable migration |
| `care/emr/migrations/0084_correspondence_letter_workflow.py` | 470 / 0 | D: historical immutable migration |
| `care/emr/migrations/0085_correspondence_delivery_ledger.py` | 769 / 0 | D: historical immutable migration |
| `care/emr/migrations/0086_correspondence_source_correction.py` | 781 / 0 | D: historical immutable migration |
| `care/emr/migrations/0087_correspondence_continuity.py` | 705 / 0 | D: historical immutable migration |
| `care/emr/migrations/0088_correspondence_replacement_workflow.py` | 811 / 0 | D: historical immutable migration |
| `care/emr/migrations/0089_consult_closure_workflow.py` | 144 / 0 | D: historical immutable migration |
| `care/emr/migrations/0090_consult_closure_recovery_resolution.py` | 48 / 0 | D: historical immutable migration |
| `care/emr/migrations/0091_form_submission_entered_in_error_audit.py` | 93 / 0 | D: historical immutable migration |
| `care/emr/migrations/0092_clinical_text_resource.py` | 107 / 0 | D: historical immutable migration |
| `care/emr/migrations/0093_diagnosis_native_problem_list.py` | 37 / 0 | D: historical immutable migration |
| `care/emr/migrations/0094_clinical_term_translation.py` | 186 / 0 | D: historical immutable migration |
| `care/emr/migrations/0095_clinical_term_concept_kind.py` | 27 / 0 | D: historical immutable migration |
| `care/emr/migrations/0096_encounter_one_active_inpatient.py` | 49 / 0 | D: historical immutable migration |
| `care/emr/migrations/0097_correspondence_recipient_command.py` | 104 / 0 | D: historical immutable migration |
| `care/emr/migrations/0098_form_submission_create_draft_command.py` | 30 / 0 | D: historical immutable migration |
| `care/emr/migrations/0099_encounter_discharge_command.py` | 118 / 0 | D: historical immutable migration |
| `care/emr/migrations/0100_admission_documentation.py` | 55 / 0 | D: historical immutable migration |
| `care/emr/migrations/0101_emergency_admission.py` | 50 / 0 | D: historical immutable migration |
| `care/emr/migrations/0102_emergency_consult_closure.py` | 77 / 0 | D: historical immutable migration |
| `care/emr/migrations/0103_operation_plan.py` | 74 / 0 | D: historical immutable migration |
| `care/emr/migrations/0104_unscheduled_consult_closure.py` | 57 / 0 | D: historical immutable migration |
| `care/emr/migrations/0105_consult_closure_optional_token.py` | 64 / 0 | D: historical immutable migration |
| `care/emr/migrations/0106_form_submission_lab.py` | 91 / 0 | D: historical immutable migration |
| `care/emr/migrations/0107_letter_artifact_link_on_revision.py` | 100 / 0 | D: historical immutable migration |
| `care/emr/migrations/0108_move_models_to_care_suriname.py` | 1450 / 0 | D: historical immutable migration |
| `care/emr/tests/test_admission_documentation.py` | 96 / 0 | D: test/fixture |
| `care/emr/tests/test_clinical_no_store.py` | 78 / 0 | D: test/fixture |
| `care/emr/tests/test_clinical_term_translation.py` | 469 / 0 | D: test/fixture |
| `care/emr/tests/test_clinical_text_resource.py` | 141 / 0 | D: test/fixture |
| `care/emr/tests/test_clinical_workflow_readiness_command.py` | 161 / 0 | D: test/fixture |
| `care/emr/tests/test_consult_closure.py` | 1050 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_body.py` | 25 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_compilation.py` | 1350 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_continuity.py` | 1191 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_continuity_migration.py` | 185 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_correction_migration.py` | 304 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_delivery.py` | 1179 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_letter.py` | 1087 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_replacement_spec.py` | 183 / 0 | D: test/fixture |
| `care/emr/tests/test_correspondence_review.py` | 805 / 0 | D: test/fixture |
| `care/emr/tests/test_device_api.py` | 21 / 13 | D: test/fixture |
| `care/emr/tests/test_diagnosis_idempotent_api.py` | 154 / 0 | D: test/fixture |
| `care/emr/tests/test_discharge_documentation.py` | 202 / 0 | D: test/fixture |
| `care/emr/tests/test_doctor_activation.py` | 150 / 0 | D: test/fixture |
| `care/emr/tests/test_emergency_admission.py` | 124 / 0 | D: test/fixture |
| `care/emr/tests/test_encounter_admission_note_command.py` | 231 / 0 | D: test/fixture |
| `care/emr/tests/test_encounter_admission_note_extension.py` | 121 / 0 | D: test/fixture |
| `care/emr/tests/test_encounter_api.py` | 61 / 4 | D: test/fixture |
| `care/emr/tests/test_encounter_clinical_closure.py` | 232 / 0 | D: test/fixture |
| `care/emr/tests/test_encounter_discharge.py` | 333 / 0 | D: test/fixture |
| `care/emr/tests/test_encounter_discharge_concurrency.py` | 94 / 0 | D: test/fixture |
| `care/emr/tests/test_form_submission_api.py` | 307 / 14 | D: test/fixture |
| `care/emr/tests/test_form_submission_artifact.py` | 1008 / 0 | D: test/fixture |
| `care/emr/tests/test_form_submission_workflow.py` | 1516 / 0 | D: test/fixture |
| `care/emr/tests/test_location_api.py` | 23 / 16 | D: test/fixture |
| `care/emr/tests/test_medication_request_idempotency.py` | 627 / 0 | D: test/fixture |
| `care/emr/tests/test_operation_plan.py` | 216 / 0 | D: test/fixture |
| `care/emr/tests/test_patient_api.py` | 184 / 0 | D: test/fixture |
| `care/emr/tests/test_production_gate.py` | 78 / 0 | D: test/fixture |
| `care/emr/tests/test_provision_urology_operations_questionnaire.py` | 44 / 0 | D: test/fixture |
| `care/emr/tests/test_provision_urology_turp_clinical_text.py` | 108 / 0 | D: test/fixture |
| `care/emr/tests/test_schedule_api.py` | 352 / 2 | D: test/fixture |
| `care/emr/tests/test_urology_operation_response.py` | 75 / 0 | D: test/fixture |
| `care/emr/tests/test_user_api.py` | 94 / 0 | D: test/fixture |
| `care/emr/tests/test_workflow_capabilities.py` | 123 / 0 | D: test/fixture |
| `care/security/authorization/PATIENT_DEPARTMENT_ACCESS.md` | 36 / 0 | D: documentation |
| `care/security/tests/__init__.py` | 0 / 0 | D: test/fixture |
| `care/security/tests/test_patient_department_access.py` | 78 / 0 | D: test/fixture |
| `care/users/migrations/0028_draftrecoverykey.py` | 43 / 0 | D: historical immutable migration |
| `care/users/migrations/0029_move_draft_recovery_to_care_suriname.py` | 14 / 0 | D: historical immutable migration |
| `care/users/tests/__init__.py` | 0 / 0 | D: test/fixture |
| `care/utils/tests/base.py` | 1 / 1 | D: test/fixture |
| `care/utils/tests/test_celery_dev_settings.py` | 17 / 0 | D: test/fixture |
| `config/settings/AZP_CLOSURE_POLICY.md` | 29 / 0 | D: documentation |
| `config/settings/tests/__init__.py` | 1 / 0 | D: test/fixture |
| `config/settings/tests/test_azp_closure_policy.py` | 32 / 0 | D: test/fixture |
| `deploy/.env.example` | 65 / 0 | D: deployment/build/configuration |
| `deploy/.gitignore` | 2 / 0 | D: deployment/build/configuration |
| `deploy/Caddyfile` | 31 / 0 | D: deployment/build/configuration |
| `deploy/README.md` | 105 / 0 | D: documentation |
| `deploy/docker-compose.yml` | 159 / 0 | D: deployment/build/configuration |
| `deploy/install-server-backup-timer.sh` | 11 / 0 | D: deployment/build/configuration |
| `deploy/local-test/Caddyfile` | 16 / 0 | D: deployment/build/configuration |
| `deploy/local-test/README.md` | 13 / 0 | D: documentation |
| `deploy/local-test/compose.override.yml` | 14 / 0 | D: deployment/build/configuration |
| `deploy/make-env.sh` | 46 / 0 | D: deployment/build/configuration |
| `deploy/server-backup.sh` | 86 / 0 | D: deployment/build/configuration |
| `deploy/ship-frontend.sh` | 69 / 0 | D: deployment/build/configuration |
| `deploy/systemd/care-suriname-server-backup.service` | 9 / 0 | D: deployment/build/configuration |
| `deploy/systemd/care-suriname-server-backup.timer` | 12 / 0 | D: deployment/build/configuration |
| `deploy/update.sh` | 63 / 0 | D: deployment/build/configuration |
| `docker-compose.local.yaml` | 3 / 0 | D: deployment/build/configuration |
| `docker-compose.test.yaml` | 130 / 0 | D: deployment/build/configuration |
| `docker/.local.env` | 17 / 0 | D: deployment/build/configuration |
| `docker/.test.env` | 31 / 0 | D: deployment/build/configuration |
| `docker/prod.Dockerfile` | 8 / 1 | D: deployment/build/configuration; generic ARG/COPY portability upstream candidate |
| `docs/development/2026-09-19-draft-recovery-ownership.md` | 247 / 0 | D: documentation |
| `docs/development/2026-09-19-note-lab-extraction.md` | 188 / 0 | D: documentation |
| `docs/development/correspondence-letter-pdf-core-patch.md` | 161 / 0 | D: documentation |
| `docs/development/encounter-admission-note-extension-core-patch.md` | 122 / 0 | D: documentation |
| `docs/development/encounter-clinical-closure-core-patch.md` | 169 / 0 | D: documentation |
| `docs/development/encounter-discharge-core-patch.md` | 168 / 0 | D: documentation |
| `docs/development/patient-directory-pagination-core-patch.md` | 39 / 0 | D: documentation |
| `docs/development/plug-app.md` | 302 / 0 | D: documentation |
| `docs/development/schedule-overlap-core-patch.md` | 93 / 0 | D: documentation |
| `docs/development/urology-recent-patients-preference-core-patch.md` | 47 / 0 | D: documentation |
| `scripts/care-suriname-backup.sh` | 168 / 0 | D: deployment/build/configuration |
| `scripts/celery-dev.sh` | 2 / 0 | D: deployment/build/configuration |
| `scripts/install-backup-timer.sh` | 65 / 0 | D: deployment/build/configuration |
| `scripts/phase2/rehearse-migration.sh` | 52 / 0 | D: deployment/build/configuration |
| `scripts/phase2/rehearse-step2.sh` | 62 / 0 | D: deployment/build/configuration |
| `scripts/phase2/verify_state.py` | 304 / 0 | D: deployment/build/configuration |
| `scripts/systemd/care-suriname-backup.service` | 18 / 0 | D: deployment/build/configuration |
| `scripts/systemd/care-suriname-backup.timer` | 16 / 0 | D: deployment/build/configuration |

## Exact native plugin imports

AST `Import` / `ImportFrom` scan of care/config production Python (tests and migrations excluded). Dynamic registration via plug_config is separately documented. This is a static import inventory, not a proof against arbitrary dynamically constructed import strings.

| Native file:line | Plugin module |
|---|---|
| `care/emr/api/viewsets/condition.py:29` | `care_suriname.resources.condition_idempotency` |
| `care/emr/api/viewsets/encounter.py:63` | `care_suriname.api.viewsets.admission_documentation` |
| `care/emr/api/viewsets/encounter.py:66` | `care_suriname.api.viewsets.emergency_admission` |
| `care/emr/api/viewsets/encounter.py:67` | `care_suriname.models.consult_closure` |
| `care/emr/api/viewsets/form_submission.py:45` | `care_suriname.api.viewsets.clinical_no_store` |
| `care/emr/api/viewsets/form_submission.py:46` | `care_suriname.correspondence.correction` |
| `care/emr/api/viewsets/form_submission.py:52` | `care_suriname.models.correspondence_correction` |
| `care/emr/api/viewsets/form_submission.py:57` | `care_suriname.models.form_submission_artifact_command` |
| `care/emr/api/viewsets/form_submission.py:60` | `care_suriname.models.form_submission_command` |
| `care/emr/api/viewsets/form_submission.py:63` | `care_suriname.reports.form_submission_artifact` |
| `care/emr/api/viewsets/form_submission.py:69` | `care_suriname.resources.form_submission.artifact` |
| `care/emr/api/viewsets/form_submission.py:75` | `care_suriname.resources.form_submission.commands` |
| `care/emr/api/viewsets/form_submission.py:86` | `care_suriname.resources.form_submission.note_labs` |
| `care/emr/api/viewsets/form_submission.py:87` | `care_suriname.resources.form_submission.structured_actions` |
| `care/emr/api/viewsets/form_submission.py:91` | `care_suriname.resources.form_submission.urology_operation` |
| `care/emr/api/viewsets/form_submission.py:96` | `care_suriname.workflow_capabilities` |
| `care/emr/api/viewsets/form_submission.py:780` | `care_suriname.resources.scheduling.operation_plan` |
| `care/emr/api/viewsets/medication_request.py:31` | `care_suriname.api.viewsets.clinical_no_store` |
| `care/emr/api/viewsets/medication_request.py:32` | `care_suriname.resources.medication_request_idempotency` |
| `care/emr/api/viewsets/medication_request.py:37` | `care_suriname.workflow_capabilities` |
| `care/emr/api/viewsets/report/report_upload.py:31` | `care_suriname.api.viewsets.clinical_no_store` |
| `care/emr/api/viewsets/scheduling/booking.py:56` | `care_suriname.api.viewsets.operation_plan` |
| `care/emr/api/viewsets/scheduling/schedule.py:39` | `care_suriname.resources.scheduling.conflicts` |
| `care/emr/api/viewsets/user.py:41` | `care_suriname.api.viewsets.doctor_activation` |
| `care/emr/api/viewsets/valueset.py:19` | `care_suriname.resources.clinical_term_translation` |
| `care/emr/models/report/template.py:26` | `care_suriname.reports.template_versioning` |
| `care/emr/utils/mfa.py:13` | `care_suriname.draft_recovery.auth` |
| `config/auth_views.py:18` | `care_suriname.draft_recovery.auth` |

# Post-extraction backend ownership: exact hunk inventory

> **SUPERSEDED HISTORICAL AUDIT — not the current ownership decision.**
> Final source `0ed1b6e10ac1a7692f6114cc8647306ce2a43d9d` reached 100% source
> ownership under the documented exception definition. Use the
> [final patient-access closure report](2026-09-19-patient-access-ownership.md) and
> [current complete hunk inventory](2026-09-19-ownership-closure-hunks.md).
> Remaining-work statements and counts below apply only to the audited revision.

Pinned fork `ece71a878b3764a476d713a163a2f5515db57581` → audited HEAD `347517ddb34fc1a8170885e4b6b12af3f381114a` (19 September 2026).
This is the fresh inventory after all eight extraction groups. The earlier
[inventory](2026-09-19-final-backend-separation-hunks.md) remains historical.
See the [decision](2026-09-19-post-extraction-ownership-audit.md): full source
ownership is still incomplete because one custom department read policy remains.

A: native safety/table contract; B: plugin integration; C: generic upstream
candidate; D: configuration/non-production/history; E: possible redundancy;
F: embedded custom implementation. Categories overlap and are not percentages.

Native non-test care/config Python: 37 files, 174 hunks,
+1320/−249. Test settings, root wiring and three generic
plugs modules appear separately in the same table. No environment values shown.

## Source classification

| Path | + / − | Categories | Intent/disposition |
|---|---:|---|---|
| `care/audit_log/helpers.py` | 6 / 0 | A/C/E | Domain-ledger audit exclusion before normal filters. Existing AUDIT_LOG.models.exclude.models supports equivalent scopes; candidate redundant branch only after configuration/secret-exclusion parity tests. |
| `care/emr/api/viewsets/condition.py` | 15 / 8 | A/C | Generic additive action registration; native update/retrieve authorization delta remains. Diagnosis command implementation is plugin-owned. |
| `care/emr/api/viewsets/device.py` | 37 / 26 | A/C | Atomic encounter/device association locks; closed-encounter veto; disassociation end handling. Keep native write protection; upstream transaction/transition hook candidate. |
| `care/emr/api/viewsets/encounter.py` | 140 / 28 | A/B | Closed encounter filters, locked update/restart/booking and active-inpatient uniqueness; plugin admission mixins and ConsultClosure restart veto. New mixin actions may be remounted; native legacy-write guards cannot simply disappear. |
| `care/emr/api/viewsets/form_submission.py` | 186 / 16 | A/B/C | Generic six-action contribution; native draft-only CRUD, read/write authorization, version/immutability guards and locks; plugin no-store integration. No command/ledger/artifact/Urology engine remains. |
| `care/emr/api/viewsets/location.py` | 78 / 24 | A/C | Lock encounter/location/association before validation and mutation; closed encounter veto and discharge closing. Native safety patch, upstream atomicity candidate. |
| `care/emr/api/viewsets/medication_request.py` | 68 / 3 | A/B/C | Generic two-action registration; native CRUD encounter locks, authorization and closed-write veto; plugin no-store. Command/prescription orchestration is plugin-owned; native shared prescription helper remains. |
| `care/emr/api/viewsets/patient.py` | 2 / 1 | C | Exact date-of-birth filter only. Directory endpoint/DTO/pagination are plugin-owned; native delete is unchanged. |
| `care/emr/api/viewsets/report/report_upload.py` | 29 / 2 | A/B/C | Clinical no-store, provenance eager loading, generated artifact archive refusal. Generic caching/provenance candidates; native archive veto remains required. |
| `care/emr/api/viewsets/scheduling/booking.py` | 103 / 52 | A/B/C | Plugin OperationPlanMixin plus terminal-state immutability and authoritative booking/slot/queue locks. Custom mixin routes may be remounted; retain native safety checks. |
| `care/emr/api/viewsets/scheduling/schedule.py` | 36 / 4 | A/B/C | Resource locks and plugin overlap validation across schedules, atomic update/destroy. Generic overlap validation/transaction hook candidate; requires all native write paths. |
| `care/emr/api/viewsets/scheduling/token.py` | 48 / 11 | A/C | Terminal token immutability, row locks and queue serialization; authorize_destroy recursion fix. Recursion fix already in cached origin/develop; merge instead of duplicate PR. |
| `care/emr/api/viewsets/user.py` | 8 / 2 | A/B/C | Plugin doctor activation action; ProtectedError hard-delete fallback to soft delete. Action potentially plugin-mounted; deletion fallback generic but must preserve caller expectations. |
| `care/emr/api/viewsets/valueset.py` | 6 / 1 | C | Optional generic valueset expansion callback; no slug/locale/translation/dedup policy in native viewset. |
| `care/emr/models/__init__.py` | 2 / 0 | C | Discover native ReportUpload and Template models. Native registration, not custom model ownership. |
| `care/emr/models/condition.py` | 14 / 0 | A | Native table clinical_domain default/column, client UUID/hash and uniqueness. Vocabulary validation is a plugin contribution; no schema change. |
| `care/emr/models/encounter.py` | 16 / 0 | A/C | Partial unique constraint for one active inpatient encounter per patient. Native table invariant. |
| `care/emr/models/medication_request.py` | 27 / 0 | A | Idempotency UUID/hash pair and uniqueness constraints on native medication rows. Native table invariant. |
| `care/emr/models/questionnaire.py` | 121 / 1 | A | Native FormSubmission series/version/provenance/entered-error columns, constraints and immutable-save guard. Constant retained here names a constraint on this native table; plugin command-only constant is gone. |
| `care/emr/models/report/report_upload.py` | 92 / 1 | A | Native ReportUpload provenance columns, source-version constraint, optional template and immutable-save guard. Plugin ledger-only constant is gone; no artifact engine here. |
| `care/emr/models/report/template.py` | 23 / 0 | A/B/C | Native resource version/hash maintained on save through plugin helper. Generic versioned-template hashing hook or upstream implementation candidate. |
| `care/emr/registries/system_questionnaire/system_questionnaire.py` | 26 / 1 | C | Builtin structured-resource model fallback and get_resource_model for structured action cloning. Generic registry capability suitable for upstream review. |
| `care/emr/resources/condition/spec.py` | 7 / 0 | A/C | Native column/API field plus generic clinical-domain type contribution with str fallback. general default preserves native table contract; Urology vocabulary is plugin-owned. |
| `care/emr/resources/encounter/constants.py` | 9 / 0 | A | Shared discharged/completed clinically-closed set prevents administrative status interpretation from reopening clinical writes. |
| `care/emr/resources/encounter/spec.py` | 3 / 2 | C | ExtensionListRenderer and super() include registered extensions in list/read rendering. Generic extension correctness fix. |
| `care/emr/resources/form_submission/spec.py` | 50 / 8 | A/C | Versioned draft/update/read contract, extra-field rejection and patient/encounter consistency; native resource remains native. Shared native model contract is not an independently movable custom model. |
| `care/emr/resources/medication/request/spec.py` | 26 / 21 | C | Extract existing prescription construction into reusable resolve_created_prescription; no copied native model. Generic reuse refactor. |
| `care/emr/resources/report/report_upload/spec.py` | 18 / 2 | A/C | Expose native generated-artifact provenance and optional template/uploader fields; keep aligned with native table schema. |
| `care/emr/resources/report/template/spec.py` | 2 / 0 | A/C | Expose native template resource_version/content_hash used by immutable artifact provenance. |
| `care/emr/resources/scheduling/schedule/spec.py` | 2 / 2 | C | Strict interval overlap allows adjacent intervals. Generic validation fix. |
| `care/emr/utils/mfa.py` | 2 / 1 | B | Successful interactive MFA obtains plugin authentication-proof token. Missing common post-auth claim hook; removing it breaks encrypted draft recovery recency proof. |
| `care/security/authorization/patient.py` | 9 / 1 | B/F | Residual custom completed-encounter department role lookup (lines 33-40): read-access policy, not write-time safety. Requires narrow role-contribution hook used by both direct PatientAccess and controller consumers before moving policy. |
| `config/auth_views.py` | 2 / 1 | B | Successful password login obtains plugin authentication-proof token; same missing generic hook as MFA. |
| `config/settings/base.py` | 48 / 4 | C/D | Generic settings contribution; deployment/fail-closed workflow gates, time zone, audit exclusions and draft-recovery configuration. Department-to-required-form policy moved; no settings values reproduced. |
| `config/settings/config.py` | 35 / 26 | C/D | Generic preference-schema contribution; department-access feature switch remains. Urology recent-patient schema/defaults/bounds are plugin-owned. |
| `config/settings/local.py` | 9 / 0 | C/D | Local deterministic workflow/delivery configuration and generic plugin settings contribution. |
| `config/settings/test.py` | 9 / 0 | C/D | Test DB-name override, deterministic workflow/delivery gates and generic settings contribution; non-production. |
| `config/urls.py` | 15 / 0 | C | Generic optional plug v1 mount plus collision-checked literal-priority path registration; no specialty URL hardcoding. |
| `plug_config.py` | 13 / 1 | D | LocalPlugManager registers care_suriname through get_apps despite plugs=[]; local integration wiring, not an upstream core model/service. |
| `plugs/contributions.py` | 71 / 0 | C | Generic lazy single-provider, collision-checked mapping and validated settings contribution registry; no specialty policy. |
| `plugs/urls.py` | 119 / 0 | C | Generic literal explicit-route priority with exact/parameter collision safeguards and format alias handling. |
| `plugs/viewset_actions.py` | 89 / 0 | C | Generic optional additive plain-class/tuple action and private-helper contributions; host/name/path collisions fail closed. |

## Exact unified-zero hunks

### `care/audit_log/helpers.py`

+6/−0; 1 hunks; 120 source lines.

```diff
@@ -86,0 +87,6 @@ def exclude_model(model_name):
```

### `care/emr/api/viewsets/condition.py`

+15/−8; 4 hunks; 160 source lines.

```diff
@@ -22,0 +23 @@ from care.utils.shortcuts import get_object_or_404
@@ -102,0 +104 @@ InternalQuestionnaireRegistry.register(SymptomViewSet)
@@ -138,8 +140 @@ class DiagnosisViewSet(
@@ -151,0 +147,12 @@ class DiagnosisViewSet(
```

### `care/emr/api/viewsets/device.py`

+37/−26; 6 hunks; 478 source lines.

```diff
@@ -43 +43 @@ from care.emr.resources.device.spec import (
@@ -180,4 +179,0 @@ class DeviceViewSet(EMRModelViewSet):
@@ -185,18 +181 @@ class DeviceViewSet(EMRModelViewSet):
@@ -203,0 +183,32 @@ class DeviceViewSet(EMRModelViewSet):
@@ -455,2 +466,2 @@ class DeviceServiceHistoryViewSet(
@@ -467 +478 @@ def disassociate_device_from_encounter(instance):
```

### `care/emr/api/viewsets/encounter.py`

+140/−28; 17 hunks; 549 source lines.

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

+186/−16; 22 hunks; 264 source lines.

```diff
@@ -0,0 +1,2 @@
@@ -1,0 +4 @@ from django_filters import rest_framework as filters
@@ -2,0 +6 @@ from rest_framework.exceptions import PermissionDenied, ValidationError
@@ -13 +17,4 @@ from care.emr.models.patient import Patient
@@ -15,0 +23 @@ from care.emr.resources.form_submission.spec import (
@@ -22,0 +31,2 @@ from care.utils.shortcuts import get_object_or_404
@@ -33,0 +44 @@ class FormSubmissionFilters(filters.FilterSet):
@@ -34,0 +46 @@ class FormSubmissionViewSet(
@@ -47,0 +60,6 @@ class FormSubmissionViewSet(
@@ -49 +67,2 @@ class FormSubmissionViewSet(
@@ -51,3 +70,7 @@ class FormSubmissionViewSet(
@@ -55 +78 @@ class FormSubmissionViewSet(
@@ -58,0 +82 @@ class FormSubmissionViewSet(
@@ -60,3 +84,3 @@ class FormSubmissionViewSet(
@@ -66 +90 @@ class FormSubmissionViewSet(
@@ -68 +92,32 @@ class FormSubmissionViewSet(
@@ -72 +127 @@ class FormSubmissionViewSet(
@@ -76 +131,7 @@ class FormSubmissionViewSet(
@@ -79 +140,13 @@ class FormSubmissionViewSet(
@@ -86 +159 @@ class FormSubmissionViewSet(
@@ -92 +165 @@ class FormSubmissionViewSet(
@@ -94,0 +168,97 @@ class FormSubmissionViewSet(
```

### `care/emr/api/viewsets/location.py`

+78/−24; 7 hunks; 578 source lines.

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

+68/−3; 7 hunks; 165 source lines.

```diff
@@ -1 +1 @@
@@ -4 +4,3 @@ from rest_framework import filters as rest_framework_filters
@@ -12,0 +15 @@ from care.emr.registries.system_questionnaire.system_questionnaire import (
@@ -24,0 +28,2 @@ from care.utils.shortcuts import get_object_or_404
@@ -59,0 +65 @@ class MedicationRequestFilter(filters.FilterSet):
@@ -61 +67,4 @@ class MedicationRequestViewSet(
@@ -77,0 +87,56 @@ class MedicationRequestViewSet(
```

### `care/emr/api/viewsets/patient.py`

+2/−1; 2 hunks; 518 source lines.

```diff
@@ -5 +5 @@ from django.utils import timezone
@@ -46,0 +47 @@ class PatientFilters(FilterSet):
```

### `care/emr/api/viewsets/report/report_upload.py`

+29/−2; 4 hunks; 208 source lines.

```diff
@@ -30,0 +31 @@ from care.utils.shortcuts import get_object_or_404
@@ -58 +59,3 @@ class GenerateReportRequest(BaseModel):
@@ -68 +71,13 @@ class ReportUploadViewSet(EMRRetrieveMixin, EMRListMixin, EMRBaseViewSet):
@@ -165,0 +181,12 @@ class ReportUploadViewSet(EMRRetrieveMixin, EMRListMixin, EMRBaseViewSet):
```

### `care/emr/api/viewsets/scheduling/booking.py`

+103/−52; 9 hunks; 446 source lines.

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

+36/−4; 5 hunks; 390 source lines.

```diff
@@ -38,0 +39,3 @@ from care.utils.shortcuts import get_object_or_404
@@ -162 +165,9 @@ class ScheduleViewSet(EMRModelViewSet):
@@ -170 +181,10 @@ class ScheduleViewSet(EMRModelViewSet):
@@ -174,0 +195 @@ class ScheduleViewSet(EMRModelViewSet):
@@ -331,2 +352,13 @@ class AvailabilityViewSet(EMRCreateMixin, EMRDestroyMixin, EMRBaseViewSet):
```

### `care/emr/api/viewsets/scheduling/token.py`

+48/−11; 10 hunks; 221 source lines.

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

+8/−2; 4 hunks; 299 source lines.

```diff
@@ -2,0 +3 @@ from django.db import IntegrityError, transaction
@@ -39,0 +41 @@ from care.utils.shortcuts import get_object_or_404
@@ -94 +96 @@ class UserFilter(filters.FilterSet):
@@ -168 +170,5 @@ class UserViewSet(EMRModelViewSet):
```

### `care/emr/api/viewsets/valueset.py`

+6/−1; 2 hunks; 240 source lines.

```diff
@@ -18,0 +19 @@ from care.emr.resources.valueset.spec import ValueSetReadSpec, ValueSetSpec
@@ -74 +75,5 @@ class ValueSetViewSet(EMRModelViewSet):
```

### `care/emr/models/__init__.py`

+2/−0; 1 hunks; 35 source lines.

```diff
@@ -24,0 +25,2 @@ from .questionnaire import *  # noqa F403
```

### `care/emr/models/condition.py`

+14/−0; 1 hunks; 34 source lines.

```diff
@@ -20,0 +21,14 @@ class Condition(EMRBaseModel):
```

### `care/emr/models/encounter.py`

+16/−0; 2 hunks; 101 source lines.

```diff
@@ -9,0 +10,3 @@ from care.emr.resources.patient_identifier.default_expression_evaluator import (
@@ -42,0 +46,13 @@ class Encounter(EMRBaseModel):
```

### `care/emr/models/medication_request.py`

+27/−0; 3 hunks; 91 source lines.

```diff
@@ -7,0 +8,2 @@ from care.emr.models.base import EMRBaseModel
@@ -31,0 +34,2 @@ class MedicationRequest(EMRBaseModel):
@@ -64,0 +69,23 @@ class MedicationRequest(EMRBaseModel):
```

### `care/emr/models/questionnaire.py`

+121/−1; 5 hunks; 313 source lines.

```diff
@@ -3,0 +4 @@ from django.contrib.postgres.fields import ArrayField
@@ -10,0 +12 @@ MAX_QUESTIONNAIRE_TAGS_COUNT = 1000
@@ -33 +35 @@ class QuestionnaireTag(EMRBaseModel):
@@ -83,0 +86,2 @@ class FormSubmission(EMRBaseModel):
@@ -90,0 +95,116 @@ class FormSubmission(EMRBaseModel):
```

### `care/emr/models/report/report_upload.py`

+92/−1; 6 hunks; 147 source lines.

```diff
@@ -3,0 +4 @@ from uuid import uuid4
@@ -11,0 +13,2 @@ from care.utils.models.validators import parse_file_extension
@@ -14 +17,5 @@ class ReportUpload(EMRBaseModel):
@@ -21,0 +29,34 @@ class ReportUpload(EMRBaseModel):
@@ -35,0 +77,42 @@ class ReportUpload(EMRBaseModel):
@@ -50,0 +134,8 @@ class ReportUpload(EMRBaseModel):
```

### `care/emr/models/report/template.py`

+23/−0; 1 hunks; 44 source lines.

```diff
@@ -21,0 +22,23 @@ class Template(SlugBaseModel):
```

### `care/emr/registries/system_questionnaire/system_questionnaire.py`

+26/−1; 3 hunks; 61 source lines.

```diff
@@ -2,0 +3,2 @@ import uuid
@@ -4,0 +7,11 @@ from care.emr.resources.questionnaire.spec import QuestionnaireStatus
@@ -36 +49,13 @@ class InternalQuestionnaireRegistry:
```

### `care/emr/resources/condition/spec.py`

+7/−0; 5 hunks; 157 source lines.

```diff
@@ -14,0 +15 @@ from care.utils.time_util import care_now
@@ -47,0 +49,3 @@ class SeverityChoices(str, Enum):
@@ -87,0 +92 @@ class ConditionSpec(BaseConditionSpec):
@@ -114,0 +120 @@ class ConditionReadSpec(BaseConditionSpec):
@@ -142,0 +149 @@ class ConditionUpdateSpec(BaseConditionSpec):
```

### `care/emr/resources/encounter/constants.py`

+9/−0; 1 hunks; 97 source lines.

```diff
@@ -22,0 +23,9 @@ COMPLETED_CHOICES = [
```

### `care/emr/resources/encounter/spec.py`

+3/−2; 3 hunks; 219 source lines.

```diff
@@ -8 +8 @@ from care.emr.extensions.base import ExtensionResource
@@ -120 +120 @@ class EncounterUpdateSpec(ExtensionValidator, EncounterSpecBase):
@@ -154,0 +155 @@ class EncounterListSpec(EncounterSpecBase):
```

### `care/emr/resources/form_submission/spec.py`

+50/−8; 8 hunks; 106 source lines.

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

+26/−21; 2 hunks; 329 source lines.

```diff
@@ -209,0 +210,23 @@ class CreatePrescription(BaseModel):
@@ -264,21 +287,3 @@ class MedicationRequestSpec(BaseMedicationRequestSpec):
```

### `care/emr/resources/report/report_upload/spec.py`

+18/−2; 4 hunks; 70 source lines.

```diff
@@ -20 +20 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
@@ -30 +30 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
@@ -31,0 +32,8 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
@@ -39,0 +48,8 @@ class ReportUploadListSpec(ReportUploadBaseSpec):
```

### `care/emr/resources/report/template/spec.py`

+2/−0; 1 hunks; 121 source lines.

```diff
@@ -101,0 +102,2 @@ class TemplateReadSpec(TemplateBaseSpec):
```

### `care/emr/resources/scheduling/schedule/spec.py`

+2/−2; 1 hunks; 258 source lines.

```diff
@@ -254,2 +254,2 @@ def has_overlapping_availability(availabilities: list[AvailabilityDateTimeSpec])
```

### `care/emr/utils/mfa.py`

+2/−1; 2 hunks; 62 source lines.

```diff
@@ -12,0 +13 @@ from care.users.models import User
@@ -49 +50 @@ def create_auth_response(user: User) -> Response:
```

### `care/security/authorization/patient.py`

+9/−1; 2 hunks; 139 source lines.

```diff
@@ -6 +6 @@ from care.emr.models.organization import FacilityOrganizationUser, OrganizationU
@@ -32,0 +33,8 @@ class PatientAccess(AuthorizationHandler):
```

### `config/auth_views.py`

+2/−1; 2 hunks; 236 source lines.

```diff
@@ -17,0 +18 @@ from rest_framework_simplejwt.views import TokenVerifyView, TokenViewBase
@@ -134 +135 @@ class TokenObtainPairSerializer(TokenObtainSerializer):
```

### `config/settings/base.py`

+48/−4; 9 hunks; 756 source lines.

```diff
@@ -19,0 +20 @@ from plug_config import manager
@@ -21 +22 @@ from plug_config import manager
@@ -38,0 +40,3 @@ if READ_DOT_ENV_FILE := env.bool("DJANGO_READ_DOT_ENV_FILE", default=False):
@@ -41 +45 @@ SECRET_KEY = env(
@@ -46,0 +51,20 @@ DEBUG = env.bool("DJANGO_DEBUG", False)
@@ -51 +75 @@ DEBUG = env.bool("DJANGO_DEBUG", False)
@@ -419 +443 @@ if USE_TZ:
@@ -471,0 +496,16 @@ AUDIT_LOG_ENABLED = env.bool("AUDIT_LOG_ENABLED", default=False)
@@ -712,0 +753,4 @@ FILE_UPLOAD_EXPIRY_HOURS = env.int("FILE_UPLOAD_EXPIRY_HOURS", default=24)
```

### `config/settings/config.py`

+35/−26; 6 hunks; 334 source lines.

```diff
@@ -3,0 +4 @@ from care.emr.resources.utils import MonetaryCodes, MonetaryComponentDefinitions
@@ -288,0 +290,5 @@ PATIENT_GLOBAL_EDIT_ACCESS_ENABLED = env.bool(
@@ -291,23 +297,26 @@ PREFERENCE_SCHEMA = env.json(
@@ -314,0 +324 @@ PREFERENCE_SCHEMA = env.json(
@@ -316 +325,0 @@ PREFERENCE_SCHEMA = env.json(
@@ -319,2 +328,2 @@ PREFERENCE_SCHEMA = env.json(
```

### `config/settings/local.py`

+9/−0; 2 hunks; 74 source lines.

```diff
@@ -6,0 +7 @@ from care.utils.jwks.generate_jwk import get_jwks_from_file
@@ -49,0 +51,8 @@ DISABLE_RATELIMIT = True
```

### `config/settings/test.py`

+9/−0; 3 hunks; 122 source lines.

```diff
@@ -6,0 +7 @@ from care.utils.jwks.generate_jwk import get_jwks_from_file
@@ -40,0 +42,2 @@ DATABASES = {"default": env.db("DATABASE_URL", default="postgres:///care-test")}
@@ -109,0 +113,6 @@ DISABLE_RATELIMIT = True
```

### `config/urls.py`

+15/−0; 4 hunks; 127 source lines.

```diff
@@ -0,0 +1,2 @@
@@ -19,0 +22 @@ from config import api_router
@@ -110,0 +114 @@ if settings.DEBUG or not settings.IS_PRODUCTION:
@@ -112,0 +117,11 @@ for plug in settings.PLUGIN_APPS:
```

### `plug_config.py`

+13/−1; 1 hunks; 18 source lines.

```diff
@@ -6 +6,13 @@ plugs = []
```

### `plugs/contributions.py`

+71/−0; 1 hunks; 71 source lines.

```diff
@@ -0,0 +1,71 @@
```

### `plugs/urls.py`

+119/−0; 1 hunks; 119 source lines.

```diff
@@ -0,0 +1,119 @@
```

### `plugs/viewset_actions.py`

+89/−0; 1 hunks; 89 source lines.

```diff
@@ -0,0 +1,89 @@
```

## All direct native plugin imports

Ten files, 12 statements. This scans AST Import/ImportFrom, including inner imports;
configuration strings and generic registry discovery are not direct imports.

| Native file:line | Plugin module |
|---|---|
| `care/emr/api/viewsets/encounter.py:63` | `care_suriname.api.viewsets.admission_documentation` |
| `care/emr/api/viewsets/encounter.py:66` | `care_suriname.api.viewsets.emergency_admission` |
| `care/emr/api/viewsets/encounter.py:67` | `care_suriname.models.consult_closure` |
| `care/emr/api/viewsets/form_submission.py:31` | `care_suriname.api.viewsets.clinical_no_store` |
| `care/emr/api/viewsets/medication_request.py:28` | `care_suriname.api.viewsets.clinical_no_store` |
| `care/emr/api/viewsets/report/report_upload.py:31` | `care_suriname.api.viewsets.clinical_no_store` |
| `care/emr/api/viewsets/scheduling/booking.py:56` | `care_suriname.api.viewsets.operation_plan` |
| `care/emr/api/viewsets/scheduling/schedule.py:39` | `care_suriname.resources.scheduling.conflicts` |
| `care/emr/api/viewsets/user.py:41` | `care_suriname.api.viewsets.doctor_activation` |
| `care/emr/models/report/template.py:26` | `care_suriname.reports.template_versioning` |
| `care/emr/utils/mfa.py:13` | `care_suriname.draft_recovery.auth` |
| `config/auth_views.py:18` | `care_suriname.draft_recovery.auth` |

## Other non-plugin fork differences

Tests, immutable migrations, docs and deployment wiring are outside the native
production count. Paths and counts only; no environment contents.

| Path | + / − | Disposition |
|---|---:|---|
| `.dockerignore` | 8 / 0 | D: deployment/build/configuration |
| `.gitignore` | 3 / 0 | D: deployment/build/configuration |
| `CLAUDE.md` | 21 / 0 | D: documentation |
| `care/emr/migrations/0078_medicationrequest_idempotency.py` | 45 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0079_formsubmission_versioned_workflow.py` | 283 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0080_form_submission_artifact.py` | 233 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0081_correspondence_compilation.py` | 299 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0082_correspondencerecipient_correspondencereview_and_more.py` | 394 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0083_correspondencerecipient_kind_constraint.py` | 17 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0084_correspondence_letter_workflow.py` | 470 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0085_correspondence_delivery_ledger.py` | 769 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0086_correspondence_source_correction.py` | 781 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0087_correspondence_continuity.py` | 705 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0088_correspondence_replacement_workflow.py` | 811 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0089_consult_closure_workflow.py` | 144 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0090_consult_closure_recovery_resolution.py` | 48 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0091_form_submission_entered_in_error_audit.py` | 93 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0092_clinical_text_resource.py` | 107 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0093_diagnosis_native_problem_list.py` | 37 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0094_clinical_term_translation.py` | 186 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0095_clinical_term_concept_kind.py` | 27 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0096_encounter_one_active_inpatient.py` | 49 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0097_correspondence_recipient_command.py` | 104 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0098_form_submission_create_draft_command.py` | 30 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0099_encounter_discharge_command.py` | 118 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0100_admission_documentation.py` | 55 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0101_emergency_admission.py` | 50 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0102_emergency_consult_closure.py` | 77 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0103_operation_plan.py` | 74 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0104_unscheduled_consult_closure.py` | 57 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0105_consult_closure_optional_token.py` | 64 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0106_form_submission_lab.py` | 91 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0107_letter_artifact_link_on_revision.py` | 100 / 0 | D: immutable applied migration/history |
| `care/emr/migrations/0108_move_models_to_care_suriname.py` | 1450 / 0 | D: immutable applied migration/history |
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
| `care/emr/tests/test_diagnosis_idempotent_api.py` | 63 / 0 | D: test/fixture |
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
| `care/emr/tests/test_form_submission_workflow.py` | 1520 / 0 | D: test/fixture |
| `care/emr/tests/test_location_api.py` | 23 / 16 | D: test/fixture |
| `care/emr/tests/test_medication_request_idempotency.py` | 628 / 0 | D: test/fixture |
| `care/emr/tests/test_operation_plan.py` | 216 / 0 | D: test/fixture |
| `care/emr/tests/test_patient_api.py` | 41 / 0 | D: test/fixture |
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
| `care/users/migrations/0028_draftrecoverykey.py` | 43 / 0 | D: immutable applied migration/history |
| `care/users/migrations/0029_move_draft_recovery_to_care_suriname.py` | 14 / 0 | D: immutable applied migration/history |
| `care/users/tests/__init__.py` | 0 / 0 | D: test/fixture |
| `care/utils/tests/base.py` | 1 / 1 | D: test/fixture |
| `care/utils/tests/test_celery_dev_settings.py` | 17 / 0 | D: test/fixture |
| `config/settings/AZP_CLOSURE_POLICY.md` | 38 / 0 | D: documentation |
| `config/settings/tests/__init__.py` | 1 / 0 | D: test/fixture |
| `config/settings/tests/test_azp_closure_policy.py` | 19 / 0 | D: test/fixture |
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
| `docker/prod.Dockerfile` | 8 / 1 | D: deployment/build/configuration |
| `docs/development/2026-09-19-diagnosis-command-ownership.md` | 179 / 0 | D: documentation |
| `docs/development/2026-09-19-directory-and-constraint-ownership.md` | 229 / 0 | D: documentation |
| `docs/development/2026-09-19-draft-recovery-ownership.md` | 247 / 0 | D: documentation |
| `docs/development/2026-09-19-final-backend-separation-audit.md` | 342 / 0 | D: documentation |
| `docs/development/2026-09-19-final-backend-separation-hunks.md` | 680 / 0 | D: documentation |
| `docs/development/2026-09-19-form-command-ownership.md` | 143 / 0 | D: documentation |
| `docs/development/2026-09-19-medication-command-ownership.md` | 170 / 0 | D: documentation |
| `docs/development/2026-09-19-note-lab-extraction.md` | 188 / 0 | D: documentation |
| `docs/development/2026-09-19-policy-ownership.md` | 207 / 0 | D: documentation |
| `docs/development/correspondence-letter-pdf-core-patch.md` | 161 / 0 | D: documentation |
| `docs/development/encounter-admission-note-extension-core-patch.md` | 122 / 0 | D: documentation |
| `docs/development/encounter-clinical-closure-core-patch.md` | 169 / 0 | D: documentation |
| `docs/development/encounter-discharge-core-patch.md` | 168 / 0 | D: documentation |
| `docs/development/patient-directory-pagination-core-patch.md` | 52 / 0 | D: documentation |
| `docs/development/plug-app.md` | 427 / 0 | D: documentation |
| `docs/development/schedule-overlap-core-patch.md` | 93 / 0 | D: documentation |
| `docs/development/urology-recent-patients-preference-core-patch.md` | 47 / 0 | D: documentation |
| `plugs/tests/__init__.py` | 0 / 0 | D: test/fixture |
| `plugs/tests/test_contributions.py` | 86 / 0 | D: test/fixture |
| `plugs/tests/test_priority_urls.py` | 122 / 0 | D: test/fixture |
| `plugs/tests/test_viewset_actions.py` | 136 / 0 | D: test/fixture |
| `scripts/care-suriname-backup.sh` | 168 / 0 | D: deployment/build/configuration |
| `scripts/celery-dev.sh` | 2 / 0 | D: deployment/build/configuration |
| `scripts/install-backup-timer.sh` | 65 / 0 | D: deployment/build/configuration |
| `scripts/phase2/rehearse-migration.sh` | 52 / 0 | D: deployment/build/configuration |
| `scripts/phase2/rehearse-step2.sh` | 62 / 0 | D: deployment/build/configuration |
| `scripts/phase2/verify_state.py` | 304 / 0 | D: deployment/build/configuration |
| `scripts/systemd/care-suriname-backup.service` | 18 / 0 | D: deployment/build/configuration |
| `scripts/systemd/care-suriname-backup.timer` | 16 / 0 | D: deployment/build/configuration |

## Subsequent completed-department extraction — 19 September 2026

The B/F counterexample recorded above is now plugin-owned through a generic
organization-scope contribution in the shared role lookup. Its permission/query
behavior remains unchanged. This document remains historical at 347517ddb;
see [current verification and closure](2026-09-19-patient-access-ownership.md)
and [current hunk inventory](2026-09-19-ownership-closure-hunks.md). Completion
means source ownership under the documented exceptions, not zero native edits
or certification of the separately recorded baseline authorization failures.

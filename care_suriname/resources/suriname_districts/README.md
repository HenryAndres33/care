# Districts of Suriname (geographic organizations)

Owner request, 30 September 2026: the registration address ("Land" →
"District") must offer all ten districts. CARE stores these as native
geographic organizations (`emr_organization`, `org_type="govt"`); this is data,
not code, so it does not travel with a push.

`python manage.py provision_suriname_districts` (dry run) /
`--apply` adds each district from `__init__.py` that is missing under the one
country organization "Suriname" (`metadata.govt_org_type = "country"`), with
`metadata.govt_org_type = "district"`, through CARE's own `Organization.save`
(level/parent caches, duplicate-name check). Existing rows are never changed or
deleted. The dry run first lists the top-level geographic organizations
(name, type, number of children). If no top-level "Suriname" exists, it stops
unless `--create-country` is given, which creates it as a country
(`govt_org_children_type = "district"`). A "Suriname" that is not marked as a
country, or more than one, is reported and left for a person to resolve.

Run it on the server once (CODING_RULES §7 step 7) after the backend update.

Rollback: soft-delete an added district in CARE's admin organization screen,
only if no patient uses it as `geo_organization`.

Tests: `care_suriname/tests/test_suriname_districts.py`.

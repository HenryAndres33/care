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
(`govt_org_children_type = "district"`). An untyped top-level "Suriname" (the
server's clean start, 30 September 2026) is changed only with `--mark-country`:
its metadata gets `govt_org_type = "country"` and
`govt_org_children_type = "district"`, and its untyped children whose name is a
district get `govt_org_type = "district"`, so CARE labels the levels "Land" and
"District". Other children, names and patient links are untouched. A "Suriname"
of another type, or more than one, is reported and left for a person.

Run it on the server once (CODING_RULES §7 step 7) after the backend update.

Rollback: remove the added metadata keys in CARE's admin organization screen
(labels fall back to "Land" for every level); soft-delete an added district in CARE's admin organization screen,
only if no patient uses it as `geo_organization`.

Tests: `care_suriname/tests/test_suriname_districts.py`.

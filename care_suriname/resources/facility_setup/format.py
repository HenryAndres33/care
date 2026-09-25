"""Shared constants and helpers for the facility-setup file format."""

FORMAT_NAME = "care-suriname-facility-setup"
FORMAT_VERSION = 1

# Forms the urology workspace runs on. Instance-wide in CARE, so they are
# selected by slug rather than by facility.
DEFAULT_QUESTIONNAIRE_SLUGS = (
    "urology-medisch-dossier",
    "urology-operaties",
    "urology-vochtbalans",
)

# Only the plug's own key of `meta` is carried; it holds the governed lab
# reference ranges, whose fingerprint is computed from content, not ids.
PLUG_META_KEY = "care_suriname"

# Stands for CARE's system-generated "Administration" root, which every new
# facility gets automatically and is therefore never exported itself.
FACILITY_ROOT = "__facility_root__"


class FacilitySetupError(Exception):
    """The setup cannot be moved safely; nothing is written."""


def slug_value(instance) -> str:
    """The facility-independent part of a facility-scoped slug."""
    return instance.parse_slug(instance.slug)["slug_value"]


def plug_meta(meta) -> dict:
    """Keep only the plug's key of a `meta` JSON object."""
    if isinstance(meta, dict) and PLUG_META_KEY in meta:
        return {PLUG_META_KEY: meta[PLUG_META_KEY]}
    return {}


def tree_order(rows, parent_of):
    """Parents before children, so an importer can resolve each parent."""
    by_key = {row["name"]: row for row in rows}
    ordered, seen = [], set()

    def visit(row, trail=()):
        if row["name"] in seen:
            return
        if row["name"] in trail:
            msg = f"Cycle in parent chain at {row['name']!r}"
            raise FacilitySetupError(msg)
        parent = parent_of(row)
        if parent is not None and parent in by_key:
            visit(by_key[parent], (*trail, row["name"]))
        seen.add(row["name"])
        ordered.append(row)

    for row in rows:
        visit(row)
    return ordered

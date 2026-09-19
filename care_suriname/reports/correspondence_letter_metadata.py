"""Patient metadata used by the controlled correspondence renderer."""

_NON_RECORD_IDENTIFIER_SYSTEMS = ("phone", "email", "patient-name", "/name")


def _identifier_config(config_id):
    from care.emr.models.patient import PatientIdentifierConfigCache

    try:
        return PatientIdentifierConfigCache.get_config(str(config_id)) or {}
    except Exception:  # a missing config must not break the PDF
        return {}


def patient_record_identifier(patient, encounter):
    """Return a record identifier while excluding contact and name identifiers."""
    identifiers = list(patient.instance_identifiers or [])
    facility_identifiers = patient.facility_identifiers or {}
    identifiers.extend(
        facility_identifiers.get(str(encounter.facility_id), [])
        or facility_identifiers.get(encounter.facility_id, [])
    )
    ranked = []
    for item in identifiers:
        if not isinstance(item, dict) or not item.get("value"):
            continue
        config = _identifier_config(item.get("config")).get("config") or {}
        system = str(config.get("system") or "").casefold()
        if any(marker in system for marker in _NON_RECORD_IDENTIFIER_SYSTEMS):
            continue
        use = str(config.get("use") or "").casefold()
        rank = 0 if use in ("usual", "official") else 1
        ranked.append((rank, str(item["value"])))
    ranked.sort(key=lambda entry: entry[0])
    return ranked[0][1] if ranked else "Niet vastgelegd"

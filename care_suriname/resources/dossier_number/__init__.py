"""The paper-record number (Dossiernummer) as a native CARE patient identifier.

See README.md in this directory.
"""

DOSSIER_NUMBER_SYSTEM = "system.care-suriname/medical-record-number"

# Instance-wide: CARE's patient update refuses facility-scoped configs.
# `use=official` makes the letter and PDF headers pick it first; the system
# name contains "medical-record", so the urology header shows it as the MRN.
DOSSIER_NUMBER_CONFIG = {
    "use": "official",
    "description": "Nummer van het papieren patiëntendossier.",
    "system": DOSSIER_NUMBER_SYSTEM,
    "required": False,
    "unique": True,
    "regex": "",
    "display": "Dossiernummer",
    "retrieve_config": {
        "retrieve_with_dob": False,
        "retrieve_with_year_of_birth": False,
        "retrieve_with_otp": False,
        "retrieve_partial_search": False,
    },
    "default_value": None,
    "auto_maintained": False,
}

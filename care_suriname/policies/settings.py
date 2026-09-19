"""Existing required-form defaults and local/test overrides (no secrets)."""

SETTING_DEFAULTS = {
    "base": {
        "CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT": {
            "urology": ["urology-medisch-dossier"]
        }
    },
    "local": {
        "CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT": {
            "urology": ["urology-medisch-dossier"],
            "19d9ec24-cf5e-4944-93a9-a4900e1f4feb": ["urology-medisch-dossier"],
            "d1dd82e0-0690-4121-94d8-7605b27192ee": ["urology-medisch-dossier"],
        }
    },
    "test": {
        "CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT": {
            "urology": ["urology-medisch-dossier"],
            "19d9ec24-cf5e-4944-93a9-a4900e1f4feb": ["urology-medisch-dossier"],
        }
    },
}

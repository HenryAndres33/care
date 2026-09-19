"""Version-one recent-patient payload bounds; no clinical data defaults."""

PREFERENCE_SCHEMAS = {
    "urology_recent_patients": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "UrologyRecentPatients",
        "type": "object",
        "additionalProperties": False,
        "required": ["facilities", "version"],
        "properties": {
            "version": {"const": 1},
            "facilities": {
                "type": "object",
                "maxProperties": 50,
                "propertyNames": {
                    "pattern": (
                        "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
                        "[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-"
                        "[0-9a-fA-F]{12}$"
                    )
                },
                "additionalProperties": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "displayName",
                            "lastOpened",
                            "mrn",
                            "patientId",
                        ],
                        "properties": {
                            "patientId": {
                                "type": "string",
                                "pattern": (
                                    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
                                    "[1-8][0-9a-fA-F]{3}-"
                                    "[89abAB][0-9a-fA-F]{3}-"
                                    "[0-9a-fA-F]{12}$"
                                ),
                            },
                            "displayName": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 200,
                            },
                            "mrn": {"type": "string", "maxLength": 100},
                            "lastOpened": {
                                "type": "string",
                                "format": "date-time",
                            },
                        },
                    },
                },
            },
        },
    }
}

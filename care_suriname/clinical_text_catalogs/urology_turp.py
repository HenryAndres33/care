"""TURP Smart Text fixture for automated tests.

This is NOT a mirror of the live catalog and must not be treated as one.

Clinicians author the real Smart Text templates through the UI, and that live
content is the source of truth. This declaration exists only so automated tests
start from a known, deterministic catalog. The two are expected to drift — the
live TURP template already uses a shared anesthesia list that this fixture does
not — and that drift is fine.

Do not "resync" this file to match production. Keeping two copies aligned by
hand is the failure mode this arrangement avoids. If a test needs to assert on a
specific template, assert against this fixture in a test database.

`provision_urology_turp_clinical_text` refuses to write this into anything but a
known test database for the same reason. See care_fe/docs/pilot-scope.md.
"""

TURP_CATALOG_VERSION = 3

TURP_TEMPLATE_BODY = """OPERATIEVERLOOP

Patiënt wordt onder [[*ANESTHESIE|list:turp_anesthesie*]] in [[*POSITIONERING|list:turp_positionering*]] gelegd.
[[if:POSITIONERING=anders]]
Andere positionering: [[*POSITIONERING_ANDERS*]]
[[endif]]
Antibioticaprofylaxe: [[*ANTIBIOTICA|choice:Ja,Nee,Conform protocol*]]
[[if:ANTIBIOTICA=Ja]]
Antibioticum: [[*ANTIBIOTICUM|list:antibiotica_profylaxe*]]
[[endif]]

Introductiewijze: [[*INTRODUCTIE|list:turp_introductie*]]
[[if:INTRODUCTIE=zichtobturator]]
Resectoscoop wordt met [[*RESECTOSCOOP_MAAT|list:turp_resectoscoop_maat*]] zichtobturator ingebracht.
[[endif]]
[[if:INTRODUCTIE=blinde introductie]]
Resectoscoop wordt blind ingebracht.
[[endif]]
Inspectie van de blaas wordt verricht.
Blaasafwijkingen: [[*BLAASAFWIJKINGEN|choice:Geen,Wel*]]
[[if:BLAASAFWIJKINGEN=Wel]]
Beschrijving afwijkingen: [[*BLAASAFWIJKINGEN_DETAIL*]]
[[endif]]
Ureterostia worden geïdentificeerd.

Middenkwab aanwezig: [[*MIDDENKWAB|choice:Nee,Ja*]]
Prostaatconfiguratie: [[*PROSTAATCONFIGURATIE|list:turp_prostaatconfiguratie*]]
[[if:PROSTAATCONFIGURATIE=anders]]
Andere prostaatconfiguratie: [[*PROSTAATCONFIGURATIE_ANDERS*]]
[[endif]]

Resectie wordt gestart aan de [[*STARTZIJDE|list:turp_startzijde*]] zijde.
Semi-circulaire resectie van laterale adenomen: [[*SEMICIRCULAIR|choice:Ja,Nee*]]
Distale begrenzing ter hoogte van de colliculus gerespecteerd: [[*DISTALE_BEGRENZING|choice:Ja,Nee*]]
Vaporisatie toegepast: [[*VAPORISATIE|choice:Nee,Ja*]]
Contralaterale resectie verricht: [[*CONTRALATERAAL|choice:Ja,Nee*]]

Adequate hemostase bereikt: [[*HEMOSTASE|choice:Ja,Nee*]]
[[if:HEMOSTASE=Ja]]
Hemostasemethode: [[*HEMOSTASE_METHODE|list:turp_hemostasemethode*]]
[[endif]]
[[if:HEMOSTASE=Nee]]
Toelichting hemostase: [[*HEMOSTASE_DETAIL*]]
[[endif]]

Prostaatchips verwijderd: [[*CHIPS|choice:Ja,Nee*]]
[[if:CHIPS=Ja]]
Verwijderingsmethode: [[*CHIP_METHODE|list:turp_chipmethode*]]
Preparaat ingestuurd voor PA: [[*PA|choice:Ja,Nee*]]
[[endif]]

Spoelkatheter achtergelaten: [[*SPOELKATHETER|choice:Ja,Nee*]]
[[if:SPOELKATHETER=Ja]]
Kathetertype: [[*KATHETER_TYPE|list:turp_kathetertype*]]
Kathetermaat: [[*KATHETER_MAAT|list:turp_kathetermaat*]]
Spoelsysteem: [[*SPOELSYSTEEM|list:turp_spoelsysteem*]]
Beoogde katheterduur: [[*KATHETERDUUR|list:turp_katheterduur*]]
[[endif]]

COMPLICATIES
Verloop: [[*VERLOOP|choice:Ongecompliceerd,Gecompliceerd*]]
[[if:VERLOOP=Gecompliceerd]]
Beschrijving complicatie(s): [[*COMPLICATIES*]]
[[endif]]
Bloedverlies: [[*BLOEDVERLIES|list:turp_bloedverlies*]]

Intravesicale therapie: [[*INTRAVESICAAL|choice:Nee,Ja,Niet van toepassing*]]
Aanvullend postoperatief beleid: [[*AANVULLEND|choice:Nee,Ja*]]
[[if:AANVULLEND=Ja]]
Aanvullende instructies: [[*AANVULLENDE_INSTRUCTIES*]]
[[endif]]

Contactpersoon: [[*CONTACTSTATUS|list:turp_contactstatus*]]"""


def _options(key, values):
    return [
        {
            "id": f"{key}-{index}",
            "value": value,
            "label": value,
            "enabled": True,
        }
        for index, value in enumerate(values, start=1)
    ]


def _list(key, label, description, values):
    return {
        "kind": "list",
        "key": key,
        "label": label,
        "description": description,
        "status": "active",
        "payload": {
            "label": label,
            "description": description,
            "options": _options(key, values),
        },
    }


TURP_LISTS = [
    _list(
        "antibiotica_profylaxe",
        "Antibiotica profylaxe",
        "Perioperatieve antibiotica voor profylaxe.",
        ["Cefazoline", "Ciprofloxacine", "Amoxicilline/clavulaan", "Gentamicine"],
    ),
    _list(
        "turp_anesthesie",
        "TURP anesthesie",
        "Anesthesievormen voor TURP-operatiedocumentatie.",
        [
            "algehele anesthesie",
            "spinale anesthesie",
            "sedatie",
            "lokale anesthesie",
        ],
    ),
    _list(
        "turp_positionering",
        "TURP positionering",
        "Positioneringen voor TURP-operatiedocumentatie.",
        ["steensnedeligging", "rugligging", "anders"],
    ),
    _list(
        "turp_introductie",
        "TURP introductiewijze",
        "Introductiewijzen voor de resectoscoop.",
        ["zichtobturator", "blinde introductie"],
    ),
    _list(
        "turp_resectoscoop_maat",
        "TURP resectoscoopmaat",
        "",
        ["24 Ch", "26 Ch", "27 Ch"],
    ),
    _list(
        "turp_prostaatconfiguratie",
        "TURP prostaatconfiguratie",
        "",
        ["bilobair", "trilobair", "anders"],
    ),
    _list(
        "turp_startzijde",
        "TURP startzijde resectie",
        "",
        ["linker", "rechter"],
    ),
    _list(
        "turp_hemostasemethode",
        "TURP hemostasemethode",
        "",
        ["lis", "rollerball", "bipolaire coagulatie"],
    ),
    _list(
        "turp_chipmethode",
        "TURP chipverwijdering",
        "",
        ["ellik", "spoelen", "morcellator"],
    ),
    _list(
        "turp_kathetertype",
        "TURP kathetertype",
        "",
        ["3-weg spoelkatheter", "2-weg katheter"],
    ),
    _list(
        "turp_kathetermaat",
        "TURP kathetermaat",
        "",
        ["18 Ch", "20 Ch", "22 Ch"],
    ),
    _list(
        "turp_spoelsysteem",
        "TURP spoelsysteem",
        "",
        ["Geen", "continu", "intermitterend"],
    ),
    _list(
        "turp_katheterduur",
        "TURP katheterduur",
        "",
        ["Nog te bepalen", "1 dag", "2 dagen", "3 dagen", "5 dagen", "anders"],
    ),
    _list(
        "turp_bloedverlies",
        "TURP bloedverlies",
        "",
        ["Minimaal", "Matig", "Fors"],
    ),
    _list(
        "turp_contactstatus",
        "TURP contactpersoonstatus",
        "",
        ["geïnformeerd", "niet bereikbaar", "niet van toepassing"],
    ),
]


TURP_CLINICAL_TEXT_CATALOG = [
    {
        "kind": "template",
        "key": ".turp",
        "label": "TURP operatieverloop",
        "description": (
            "Volledig TURP-operatieverloop met keuzelijsten en conditionele velden."
        ),
        "status": "active",
        "payload": {
            "name": "TURP operatieverloop",
            "description": (
                "Volledig TURP-operatieverloop met keuzelijsten en conditionele velden."
            ),
            "shortcut": ".turp",
            "body": TURP_TEMPLATE_BODY,
            "scopes": ["urology", "medisch-dossier", "operations"],
        },
    },
    *TURP_LISTS,
    {
        "kind": "preset",
        "key": "ja_nee",
        "label": "Ja / Nee",
        "description": "",
        "status": "active",
        "payload": {
            "label": "Ja / Nee",
            "options": _options("ja-nee", ["Ja", "Nee"]),
        },
    },
]

"""Urology drawing content for the note PDF (owner, 29 September 2026).

Mirror of the frontend catalog (care_fe src/Plugins/urology/forms/
note-drawings/data/). Ids are stored in notes and never change meaning; a new
image gets a new id. A specialty fork replaces this file and the images in
``assets/tekeningen/`` (see NOTE_DRAWINGS.md); the rendering stays.
"""

import base64
from functools import cache
from pathlib import Path

ASSET_DIR = Path(__file__).parent / "assets" / "tekeningen"

CANVAS_WIDTH = 1122
CANVAS_HEIGHT = 1402
STAMP_SIZE = 110

TEMPLATES = {
    "urology-blaas-v1": ("blaas.jpg", "Cystoscopie"),
    "urology-urinewegen-v1": ("urinewegen.jpg", "URS"),
}

STAMPS = {
    "tumor": ("tumor.png", "Tumor"),
    "cis": ("cis.png", "CIS"),
    "stolsel": ("stolsel.png", "Stolsel"),
    "litteken": ("litteken.png", "Litteken/resectie"),
    "biopsie": ("biopsie.png", "Biopsie"),
    "steen": ("steen.png", "Steen"),
    "cyste": ("cyste.png", "Cyste"),
    "stenose": ("stenose.png", "Stenose"),
    "stent": ("stent.png", "Stent"),
}

STROKE_COLORS = {
    "zwart": "#1A1A1A",
    "rood": "#C62828",
    "blauw": "#1565C0",
    "groen": "#2E7D32",
}

# Same numbers as the frontend (noteDrawingTypes.ts NOTE_DRAWING_LIMITS).
MAX_DRAWINGS = 6
MAX_NUMBER = 99
MAX_STAMPS = 30
MAX_STROKES = 30
MAX_POINTS_CHARS = 3000
MAX_SIZE_CHARS = 40
MAX_NOTE_CHARS = 160
SCALE_MIN = 0.5
SCALE_MAX = 3


@cache
def asset_data_uri(filename: str) -> str:
    """Inline image for WeasyPrint, which fetches nothing (no base_url)."""
    mime = "image/jpeg" if filename.endswith(".jpg") else "image/png"
    data = base64.b64encode((ASSET_DIR / filename).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"

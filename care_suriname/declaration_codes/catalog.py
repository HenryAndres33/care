"""Urology declaration code lists (content, not mechanics).

Owner-provided, 30 September 2026; codes only, no prices (owner: prices vary
with time and insurer). Which list applies follows the patient's insurance
plan (frontend `declaratiecodes/data/planCodeLists.ts`).

- `szf`: SZF "Verrichtingenlijst van de BAZO & Regulieren verzekerden",
  Urologie (screenshot); includes the three combination codes.
- `assuria`: Assuria's "CODES UROLOOG" sheet (DOC-20260923-WA0007.xls) for the
  AZPAS plans; the four rows without a code are left out (owner).
- `azp-po`: AZP "Administratie 308, Assortiment Vakgroep Urologie Survam"
  (screenshot, PO089-PO117) for PZS, other plans and Eigen rekening.

Codes are verbatim. Descriptions are lightly edited for readability: typos
fixed ("Cytoscopie", "Bioptei", "pijn stiller", "Uretera", a stray
apostrophe), abbreviations spelled out ("diagn.", "v.d.", "nat.lich.",
"Cath"), a capital first letter, and the parts of the three SZF combination
codes named. A code is never reused for something else: remove a line (it is
retired) and add a new one.
"""

from typing import NamedTuple


class CodeList(NamedTuple):
    key: str
    label: str
    codes: tuple[tuple[str, str], ...]


SZF = CodeList(
    key="szf",
    label="SZF",
    codes=(
        ("201000", "Consult"),
        ("217001", "Aanleggen van een cystostomie"),
        ("217006", "Aanleggen van een urethrostomie"),
        ("217011", "Cavernosografie met of zonder veneuze compressie"),
        ("217016", "Circumcisie cq dorsal slit cq frenulumplastiek"),
        ("217021", "Colposcopie"),
        ("217026", "Colposcopie met biopsie of kweek"),
        ("217031", "Cystoscopie"),
        ("217036", "Cystoscopie met biopsie"),
        ("217041", "Cystoscopie met prostaatbiopsie"),
        ("217046", "Cystoscopie met retrograde 1x"),
        ("217051", "Cystoscopie met retrograde 2x"),
        ("217056", "Diagnostische catheterisatie van de blaas"),
        ("217061", "Doppleronderzoek bij potentiestoornissen"),
        ("217066", "Echografie nieren en/of blaas"),
        ("217071", "Endoscopische blaassteenbehandeling eenvoudig"),
        ("217076", "Endoscopische blaassteenbehandeling met fragmentatie"),
        ("217081", "Endoscopische uretersteenbehandeling eenvoudig"),
        ("217086", "Endoscopische uretersteenbehandeling met fragmentatie"),
        ("217096", "Endoscopische verwijdering van corpora aliena"),
        ("217101", "Farmacocavernosografie"),
        ("217106", "Haemodynamische potentie odz met farmaca"),
        ("217111", "Incisie abces waar dan ook"),
        ("217116", "Infertilisatievasectomie"),
        ("217121", "Meatusplastiek"),
        ("217126", "Nierbiopsie met echo"),
        ("217131", "Niercystepunctie met echo"),
        ("217141", "Operatieve behandeling van veneuze lekkage v.d. corpora cavernosa"),
        ("217156", "Percutaan inbrengen van een nephrostomie"),
        ("217161", "Priapisme operatie"),
        ("217166", "Prostaatbiopsie"),
        ("217171", "Punctie hydro- of spermatocele"),
        ("217176", "Retrograde urethrografie"),
        ("217181", "Testisbiopsie"),
        ("217186", "Therapeutische endoscopische handelingen in de urethra"),
        ("217191", "Ureteropyeloscopie"),
        ("217196", "Urethracalibratie"),
        ("217201", "Urodynamisch onderzoek 3-5 kanalig"),
        ("217206", "Varicoceleoperatie"),
        ("217211", "Vasovesiculografie"),
        ("217221", "Anaesthesie geleiding"),
        ("217226", "Anaesthesie lokaal"),
        ("217231", "Corpus alienum verwijderen (natuurlijke lichaamsopening)"),
        ("217236", "Incisie abces (klein)"),
        ("217241", "Injectie intra-articulair"),
        ("217246", "Injectie intra-laesionaal"),
        ("217251", "Injectie intra-musculair"),
        ("217256", "Injectie intra-veneus"),
        ("217261", "Nagelextractie"),
        ("217266", "Preparaat incl. beoordeling (bact./cytologie)"),
        ("217271", "Punctie"),
        ("217276", "Wondbehandeling (max 2x per week)"),
        ("217281", "Twee-kanalige flowmetrie"),
        ("217277", "Flow + ER (217281 + 217066)"),
        ("217278", "Cystoscopie en verwijderen corpus alienum (217031 + 217096)"),
        ("217279", "Prostaatbiopsie + TRUS (217166 + 217066)"),
    ),
)

ASSURIA = CodeList(
    key="assuria",
    label="Assuria",
    codes=(
        ("39445", "Injecties, m.u.v. locale anaesthesie"),
        ("39492", "Echografie van de buikorganen"),
        ("39837", "Residubepaling"),
        ("39961", "Therapeutische cystoscopie"),
        ("ALG004", "Catheter doorspoelen"),
        ("CHIR009", "Inbrengen suprapubische catheter"),
        ("HAC009", "Circumcisie"),
        ("RO00337", "Echo prostaat/TRUS"),
        ("RO00344", "Echo abdomen"),
        ("SEM001", "Consult, standaard, specialist"),
        ("SEM002", "Catheter inbrengen/verwijderen"),
        ("SPEC00308", "JJ stent inbrengen/verwijderen"),
        ("SPEC00368", "Transoesophageale echo"),
        ("SPEC00404", "Nefrostomiecatheter verwisselen"),
        ("URO0001", "Urethra/ureter oprekken"),
        ("URO0002", "Uroflowmetrie"),
        ("WOND001", "Wondbehandeling"),
    ),
)

AZP_PO = CodeList(
    key="azp-po",
    label="AZP",
    codes=(
        ("PO089", "Consult"),
        ("PO091", "Consult niet ingezetene"),
        ("PO092", "Echo nieren/blaas"),
        ("PO093", "TRUS (echo van de prostaat)"),
        ("PO094", "Residumeting"),
        ("PO095", "Blaasspoeling/chemokuur"),
        ("PO096", "Blaascatheter inbrengen"),
        ("PO097", "Blaascatheter verwisselen/verwijderen"),
        ("PO098", "Catheter doorspoelen"),
        ("PO099", "Suprapubische catheter (SPC) verwisselen"),
        ("PO100", "Aanleggen cystostomie, SPC inbrengen"),
        ("PO101", "NSK verwisselen/verwijderen"),
        ("PO102", "Uroflowmetrie (flow)"),
        ("PO103", "Flow + ER"),
        ("PO104", "Prostaatbiopsie + TRUS (PB/TRUS)"),
        ("PO105", "Biopt penis"),
        ("PO106", "Cystoscopie (star + flex)"),
        ("PO107", "Cystoscopie + verwijdering corpus alienum (2-J uit)"),
        ("PO108", "Verdoving lokaal"),
        ("PO109", "Urethra oprekken"),
        ("PO110", "Injectie (hormonaal)"),
        ("PO111", "Injectie (pijnstiller)"),
        ("PO112", "Circumcisie onder lokaal"),
        ("PO113", "Frenulotomie"),
        ("PO114", "Vasectomie"),
        ("PO115", "Scrotale abcesdrainage"),
        ("PO116", "Wondbehandeling"),
        ("PO117", "Medische verklaring (verzekering)"),
    ),
)

CODE_LISTS: tuple[CodeList, ...] = (SZF, ASSURIA, AZP_PO)

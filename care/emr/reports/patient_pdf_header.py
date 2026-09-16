from __future__ import annotations

from html import escape

RUNNING_PATIENT_CSS = """
@page {
  @top-left { content: element(patientHeader); }
  @bottom-right {
    color: #64748b;
    content: "Pagina " counter(page) " van " counter(pages);
    font-family: Arial, sans-serif;
    font-size: 8pt;
  }
}
.running-patient-header {
  color: #475569;
  font-family: Arial, sans-serif;
  font-size: 8pt;
  line-height: 1.2;
  position: running(patientHeader);
}
"""


def render_running_patient_header(*, name, date_of_birth, identifier=""):
    details = [str(name), f"Geb. {date_of_birth}"]
    if identifier and identifier != "Niet vastgelegd":
        details.append(f"Patiëntnr. {identifier}")
    return (
        '<div class="running-patient-header">'
        + escape(" · ".join(details))
        + "</div>"
    )

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class CorrespondenceBrand:
    logo_data_uri: str | None = None
    address: str = ""
    central_phone: str = ""
    department_phone: str = ""


_AZP_LOGO_BASE64 = (
    "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"
)

_AZP_BRAND = CorrespondenceBrand(
    logo_data_uri=f"data:image/png;base64,{_AZP_LOGO_BASE64}",
    address="Flustraat 1 · Paramaribo, Suriname",
    central_phone="Centraal: +597 442222",
    department_phone="Polikliniek Urologie: +597 8629846 · toestel 251",
)


def correspondence_brand(facility_name: str) -> CorrespondenceBrand:
    normalized = facility_name.strip().casefold()
    if normalized in {"academisch ziekenhuis paramaribo", "az paramaribo", "azp"}:
        return _AZP_BRAND
    return CorrespondenceBrand()


def render_letterhead(*, facility_name: str, department_title: str) -> str:
    brand = correspondence_brand(facility_name)
    logo = ""
    if brand.logo_data_uri:
        logo = f'<img class="letterhead-logo" src="{brand.logo_data_uri}" alt="AZP">'
    contact_values = (
        brand.address,
        brand.central_phone,
        brand.department_phone,
    )
    contact = "".join(
        f"<div>{escape(value)}</div>" for value in contact_values if value
    )
    return (
        '<header class="letterhead">'
        f'<div class="letterhead-brand">{logo}</div>'
        '<div class="letterhead-identity">'
        f'<div class="facility-name">{escape(facility_name)}</div>'
        f'<div class="specialty-name">{escape(department_title)}</div>'
        f'<div class="facility-contact">{contact}</div>'
        "</div></header>"
    )

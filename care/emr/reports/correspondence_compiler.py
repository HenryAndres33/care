from __future__ import annotations

import re
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from typing import Any

from jinja2 import StrictUndefined, TemplateError, nodes
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup

from care.emr.resources.form_submission.artifact import has_unresolved_placeholder


class CorrespondenceCompilationError(ValueError):
    pass


class CorrespondenceSanitizationError(CorrespondenceCompilationError):
    pass


MAX_TEMPLATE_SOURCE_BYTES = 100_000
MAX_TEMPLATE_AST_NODES = 1_000
MAX_RENDERED_TEMPLATE_BYTES = 500_000
MAX_COMPILED_HTML_BYTES = 2_000_000
FORBIDDEN_TEMPLATE_NODES = (
    nodes.AssignBlock,
    nodes.Block,
    nodes.Call,
    nodes.CallBlock,
    nodes.Extends,
    nodes.FilterBlock,
    nodes.For,
    nodes.FromImport,
    nodes.Import,
    nodes.Include,
    nodes.Macro,
    nodes.Mul,
    nodes.Pow,
)


def compile_correspondence_html(
    *,
    compilation_id,
    compiled_at: datetime,
    template_data: str,
    context: dict,
    provenance: dict,
) -> tuple[str, str]:
    if not isinstance(template_data, str) or not template_data.strip():
        raise CorrespondenceCompilationError("Correspondence template is empty")
    if len(template_data.encode()) > MAX_TEMPLATE_SOURCE_BYTES:
        raise CorrespondenceCompilationError(
            "Correspondence template exceeds the source-size limit"
        )
    environment = SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters.clear()
    environment.globals.clear()
    try:
        parsed = environment.parse(template_data)
        _validate_template_ast(parsed)
        rendered_template = environment.from_string(template_data).render(**context)
    except TemplateError as exc:
        raise CorrespondenceCompilationError(
            "Correspondence template syntax or conditional evaluation failed"
        ) from exc
    if len(rendered_template.encode()) > MAX_RENDERED_TEMPLATE_BYTES:
        raise CorrespondenceCompilationError(
            "Correspondence template output exceeds the rendering limit"
        )
    if has_unresolved_placeholder(rendered_template):
        raise CorrespondenceCompilationError(
            "Correspondence output contains unresolved placeholders"
        )

    sanitized_template = sanitize_correspondence_html(rendered_template)
    if len(sanitized_template.encode()) > MAX_RENDERED_TEMPLATE_BYTES:
        raise CorrespondenceCompilationError(
            "Sanitized correspondence template exceeds the rendering limit"
        )
    if not _html_to_text(sanitized_template):
        raise CorrespondenceCompilationError("Correspondence template rendered empty")
    mandatory_snapshot = _mandatory_snapshot_html(
        compilation_id=compilation_id,
        compiled_at=compiled_at,
        context=context,
        provenance=provenance,
    )
    compiled_html = sanitize_correspondence_html(
        f"<article>{sanitized_template}{mandatory_snapshot}</article>"
    )
    if len(compiled_html.encode()) > MAX_COMPILED_HTML_BYTES:
        raise CorrespondenceCompilationError(
            "Compiled correspondence exceeds the document-size limit"
        )
    if has_unresolved_placeholder(compiled_html):
        raise CorrespondenceCompilationError(
            "Sanitized correspondence contains unresolved placeholders"
        )
    compiled_text = _html_to_text(compiled_html)
    if not compiled_text:
        raise CorrespondenceCompilationError("Sanitized correspondence is unreadable")
    return compiled_html, compiled_text


def sanitize_correspondence_html(value: str) -> str:
    parser = _StrictHTMLSanitizer()
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:
        if isinstance(exc, CorrespondenceSanitizationError):
            raise
        raise CorrespondenceSanitizationError(
            "Correspondence HTML sanitization failed"
        ) from exc
    return parser.output


def readable_form_html(response_dump: dict) -> Markup:
    return Markup(  # noqa: S704 - content is recursively escaped below
        '<section class="form-snapshot"><h2>Finalized form responses</h2>'
        f"{_render_value(response_dump)}</section>"
    )


def readable_medications_html(medications: list[dict]) -> Markup:
    if not medications:
        content = "<p>No linked confirmed medication actions.</p>"
    else:
        rows = "".join(
            "<tr>"
            f"<td>{escape(item['display'])}</td>"
            f"<td>{escape(item['status'])}</td>"
            f"<td>{escape(item['intent'])}</td>"
            f"<td>{escape(item['dosage_text'])}</td>"
            f"<td>{escape(item['id'])}</td>"
            "</tr>"
            for item in medications
        )
        content = (
            "<table><thead><tr><th>Medication</th><th>Status</th>"
            "<th>Intent</th><th>Dosage</th><th>Reference</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    return Markup(  # noqa: S704 - all dynamic values are escaped above
        '<section class="medication-snapshot"><h2>Confirmed medication actions</h2>'
        f"{content}</section>"
    )


def _mandatory_snapshot_html(*, compilation_id, compiled_at, context, provenance):
    patient_identifiers = "; ".join(
        f"{item.get('config', 'identifier')}: {item['value']}"
        for item in context["patient"]["identifiers"]
    )
    source_rows = [
        ("Compilation reference", compilation_id),
        ("Compiled at", _timestamp(compiled_at)),
        ("Patient name", context["patient"]["name"]),
        ("Patient CARE reference", context["patient"]["id"]),
        ("Patient date of birth", context["patient"]["date_of_birth"]),
        ("Patient identifiers", patient_identifiers),
        ("Encounter CARE reference", context["encounter"]["id"]),
        ("Encounter date", context["encounter"]["date"]),
        ("Encounter reason", context["encounter"]["reason"]),
        ("Facility", context["encounter"]["facility"]["name"]),
        ("Department", context["encounter"]["department"]["name"]),
        ("Author", context["author"]["display"]),
        ("Author CARE reference", context["author"]["id"]),
        ("Author professional role", context["author"]["professional_role"]),
        ("Author qualification", _recorded(context["author"]["qualification"])),
        ("Author registration", _recorded(context["author"]["registration"])),
        ("Author facility", context["author"]["facility"]["name"]),
        ("Author department", context["author"]["department"]["name"]),
        ("Finalized form reference", context["form"]["id"]),
        ("Finalized form version", context["form"]["version"]),
        ("Finalized form SHA-256", context["form"]["hash"]),
        ("Form artifact reference", context["form"]["artifact_id"]),
        ("Template reference", context["template"]["id"]),
        ("Template version", context["template"]["version"]),
        ("Template SHA-256", context["template"]["hash"]),
        ("Provenance contract", provenance["contract"]),
    ]
    rows = "".join(
        f"<tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in source_rows
    )
    return (
        '<section class="source-provenance"><h2>Source provenance</h2>'
        f"<table><tbody>{rows}</tbody></table></section>"
        f"{context['form']['readable_html']}"
        f"{context['medications_readable_html']}"
    )


def _validate_template_ast(parsed):
    template_nodes = list(parsed.find_all(nodes.Node))
    if len(template_nodes) > MAX_TEMPLATE_AST_NODES:
        raise CorrespondenceCompilationError(
            "Correspondence template exceeds the complexity limit"
        )
    if any(isinstance(node, FORBIDDEN_TEMPLATE_NODES) for node in template_nodes):
        raise CorrespondenceCompilationError(
            "Correspondence template uses an unbounded or unsupported construct"
        )


def _recorded(value):
    return value if value else "Not recorded in CARE"


def _render_value(value: Any) -> str:
    if isinstance(value, dict):
        rows = "".join(
            f"<tr><th>{escape(key)}</th><td>{_render_value(value[key])}</td></tr>"
            for key in sorted(value)
        )
        return f"<table><tbody>{rows}</tbody></table>"
    if isinstance(value, list):
        if not value:
            return "<span>—</span>"
        items = "".join(f"<li>{_render_value(item)}</li>" for item in value)
        return f"<ol>{items}</ol>"
    if value is None or value == "":
        return "<span>—</span>"
    if isinstance(value, bool):
        return "<span>Yes</span>" if value else "<span>No</span>"
    return f"<span>{escape(str(value))}</span>"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return "\n".join(
        line
        for line in (re.sub(r"\s+", " ", line).strip() for line in parser.lines)
        if line
    )


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "hr",
        "li",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = [""]

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS and self.lines[-1]:
            self.lines.append("")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS and self.lines[-1]:
            self.lines.append("")

    def handle_data(self, data):
        self.lines[-1] += data


class _StrictHTMLSanitizer(HTMLParser):
    ALLOWED_TAGS = {
        "article",
        "b",
        "br",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "section",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
    VOID_TAGS = {"br", "hr"}
    ALLOWED_ATTRIBUTES = {"class"}
    CLASS_PATTERN = re.compile(r"^[a-zA-Z0-9 _-]{1,200}$")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []

    @property
    def output(self):
        if self.stack:
            raise CorrespondenceSanitizationError(
                "Correspondence HTML contains unclosed elements"
            )
        return "".join(self.parts)

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED_TAGS:
            raise CorrespondenceSanitizationError(
                "Correspondence HTML contains a prohibited element"
            )
        normalized = []
        for name, value in attrs:
            if name not in self.ALLOWED_ATTRIBUTES:
                raise CorrespondenceSanitizationError(
                    "Correspondence HTML contains a prohibited attribute"
                )
            if not value or not self.CLASS_PATTERN.fullmatch(value):
                raise CorrespondenceSanitizationError(
                    "Correspondence HTML contains an invalid attribute"
                )
            normalized.append((name, value))
        attributes = "".join(
            f' {name}="{escape(value, quote=True)}"'
            for name, value in sorted(normalized)
        )
        self.parts.append(f"<{tag}{attributes}>")
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            raise CorrespondenceSanitizationError(
                "Correspondence HTML contains mismatched elements"
            )
        self.stack.pop()
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(escape(data))

    def handle_comment(self, data):
        raise CorrespondenceSanitizationError(
            "Correspondence HTML comments are not allowed"
        )

    def handle_decl(self, decl):
        raise CorrespondenceSanitizationError(
            "Correspondence HTML declarations are not allowed"
        )

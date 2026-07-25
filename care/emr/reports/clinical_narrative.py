import re

_HISTORY_HEADINGS = {
    "algemene voorgeschiedenis:",
    "urologische voorgeschiedenis:",
}
_EM_DASH = "\N{EM DASH}"
_EN_DASH = "\N{EN DASH}"
_SECTION_HEADING = re.compile(r"^[^:\n]{1,80}:$")
_INLINE_HISTORY_DETAIL = re.compile(
    rf"^(?P<prefix>\s*-\s+)(?P<label>.+?)\s+[{_EM_DASH}{_EN_DASH}]"
    r"\s+(?P<detail>\S.*)$"
)


def normalize_diagnosis_history_layout(narrative: str) -> str:
    """Format generated diagnosis history without changing other clinical prose."""

    lines = str(narrative or "").replace("\r\n", "\n").split("\n")
    normalized: list[str] = []
    in_history = False
    detail_entry_open = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        folded = stripped.casefold()
        if folded in _HISTORY_HEADINGS:
            in_history = True
            detail_entry_open = False
            normalized.append(line)
            continue
        if (
            in_history
            and stripped
            and not stripped.startswith("-")
            and _SECTION_HEADING.fullmatch(stripped)
        ):
            in_history = False
            detail_entry_open = False

        if not in_history or not stripped:
            normalized.append(line)
            if not stripped:
                detail_entry_open = False
            continue

        inline_detail = _INLINE_HISTORY_DETAIL.fullmatch(line)
        if inline_detail:
            label = inline_detail.group("label").rstrip().removesuffix(":")
            detail = _replace_history_dashes(inline_detail.group("detail"))
            normalized.extend(
                [
                    f"{inline_detail.group('prefix')}{label}:",
                    f"  {detail}",
                ]
            )
            detail_entry_open = True
            continue

        if stripped.startswith("-"):
            has_detail = _next_line_is_detail(lines, index)
            bullet = line.rstrip()
            if has_detail and not bullet.endswith(":"):
                bullet = f"{bullet}:"
            normalized.append(bullet)
            detail_entry_open = has_detail
            continue

        if detail_entry_open:
            normalized.append(f"  {_replace_history_dashes(stripped)}")
            continue

        normalized.append(line)

    return "\n".join(normalized)


def _next_line_is_detail(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    following = lines[index + 1].strip()
    return bool(
        following
        and not following.startswith("-")
        and not _SECTION_HEADING.fullmatch(following)
    )


def _replace_history_dashes(value: str) -> str:
    return value.replace(f" {_EM_DASH} ", ": ").replace(f" {_EN_DASH} ", ": ")

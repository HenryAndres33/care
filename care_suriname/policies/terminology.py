"""Approved Dutch expansion overlay; native search remains the source fallback."""

from care_suriname.resources.clinical_term_translation import (
    resolve_concepts,
    search_approved_translation_concepts,
)

LOCAL_TRANSLATION_CONCEPT_KINDS = {
    "system-condition-code": "condition",
    "activity-definition-procedure-code": "procedure",
}


def expand(valueset, request_params):
    requested_language = request_params["display_language"]
    use_local_dutch = requested_language.casefold() in {"nl", "nl-sr"}
    if use_local_dutch:
        request_params["display_language"] = "en-gb"
    results = [result.model_dump() for result in valueset.search(**request_params)]
    if not use_local_dutch:
        return results

    language = "nl-SR"
    translated_matches = []
    concept_kind = LOCAL_TRANSLATION_CONCEPT_KINDS.get(valueset.slug)
    if concept_kind:
        translated_matches = search_approved_translation_concepts(
            request_params["search"],
            language,
            request_params["count"],
            concept_kind,
        )
    resolved_results = resolve_concepts(results, language=language)
    merged = []
    seen = set()
    for item in [*translated_matches, *resolved_results]:
        key = (item["system"], item["code"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= request_params["count"]:
            break
    return merged

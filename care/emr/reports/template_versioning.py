import hashlib
import json


def calculate_template_content_hash(template) -> str:
    canonical = {
        "context": template.context,
        "default_format": template.default_format,
        "name": template.name,
        "options": template.options,
        "slug": template.slug,
        "template_data": template.template_data,
        "template_type": template.template_type,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()

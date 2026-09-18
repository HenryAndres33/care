from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def clinical_workflow_deployment_checks(app_configs, **kwargs):
    del app_configs, kwargs
    if not getattr(settings, "IS_PRODUCTION", False):
        return []

    errors = []
    if settings.SECRET_KEY == settings.INSECURE_DEFAULT_SECRET_KEY:
        errors.append(
            Error(
                "Production must provide a non-default DJANGO_SECRET_KEY.",
                id="care.E001",
            )
        )
    if not settings.ALLOWED_HOSTS or "*" in settings.ALLOWED_HOSTS:
        errors.append(
            Error(
                "Production must configure explicit DJANGO_ALLOWED_HOSTS.",
                id="care.E002",
            )
        )
    if not settings.CSRF_TRUSTED_ORIGINS:
        errors.append(
            Error(
                "Production must configure explicit CSRF_TRUSTED_ORIGINS.",
                id="care.E003",
            )
        )
    if not getattr(settings, "CORS_ALLOWED_ORIGINS", []):
        errors.append(
            Error(
                "Production must configure explicit CORS_ALLOWED_ORIGINS.",
                id="care.E004",
            )
        )
    for setting_name, error_id in [
        ("CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES", "care.E005"),
        ("CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES", "care.E006"),
    ]:
        if "*" in getattr(settings, setting_name, []):
            errors.append(
                Error(
                    f"Production {setting_name} must use explicit facility UUIDs.",
                    id=error_id,
                )
            )
    if settings.AUDIT_LOG_ENABLED and not getattr(
        settings, "AUDIT_LOG_DOMAIN_LEDGER_MODE", False
    ):
        errors.append(
            Error(
                "Generic audit logging requires clinical domain-ledger mode.",
                id="care.E007",
            )
        )
    return errors

from importlib import import_module

from django.apps import AppConfig


class CareSurinameConfig(AppConfig):
    name = "care_suriname"
    verbose_name = "CARE Suriname"

    def ready(self):
        # Side-effect modules, loaded explicitly (a plain `import` here is an
        # "unused import" to linters and was once auto-removed).
        # Deploy-time system checks E001 to E007 (django.core.checks registry).
        import_module("care_suriname.checks")
        # Encounter extension: admission note (ExtensionRegistry.register at import).
        import_module("care_suriname.extensions.encounter_admission_note")

        from care_suriname.authorization import register_authorization_handlers

        register_authorization_handlers()

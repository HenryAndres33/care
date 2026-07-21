from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class CeleryDevSettingsTest(SimpleTestCase):
    def test_dev_worker_defaults_to_local_settings_before_startup_commands(self):
        script = (Path(settings.BASE_DIR) / "scripts" / "celery-dev.sh").read_text()
        default = (
            'export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-'
            'config.settings.local}"'
        )

        self.assertIn(default, script)
        self.assertLess(script.index(default), script.index("python manage.py migrate"))
        self.assertLess(script.index(default), script.index("celery --workdir"))

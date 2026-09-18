from plugs.manager import PlugManager
from plugs.plug import Plug  # noqa: F401

plugs = []

# CARE Suriname: the `care_suriname` Django app lives in this repository and is
# loaded as a plug (INSTALLED_APPS, `api/care_suriname/`, and via the seam in
# config/urls.py its `v1_urls`). It needs no pip install, so it is added to the
# app list rather than to `plugs`. See docs/development/plug-app.md.
LOCAL_PLUG_APPS = ["care_suriname"]


class LocalPlugManager(PlugManager):
    def get_apps(self) -> list[str]:
        return [*super().get_apps(), *LOCAL_PLUG_APPS]


manager = LocalPlugManager(plugs)

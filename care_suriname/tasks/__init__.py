"""Periodic correspondence scans, formerly registered in care/emr/tasks/__init__.py.

Celery autodiscovers this module through INSTALLED_APPS. Importing the task
modules here also registers the tasks themselves with the worker.
"""

from celery import Celery, current_app

from care_suriname.tasks.correspondence_correction import (
    scan_correspondence_correction_delivery_cases,
    scan_correspondence_correction_outbox,
    scan_correspondence_replacement_delivery_cases,
)
from care_suriname.tasks.correspondence_delivery import (
    scan_correspondence_delivery_outbox,
)


@current_app.on_after_finalize.connect
def setup_care_suriname_periodic_tasks(sender: Celery, **kwargs):
    for task in (
        scan_correspondence_delivery_outbox,
        scan_correspondence_correction_outbox,
        scan_correspondence_correction_delivery_cases,
        scan_correspondence_replacement_delivery_cases,
    ):
        sender.add_periodic_task(60, task.s(), name=task.__name__)

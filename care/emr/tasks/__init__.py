from celery import Celery, current_app
from celery.schedules import crontab
from django.conf import settings

from care.emr.tasks.cleanup_expired_token_slots import cleanup_expired_token_slots
from care.emr.tasks.cleanup_incomplete_file_uploads import (
    cleanup_incomplete_file_uploads,
)
from care.emr.tasks.correspondence_correction import (
    scan_correspondence_correction_delivery_cases,
    scan_correspondence_correction_outbox,
    scan_correspondence_replacement_delivery_cases,
)
from care.emr.tasks.correspondence_delivery import (
    scan_correspondence_delivery_outbox,
)


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        crontab(hour="0", minute="0"),
        cleanup_expired_token_slots.s(),
        name="cleanup_expired_token_slots",
    )

    if cleanup_file_upload_hours := settings.FILE_UPLOAD_EXPIRY_HOURS:
        sender.add_periodic_task(
            cleanup_file_upload_hours * 3600,  # convert hours to seconds
            cleanup_incomplete_file_uploads.s(),
            name="cleanup_incomplete_file_uploads",
        )

    sender.add_periodic_task(
        60,
        scan_correspondence_delivery_outbox.s(),
        name="scan_correspondence_delivery_outbox",
    )
    sender.add_periodic_task(
        60,
        scan_correspondence_correction_outbox.s(),
        name="scan_correspondence_correction_outbox",
    )
    sender.add_periodic_task(
        60,
        scan_correspondence_correction_delivery_cases.s(),
        name="scan_correspondence_correction_delivery_cases",
    )
    sender.add_periodic_task(
        60,
        scan_correspondence_replacement_delivery_cases.s(),
        name="scan_correspondence_replacement_delivery_cases",
    )

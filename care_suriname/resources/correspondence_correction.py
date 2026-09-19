from care_suriname.resources.correspondence import canonical_sha256


def form_submission_series_head_hash(head) -> str:
    return form_submission_series_head_snapshot_hash(
        series_id=head.series_id,
        current_submission_id=head.current_submission.external_id,
        current_version=head.current_version,
        current_snapshot_hash=head.current_snapshot_hash,
        advanced_at=head.advanced_at,
        advanced_by_id=head.advanced_by.external_id,
    )


def form_submission_series_head_snapshot_hash(
    *,
    series_id,
    current_submission_id,
    current_version,
    current_snapshot_hash,
    advanced_at,
    advanced_by_id,
) -> str:
    return canonical_sha256(
        {
            "advanced_at": advanced_at,
            "advanced_by": advanced_by_id,
            "contract": "form-submission-series-head-v1",
            "current_snapshot_hash": current_snapshot_hash,
            "current_submission": current_submission_id,
            "current_version": current_version,
            "series_id": series_id,
        }
    )


def correspondence_source_correction_hash(correction) -> str:
    return canonical_sha256(
        {
            "amendment_type": correction.amendment_type,
            "contract": "correspondence-source-correction-v1",
            "corrected_at": correction.corrected_at,
            "corrected_by": correction.corrected_by.external_id,
            "new_snapshot_hash": correction.new_snapshot_hash,
            "new_submission": correction.new_submission.external_id,
            "new_version": correction.new_version,
            "previous_snapshot_hash": correction.previous_snapshot_hash,
            "previous_submission": correction.previous_submission.external_id,
            "previous_version": correction.previous_version,
            "reason": correction.reason,
            "sequence": correction.sequence,
            "series_id": correction.source_head.series_id,
            "source_head_hash": correction.source_head_hash,
        }
    )

class _CommandConflictError(Exception):
    def __init__(self, response):
        super().__init__()
        self.response = response


class _ArtifactValidationError(Exception):
    pass


class _ArtifactStaleSourceError(Exception):
    pass


def _constraint_name(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)

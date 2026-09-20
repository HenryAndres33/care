class LaboratoryCommandError(Exception):
    def __init__(self, error_type: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.status_code = status_code


def conflict(error_type: str, message: str) -> LaboratoryCommandError:
    return LaboratoryCommandError(error_type, message, status_code=409)


def invalid_result(message: str) -> LaboratoryCommandError:
    return LaboratoryCommandError(
        "laboratory_result_invalid",
        message,
        status_code=422,
    )

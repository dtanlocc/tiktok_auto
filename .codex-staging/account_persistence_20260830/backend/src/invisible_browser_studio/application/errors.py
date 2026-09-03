class ApplicationError(RuntimeError):
    code = "application_error"


class SessionNotFound(ApplicationError):
    code = "session_not_found"


class BatchNotFound(ApplicationError):
    code = "batch_not_found"


class SignupTestNotFound(ApplicationError):
    code = "signup_test_not_found"


class SignupTestAlreadyConsumed(ApplicationError):
    code = "signup_test_already_consumed"


class SessionConflict(ApplicationError):
    code = "session_conflict"


class CapacityExceeded(ApplicationError):
    code = "capacity_exceeded"


class RuntimeOperationFailed(ApplicationError):
    code = "runtime_operation_failed"

"""App-wide exceptions mapped to the error shape in docs/API.md (Conventions):
{"detail": "...", "code": "machine_code"}. Raised from services, translated to
HTTP responses by the handlers registered in app.main.
"""


class AppError(Exception):
    status_code: int = 400
    code: str = "app_error"

    def __init__(self, detail: str, code: str | None = None) -> None:
        self.detail = detail
        if code:
            self.code = code
        super().__init__(detail)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"

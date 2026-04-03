from __future__ import annotations

from enum import StrEnum

import httpx


class APIErrorCode(StrEnum):
    ValidationError = "ValidationError"
    RateLimited = "RateLimited"
    Unauthorized = "Unauthorized"
    ObjectNotFound = "ObjectNotFound"


class APIResponseError(Exception):
    def __init__(
        self,
        *,
        code: APIErrorCode | str | None,
        status: int,
        message: str,
        headers: httpx.Headers | None = None,
        raw_body_text: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.headers = headers or httpx.Headers({})
        self.raw_body_text = raw_body_text


class HTTPResponseError(Exception):
    pass


class RequestTimeoutError(Exception):
    pass

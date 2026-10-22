from __future__ import annotations

from typing import Protocol


class ReadableResponse(Protocol):
    def read(self, amount: int = -1) -> bytes: ...


class ResponseTooLargeError(RuntimeError):
    pass


def read_bounded_response(response: ReadableResponse, *, max_bytes: int) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ResponseTooLargeError("external response exceeded safe size")
    return raw

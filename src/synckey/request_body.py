"""Bound request buffering before parsing or forwarding a payload."""

from fastapi import Request
from starlette.requests import ClientDisconnect


class InvalidBody(ValueError):
    pass


class BodyTooLarge(InvalidBody):
    pass


async def read_body(request: Request, limit: int) -> bytes:
    lengths = request.headers.getlist("content-length")
    if lengths:
        declared = lengths[0]
        if len(lengths) != 1 or not declared.isascii() or not declared.isdecimal():
            raise InvalidBody("Invalid Content-Length.")
        declared = declared.lstrip("0") or "0"
        if len(declared) > len(str(limit)) or int(declared) > limit:
            raise BodyTooLarge(f"Request body exceeds {limit} bytes.")

    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                raise BodyTooLarge(f"Request body exceeds {limit} bytes.")
            body.extend(chunk)
    except ClientDisconnect as exc:
        raise InvalidBody("Incomplete request body.") from exc
    return bytes(body)

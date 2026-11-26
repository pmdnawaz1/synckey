"""Speech-to-text through the key pool: ``POST /v1/audio/transcriptions``.

The multipart body stays in memory. Routing can rewrite the model field
while preserving the file and any provider-specific form fields.
"""

from __future__ import annotations

import re
import time
from email.message import Message
from email.parser import BytesHeaderParser
from io import BytesIO

import httpx
import orjson
from fastapi import Request, Response
from starlette.concurrency import run_in_threadpool
from tinytag import TinyTag, TinyTagException

from .context import Context
from .providers import split_provider_prefix
from .request_body import BodyTooLarge, InvalidBody, read_body
from .router import Resolution, order_by_priority

MAX_BYTES = 26 * 1024 * 1024

_BOUNDARY = re.compile(r"[A-Za-z0-9'()+_,./:=? -]{1,70}")
_HEADER_LIMIT = 8 * 1024
_HEADERS = BytesHeaderParser()


def boundary_of(content_type: str) -> bytes | None:
    headers = Message()
    headers["Content-Type"] = content_type
    boundary = headers.get_boundary()
    if headers.get_content_type() != "multipart/form-data" or not boundary:
        return None
    return boundary.encode("ascii") if _BOUNDARY.fullmatch(boundary) else None


def parse_upload(body: bytes, boundary: bytes) -> tuple[str, slice, memoryview]:
    delimiter = re.compile(rb"(?:\A|\r\n)--" + re.escape(boundary) + rb"(--)?[ \t]*(?:\r\n|\Z)")
    start = None
    model = None
    model_span = None
    audio = None
    for match in delimiter.finditer(body):
        if start is not None:
            end = body.find(b"\r\n\r\n", start, min(match.start(), start + _HEADER_LIMIT))
            if end == -1:
                raise ValueError("Invalid multipart headers.")
            headers = _HEADERS.parsebytes(body[start:end])
            name = headers.get_param("name", header="content-disposition")
            if (
                headers.defects
                or len(headers.get_all("content-disposition", [])) != 1
                or headers.get_content_disposition() != "form-data"
                or not name
            ):
                raise ValueError("Each multipart part must have a form-data name.")
            value_start = end + 4
            if name == "model":
                value_length = match.start() - value_start
                if model is not None or headers.get_filename() is not None or value_length > 256:
                    raise ValueError("Send one valid 'model' field.")
                model_span = slice(value_start, match.start())
                model = body[model_span].decode("utf-8")
                if not model or not model.isprintable() or model != model.strip():
                    raise ValueError("Send one valid 'model' field.")
            elif name == "file":
                if audio is not None or not headers.get_filename() or value_start >= match.start():
                    raise ValueError("Send one non-empty 'file' upload.")
                audio = memoryview(body)[value_start:match.start()]

        if match.group(1):
            if model is None or model_span is None or audio is None:
                raise ValueError("Both 'file' and 'model' are required.")
            return model, model_span, audio
        start = match.end()
    raise ValueError("Multipart body is missing its closing boundary.")


def resolve_transcription(ctx: Context, model: str, provider_hint: str | None) -> Resolution:
    model = ctx.router.materialize(model) or model
    prefix, bare_model = split_provider_prefix(model, set(ctx.providers))
    if provider_hint is not None:
        provider_id = provider_hint.strip().lower()
        if prefix == provider_id:
            model = bare_model
    else:
        provider_id = prefix
        if prefix:
            model = bare_model

    if not model or not model.isprintable() or model != model.strip():
        raise ValueError("Send a valid transcription model ID.")
    if provider_id is not None:
        provider = ctx.providers.get(provider_id)
        if not provider or not provider.transcription_path:
            raise ValueError("The selected provider does not support transcription.")
        return Resolution(model, [provider_id], "provider")

    providers = [pid for pid, provider in ctx.providers.items() if provider.matches_transcription(model)]
    ordered = order_by_priority(providers, ctx.settings.provider_priority)
    return Resolution(model, ordered, "transcription-model")


def valid_duration(value: object) -> float | None:
    if type(value) in (int, float) and 0 < value <= 2**53:
        return float(value)
    return None


def transcription_duration(resp: httpx.Response, audio: memoryview) -> float | None:
    if resp.headers.get("content-type", "").split(";", 1)[0].lower() == "application/json":
        try:
            payload = orjson.loads(resp.content)
        except orjson.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            reported = valid_duration(payload.get("duration"))
            if reported is not None:
                return reported
            usage = payload.get("usage")
            if isinstance(usage, dict):
                reported = valid_duration(usage.get("seconds"))
                if reported is not None:
                    return reported
    try:
        duration = TinyTag.get(file_obj=BytesIO(audio), tags=False).duration
    except TinyTagException:
        return None
    return valid_duration(duration)


async def transcriptions(ctx: Context, request: Request) -> Response:
    from .server import (
        authorized,
        available_attempts,
        classify_failure,
        cooling_error,
        cooling_retry_after,
        err,
        plan_attempts,
        unauthorized,
    )

    if not authorized(ctx, request):
        return unauthorized()
    content_type = request.headers.get("content-type", "")
    boundary = boundary_of(content_type)
    if boundary is None:
        return err("Send multipart/form-data with a valid boundary.", "invalid_request_error", 400)
    try:
        body = await read_body(request, MAX_BYTES)
    except BodyTooLarge as exc:
        return err(str(exc), "request_too_large", 413)
    except InvalidBody as exc:
        return err(str(exc), "invalid_request_error", 400)
    try:
        requested_model, model_span, audio = parse_upload(body, boundary)
        resolution = resolve_transcription(ctx, requested_model, request.headers.get("x-provider"))
    except UnicodeDecodeError:
        return err("The 'model' field must be UTF-8.", "invalid_request_error", 400)
    except ValueError as exc:
        return err(str(exc), "invalid_request_error", 400)

    if not resolution.providers:
        return err(
            "Unknown transcription model. Select a transcription provider with X-Provider or a provider/model prefix.",
            "not_found_error",
            404,
        )
    model = resolution.bare_model
    if model != requested_model:
        original = memoryview(body)
        body = b"".join((original[:model_span.start], model.encode("utf-8"), original[model_span.stop:]))
    attempts = plan_attempts(ctx, resolution, None)
    if not attempts:
        return err(
            "No usable key for this transcription route. Check `synckey keys`.",
            "no_available_keys",
            503,
        )

    retry_after = cooling_retry_after(ctx, attempts)
    if retry_after is not None:
        return cooling_error(retry_after)

    client: httpx.AsyncClient = request.app.state.client
    writer = request.app.state.writer
    last_err, last_status = "unknown", 502
    count = 0
    for count, attempt in enumerate(available_attempts(ctx, attempts), start=1):
        path = attempt.provider.transcription_path
        assert path is not None  # Only transcription providers enter the attempt plan.
        url = f"{attempt.provider.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Content-Type": content_type,
            **attempt.provider.auth_headers(attempt.secret),
        }
        ctx.pool.consume(attempt.key_id, 0)
        started = time.perf_counter()
        try:
            resp = await client.post(
                url, content=body, headers=headers, params=attempt.provider.auth_params(attempt.secret)
            )
        except httpx.HTTPError:
            ctx.pool.on_failure(attempt.key_id)
            last_err, last_status = "Upstream connection failed.", 502
            continue
        latency = int((time.perf_counter() - started) * 1000)
        media = resp.headers.get("content-type", "application/json")
        if resp.is_success:
            ctx.pool.on_success(attempt.key_id)
            writer.record(
                provider=attempt.provider_id,
                model=model,
                key_id=attempt.key_id,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                audio_seconds=await run_in_threadpool(transcription_duration, resp, audio),
                status_code=resp.status_code,
                latency_ms=latency,
                tier=resolution.tier,
            )
            return Response(content=resp.content, media_type=media, status_code=resp.status_code)
        retriable, last_err, last_status = classify_failure(
            ctx, writer, attempt, resp, resolution, model, latency,
            error_message=f"HTTP {resp.status_code} from {attempt.provider_id}.",
            mark_model_unavailable=False,
        )
        if not retriable:
            return Response(content=resp.content, media_type=media, status_code=resp.status_code)

    return err(
        f"All {count} transcription attempts failed. Last: {last_err}",
        "upstream_exhausted",
        last_status if last_status >= 400 else 502,
        retry_after=cooling_retry_after(ctx, attempts),
    )

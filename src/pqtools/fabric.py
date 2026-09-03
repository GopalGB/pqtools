"""Mockable, caller-authenticated Fabric Execute Query state machine."""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from .core import MAX_BYTES, AdapterError

Response = Mapping[str, Any]
Transport = Callable[
    [str, Mapping[str, str], Mapping[str, Any] | None, float], Response
]
ArrowValidator = Callable[[bytes], None]
_OPERATION_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")


def _same_origin(start: str, candidate: str) -> bool:
    left, right = urlsplit(start), urlsplit(candidate)

    def origin(parts: Any) -> tuple[str, str | None, int | None]:
        host = parts.hostname
        return (parts.scheme.lower(), host.lower() if host else host, parts.port)

    return origin(left) == origin(right)


def _validate_arrow(body: bytes) -> None:
    if len(body) > MAX_BYTES:
        raise AdapterError("Fabric Arrow stream exceeds 10 MiB")
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ImportError as error:
        raise AdapterError(
            "Arrow validation requires the pqtools[fabric] extra"
        ) from error
    stream = io.BytesIO(body)
    streams = 0
    total_bytes = 0
    try:
        while stream.tell() < len(body):
            start = stream.tell()
            reader = pa.ipc.open_stream(stream)
            metadata = {
                key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
                for key, value in (reader.schema.metadata or {}).items()
            }
            if metadata.get("IsError", "").lower() == "true":
                code = metadata.get("FaultCode", "unknown")
                message = metadata.get("FaultString", "Fabric query failed")
                raise AdapterError(f"Fabric Arrow error [{code}]: {message}")
            while True:
                try:
                    batch = reader.read_next_batch()
                except StopIteration:
                    break
                total_bytes += batch.nbytes
                if total_bytes > MAX_BYTES:
                    raise AdapterError("Fabric Arrow stream exceeds 10 MiB")
            streams += 1
            if stream.tell() <= start:
                raise AdapterError("Fabric Arrow decoder made no progress")
    except AdapterError:
        raise
    except Exception as error:
        raise AdapterError("Fabric returned an invalid Arrow stream") from error
    if streams == 0:
        raise AdapterError("Fabric returned an empty Arrow stream")


def _headers(response: Response) -> Mapping[str, Any]:
    return {
        str(key).lower(): value for key, value in response.get("headers", {}).items()
    }


@dataclass(frozen=True)
class FabricClient:
    transport: Transport
    timeout_seconds: int = 90
    sleeper: Callable[[float], None] = sleep
    clock: Callable[[], float] = monotonic
    arrow_validator: ArrowValidator = _validate_arrow

    def _request(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
        deadline: float,
    ) -> Response:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise AdapterError("Fabric operation exceeded caller timeout")
        try:
            return self.transport(url, headers, payload, remaining)
        except TimeoutError as error:
            raise AdapterError("Fabric transport timed out") from error

    @staticmethod
    def _require_https_same_origin(start: str, candidate: str) -> None:
        if urlsplit(candidate).scheme != "https" or not _same_origin(start, candidate):
            raise AdapterError(
                "Fabric operation Location must remain HTTPS and same-origin"
            )

    @staticmethod
    def _retry_seconds(headers: Mapping[str, Any]) -> int:
        retry_raw = headers.get("retry-after", 1)
        try:
            return max(1, int(str(retry_raw)))
        except ValueError as error:
            raise AdapterError("Fabric Retry-After must be whole seconds") from error

    @staticmethod
    def _poll_url(start: str, location: str, operation_id: str) -> str:
        candidate = urljoin(start, location) if location else ""
        if not candidate:
            if not _OPERATION_ID.fullmatch(operation_id):
                raise AdapterError("Fabric operation id is missing or invalid")
            origin = urlsplit(start)
            candidate = (
                f"{origin.scheme}://{origin.netloc}/v1/operations/"
                f"{quote(operation_id, safe='')}"
            )
        FabricClient._require_https_same_origin(start, candidate)
        return candidate

    def execute(self, url: str, token: str, payload: Mapping[str, Any]) -> Response:
        if not token:
            raise AdapterError("Fabric caller token is required")
        if urlsplit(url).scheme != "https":
            raise AdapterError("Fabric URL must use HTTPS")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        deadline = self.clock() + self.timeout_seconds
        request_url: str = url
        request_payload: Mapping[str, Any] | None = payload
        operation_id: str | None = None
        result_requested = False
        while True:
            if self.clock() > deadline:
                raise AdapterError("Fabric operation exceeded caller timeout")
            response = self._request(request_url, headers, request_payload, deadline)
            status = response.get("status")
            response_headers = _headers(response)
            if status == 200:
                content_type = str(response_headers.get("content-type", "")).lower()
                body = response.get("body")
                if "json" in content_type and isinstance(body, Mapping):
                    state = str(body.get("status", ""))
                    if state in {"Running", "NotStarted"}:
                        request_payload = None
                        retry = self._retry_seconds(response_headers)
                        self.sleeper(min(retry, max(0, deadline - self.clock())))
                        continue
                    if state == "Failed":
                        raise AdapterError("Fabric operation reported Failed")
                    if state == "Succeeded":
                        if request_url == url:
                            raise AdapterError(
                                "Fabric returned a JSON status instead of an "
                                "Arrow stream"
                            )
                        if result_requested:
                            raise AdapterError(
                                "Fabric result endpoint returned JSON instead of Arrow"
                            )
                        parts = urlsplit(request_url)
                        result_url = urlunsplit(
                            parts._replace(path=parts.path.rstrip("/") + "/result")
                        )
                        self._require_https_same_origin(url, result_url)
                        request_url = result_url
                        request_payload = None
                        result_requested = True
                        continue
                if (
                    "arrow" not in content_type
                    or not isinstance(body, bytes)
                    or not body
                ):
                    raise AdapterError(
                        "Fabric 200 response is not a non-empty Arrow stream"
                    )
                self.arrow_validator(body)
                return response
            if status == 429:
                retry = self._retry_seconds(response_headers)
                self.sleeper(min(retry, max(0, deadline - self.clock())))
                continue
            if status != 202:
                shown = status if status is not None else "unknown"
                raise AdapterError(f"Fabric returned HTTP {shown}")
            retry = self._retry_seconds(response_headers)
            location = str(response_headers.get("location", ""))
            next_operation = str(response_headers.get("x-ms-operation-id", ""))
            if not location and not next_operation:
                raise AdapterError(
                    "Fabric 202 response requires Location or x-ms-operation-id"
                )
            if (
                operation_id is not None
                and next_operation
                and operation_id != next_operation
            ):
                raise AdapterError("Fabric operation id changed during polling")
            operation_id = next_operation or operation_id
            request_url = self._poll_url(url, location, operation_id or "")
            request_payload = None
            result_requested = False
            self.sleeper(min(retry, max(0, deadline - self.clock())))

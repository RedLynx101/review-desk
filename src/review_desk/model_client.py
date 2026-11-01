"""Talk to an OpenAI-compatible server, asking for strict JSON.

This is the only network code in the app. It uses the standard library's `http.client`, which reads
no proxy settings and follows no redirects, so a document can't end up anywhere except the endpoint
the operator configured.
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from .config import Settings

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class ModelError(Exception):
    """The model couldn't give a usable answer. `code` is stable enough to count in reports."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class Transport(Protocol):
    """Sends one request. Raises OSError (including TimeoutError) if it can't complete."""

    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse: ...


class HttpTransport:
    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse:
        parts = urlsplit(url)
        connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parts.hostname, parts.port, timeout=timeout)
        try:
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return HttpResponse(response.status, response.read())
        except http.client.HTTPException as error:
            raise OSError(str(error)) from error
        finally:
            connection.close()


@dataclass(frozen=True)
class ModelReply:
    data: dict
    raw_text: str
    seconds: float
    attempts: int
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a reply, dropping any thinking text around it.

    A strict schema normally yields bare JSON. Models swapped in by a client may still wrap it in
    <think> blocks or a "Here's a thinking process" preamble, and the answer is then the last
    complete top-level object.
    """
    cleaned = _THINK_BLOCK.sub("", text).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    found = None
    index = cleaned.find("{")
    while index != -1:
        try:
            parsed, end = decoder.raw_decode(cleaned, index)
        except json.JSONDecodeError:
            index = cleaned.find("{", index + 1)
            continue
        if isinstance(parsed, dict):
            found = parsed
        index = cleaned.find("{", end)
    if found is None:
        raise ModelError("invalid_json", "The model's reply had no complete JSON object.")
    return found


class ModelClient:
    def __init__(self, settings: Settings, transport: Transport | None = None):
        self.settings = settings
        self._transport = transport or HttpTransport()

    def close(self) -> None:
        """Connections don't outlive a request, so there's nothing to release."""

    def __enter__(self) -> "ModelClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def complete_json(self, system: str, user: str, schema: dict, *, images: list[bytes] | None = None) -> ModelReply:
        """Ask for one schema-valid JSON object, retrying failures that can pass on their own."""
        content: str | list[dict] = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}}
                for image in images
            ]
        body = {
            "model": self.settings.model_name,
            "temperature": self.settings.model_temperature,
            "max_tokens": self.settings.model_max_tokens,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "strict": True, "schema": schema},
            },
        }
        last_error: ModelError | None = None
        started = time.monotonic()
        for attempt in range(1, self.settings.model_retries + 1):
            try:
                return self._once(json.dumps(body).encode("utf-8"), started, attempt)
            except ModelError as error:
                last_error = error
                # A reply cut off at max_tokens fails the same way every time at temperature 0.
                if error.code in ("truncated", "bad_request", "unauthorized", "model_crashed"):
                    raise
                if attempt < self.settings.model_retries:
                    time.sleep(min(60.0, 5.0 * 2.0 ** attempt))
        raise last_error

    def _once(self, body: bytes, started: float, attempt: int) -> ModelReply:
        headers = {"Authorization": f"Bearer {self.settings.model_api_key}", "Content-Type": "application/json"}
        url = f"{self.settings.model_base_url}/chat/completions"
        try:
            response = self._transport.send("POST", url, headers, body, self.settings.model_timeout_seconds)
        except TimeoutError as error:
            raise ModelError("timeout", f"No answer from the model within {self.settings.model_timeout_seconds:.0f} seconds.") from error
        except OSError as error:
            raise ModelError("unreachable", f"Couldn't reach the model at {self.settings.model_base_url}.") from error
        if response.status in (401, 403):
            raise ModelError("unauthorized", "The model server refused the API key.")
        if response.status == 400:
            detail = response.body.decode("utf-8", errors="replace")[:300]
            # LM Studio reports a crashed model as a 400. Retrying at once only crashes it again.
            if re.search(r"crash", detail, re.IGNORECASE):
                raise ModelError("model_crashed", f"The model process crashed: {detail}")
            if re.search(r"not loaded|unloaded|no models? loaded|loading", detail, re.IGNORECASE):
                raise ModelError("server_error", f"The model server isn't ready: {detail}")
            raise ModelError("bad_request", f"The model server rejected the request: {detail}")
        if response.status >= 300:
            raise ModelError("server_error", f"The model server answered {response.status}.")
        try:
            payload = json.loads(response.body)
            choice = payload["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ModelError("bad_response", "The model server's response wasn't in the expected format.") from error
        # Some servers report thinking in its own field. It's never the answer.
        text = message.get("content") or ""
        finish = choice.get("finish_reason")
        if finish == "length":
            raise ModelError("truncated", "The model ran out of tokens before finishing its answer.")
        usage = payload.get("usage") or {}
        return ModelReply(
            data=extract_json(text),
            raw_text=text,
            seconds=time.monotonic() - started,
            attempts=attempt,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=finish,
        )

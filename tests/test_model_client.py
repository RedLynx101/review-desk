"""The model client: strict schema on the wire, thinking text handled, failures named."""

from __future__ import annotations

import json

import pytest

from review_desk.config import Settings
from review_desk.model_client import HttpResponse, ModelClient, ModelError, extract_json
from review_desk.pipeline import ask_model
from review_desk.schema import ModelReading, model_json_schema

from helpers import INVOICE_ROWS, StubTransport, document, invoice_reading, model_reply

SETTINGS = Settings(model_base_url="http://model.test/v1", model_name="some-model", model_api_key="k", model_retries=3)


def _client(handler, settings=SETTINGS):
    return ModelClient(settings, transport=StubTransport(handler))


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("review_desk.model_client.time.sleep", lambda _: None)


def test_request_carries_the_strict_schema_and_settings_from_config():
    seen = {}

    def handler(request):
        seen.update(request)
        return model_reply('{"a": 1}')

    with _client(handler) as client:
        reply = client.complete_json("sys", "user", {"type": "object"})
    assert seen["url"] == "http://model.test/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer k"
    body = seen["json"]
    assert body["model"] == "some-model" and body["temperature"] == 0.0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert reply.data == {"a": 1} and reply.prompt_tokens == 10


def test_images_are_sent_as_data_urls():
    seen = {}

    def handler(request):
        seen.update(request)
        return model_reply("{}")

    with _client(handler) as client:
        client.complete_json("s", "u", {"type": "object"}, images=[b"jpegbytes"])
    content = seen["json"]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "u"}
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('<think>hmm {"a": 9} wait</think>{"a": 1}', {"a": 1}),
    ("Here's a thinking process:\n1. read it\n\nFinal answer:\n{\"a\": 1}\n", {"a": 1}),
    ('draft {"a": 0} then final {"a": 1}', {"a": 1}),
    ('```json\n{"a": {"b": [1, 2]}}\n```', {"a": {"b": [1, 2]}}),
])
def test_thinking_text_is_dropped(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "no json here", '{"a": ', "[1, 2]"])
def test_no_object_is_an_error(text):
    with pytest.raises(ModelError) as caught:
        extract_json(text)
    assert caught.value.code == "invalid_json"


def test_a_separate_reasoning_field_is_ignored():
    with _client(lambda _: model_reply('{"a": 1}', reasoning_content='{"a": 2}')) as client:
        assert client.complete_json("s", "u", {"type": "object"}).data == {"a": 1}


def test_temporary_failures_are_retried_until_success():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return HttpResponse(503, b"")
        if len(calls) == 2:
            raise ConnectionError("refused")
        return model_reply('{"a": 1}')

    with _client(handler) as client:
        reply = client.complete_json("s", "u", {"type": "object"})
    assert len(calls) == 3 and reply.attempts == 3


def test_retries_stop_and_name_the_failure():
    with _client(lambda _: HttpResponse(500, b"")) as client:
        with pytest.raises(ModelError) as caught:
            client.complete_json("s", "u", {"type": "object"})
    assert caught.value.code == "server_error"


def test_a_slow_server_is_reported_as_a_timeout():
    def handler(request):
        raise TimeoutError("slow")

    with _client(handler) as client:
        with pytest.raises(ModelError) as caught:
            client.complete_json("s", "u", {"type": "object"})
    assert caught.value.code == "timeout"


def test_an_unreachable_server_is_named():
    def handler(request):
        raise ConnectionRefusedError("nope")

    with _client(handler) as client:
        with pytest.raises(ModelError) as caught:
            client.complete_json("s", "u", {"type": "object"})
    assert caught.value.code == "unreachable"


@pytest.mark.parametrize("status,code", [(400, "bad_request"), (401, "unauthorized"), (403, "unauthorized")])
def test_requests_that_cannot_succeed_are_not_retried(status, code):
    calls = []

    def handler(request):
        calls.append(1)
        return HttpResponse(status, b"no")

    with _client(handler) as client:
        with pytest.raises(ModelError) as caught:
            client.complete_json("s", "u", {"type": "object"})
    assert caught.value.code == code and len(calls) == 1


def test_a_crashed_model_is_named_and_not_hit_again_straight_away():
    calls = []

    def handler(request):
        calls.append(1)
        return HttpResponse(400, b'{"error":"The model has crashed without additional information."}')

    with _client(handler) as client:
        with pytest.raises(ModelError) as caught:
            client.complete_json("s", "u", {"type": "object"})
    assert caught.value.code == "model_crashed" and len(calls) == 1


def test_a_model_still_loading_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return HttpResponse(400, b'{"error":"No models loaded."}')
        return model_reply('{"a": 1}')

    with _client(handler) as client:
        assert client.complete_json("s", "u", {"type": "object"}).data == {"a": 1}
    assert len(calls) == 2


def test_a_truncated_answer_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return model_reply('{"a": ', finish="length")

    with _client(handler) as client:
        with pytest.raises(ModelError) as caught:
            client.complete_json("s", "u", {"type": "object"})
    assert caught.value.code == "truncated" and len(calls) == 1


def test_a_redirect_is_treated_as_a_failure_and_never_followed():
    urls = []

    def handler(request):
        urls.append(request["url"])
        return HttpResponse(302, b"")

    with _client(handler) as client:
        with pytest.raises(ModelError) as caught:
            client.complete_json("s", "u", {"type": "object"})
    assert caught.value.code == "server_error"
    assert set(urls) == {"http://model.test/v1/chat/completions"}


def test_settings_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVIEW_DESK_MODEL_BASE_URL", "http://example.test:9000/v1/")
    monkeypatch.setenv("REVIEW_DESK_MODEL", "client-model")
    monkeypatch.setenv("REVIEW_DESK_MODEL_API_KEY", "client-key")
    settings = Settings.from_env()
    assert settings.model_base_url == "http://example.test:9000/v1"
    assert settings.model_name == "client-model" and settings.model_api_key == "client-key"


def test_a_dotenv_file_is_read_and_real_environment_wins(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REVIEW_DESK_MODEL", raising=False)
    monkeypatch.setenv("REVIEW_DESK_MODEL_API_KEY", "from-environment")
    (tmp_path / ".env").write_text("# comment\nREVIEW_DESK_MODEL='from-file'\nREVIEW_DESK_MODEL_API_KEY=from-file\n", encoding="utf-8")
    settings = Settings.from_env()
    monkeypatch.delenv("REVIEW_DESK_MODEL", raising=False)
    assert settings.model_name == "from-file"
    assert settings.model_api_key == "from-environment"


def test_schema_is_flat_strict_and_complete():
    schema = model_json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and "title" not in text
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    item = schema["properties"]["line_items"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])
    assert schema["properties"]["document_type"]["enum"] == ["invoice", "receipt"]


def test_reading_rejects_extra_and_missing_fields():
    good = invoice_reading()
    ModelReading.model_validate(good)
    with pytest.raises(ValueError):
        ModelReading.model_validate({**good, "confidence": 0.99})
    without = dict(good)
    del without["total"]
    with pytest.raises(ValueError):
        ModelReading.model_validate(without)


def test_an_answer_outside_the_schema_becomes_a_named_failure():
    doc = document(INVOICE_ROWS)
    with _client(lambda _: model_reply('{"total": "5"}')) as client:
        read = ask_model(doc, client)
    assert read.reading is None and read.error_code == "schema_violation"


def test_a_failed_model_call_becomes_data_not_an_exception():
    with _client(lambda _: HttpResponse(500, b"")) as client:
        read = ask_model(document(INVOICE_ROWS), client)
    assert read.reading is None and read.error_code == "server_error"

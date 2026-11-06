"""Provider error text is stored and logged without credentials."""
import json
import logging

import httpx
import pytest

from src.llm.client import LLMClient, LLMError, LLMErrorKind
from src.llm.redact import redact_secrets
from src.runtime.retry import AttemptSink, RetryConfig, RetryPolicy
from tests.test_fix1_failure_reasons import run

KEY = "sk-fixturekey0123456789abcdef"


@pytest.mark.parametrize("text, expected", [
    ("Authorization: Bearer abc.def-123456", "Authorization: Bearer [redacted]"),
    ('{"authorization": "Bearer abcdefgh12"}', '{"authorization": "Bearer [redacted]"}'),
    ("headers={'Authorization': 'Basic dXNlcjpwYXNz'}", "headers={'Authorization': 'Basic [redacted]'}"),
    ("retry with bearer zzzzzzzzzzzz later", "retry with bearer [redacted] later"),
    ("GET /v1?api_key=abc123&x=1", "GET /v1?api_key=[redacted]&x=1"),
    ('{"x-api-key": "k-998877"}', '{"x-api-key": "[redacted]"}'),
    ("Incorrect API key provided: sk-proj-AbCdEf0123456789xyz.", "Incorrect API key provided: [redacted]."),
])
def test_patterns(text, expected):
    assert redact_secrets(text) == expected


def test_known_secret_and_plain_text():
    assert redact_secrets("key=unusual-token-value seen", ("unusual-token-value",)) == "key=[redacted] seen"
    plain = "LLM API error 503: upstream overloaded, please retry (bearer of bad news)"
    assert redact_secrets(plain) == plain
    assert redact_secrets("value", (None, "")) == "value"


def echo_401(request):
    body = {"error": {"message": f"Incorrect API key provided: {KEY}",
                      "echo": {"Authorization": request.headers["Authorization"]}}}
    return httpx.Response(401, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_client_error_message_has_no_key(streaming):
    async with httpx.AsyncClient(transport=httpx.MockTransport(echo_401)) as http:
        client = LLMClient("https://offline.invalid/v1", KEY, "m", http_client=http, thinking="disabled")
        if streaming:
            client.supports_streaming = True
            events = [e async for e in client.stream_chat([{"role": "user", "content": "x"}])]
            message = events[-1].data["message"]
        else:
            with pytest.raises(LLMError) as caught:
                await client.chat([{"role": "user", "content": "x"}])
            message = str(caught.value)
    assert "LLM API error 401" in message
    assert KEY not in message and "[redacted]" in message


@pytest.mark.asyncio
async def test_attempt_record_and_retry_log_have_no_key(caplog):
    class Owner:
        api_key = KEY

        async def call(self, messages, tools):
            raise LLMError(f"LLM API error 503: overloaded for Authorization: Bearer {KEY}",
                           kind=LLMErrorKind.HTTP, status_code=503)

    sink = AttemptSink()
    owner = Owner()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LLMError):
            await RetryPolicy(RetryConfig(max_retries=1, backoff_base_seconds=0), sink).execute(
                owner.call, [{"role": "user", "content": "x"}], [])
    assert len(sink.attempts) == 2
    assert all(KEY not in a.message and "[redacted]" in a.message for a in sink.attempts)
    assert KEY not in caplog.text


@pytest.mark.asyncio
async def test_run_artifacts_have_no_key(tmp_path, caplog):
    http = httpx.AsyncClient(transport=httpx.MockTransport(echo_401))
    llm = LLMClient("https://offline.invalid/v1", KEY, "m", http_client=http, thinking="disabled")
    with caplog.at_level(logging.WARNING):
        result, finished = await run(tmp_path, llm)
    await http.aclose()
    assert finished["termination_kind"] == "provider_http_401"
    assert finished["llm_attempts"] and all("[redacted]" in a["message"] for a in finished["llm_attempts"])
    exported = (tmp_path / "run" / "events.jsonl").read_text(encoding="utf-8")
    assert "LLM API error 401" in exported
    assert KEY not in exported and KEY not in json.dumps(finished) and KEY not in caplog.text

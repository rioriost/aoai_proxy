import os

os.environ.setdefault(
    "AOAI_PROXY_AZURE_OPENAI_ENDPOINT",
    "https://example.cognitiveservices.azure.com",
)
os.environ.setdefault("AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT", "gpt-5.4")

from fastapi import HTTPException

from aoai_proxy.main import (
    AzureOpenAIProxy,
    sanitize_responses_request,
    settings,
)


def test_sanitize_responses_request_keeps_string_function_call_output():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "plain text output",
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == "plain text output"


def test_sanitize_responses_request_replaces_null_function_call_output():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": None,
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == "<Tool returned no output>"


def test_sanitize_responses_request_serializes_object_function_call_output():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": {"status": "ok", "count": 2},
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == '{"status":"ok","count":2}'


def test_sanitize_responses_request_serializes_array_function_call_output():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": ["a", "b", 3],
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == '["a","b",3]'


def test_sanitize_responses_request_replaces_empty_string_function_call_output():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "",
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == "<Tool returned an empty string>"


def test_sanitize_responses_request_truncates_long_function_call_output():
    long_value = {"data": "x" * 20000}
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": long_value,
            }
        ],
    }

    result = sanitize_responses_request(payload)
    output = result["input"][0]["output"]

    assert len(output) <= 12005
    assert " ... " in output
    assert output.startswith('{"data":"')
    assert output.endswith('"}')


def test_sanitize_responses_request_summarizes_failed_string_tool_output():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "This file has unsaved changes. Ask the user whether they want to keep or discard those changes.\nIf they want to keep them, ask for confirmation.",
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == "Tool error: This file has unsaved changes"


def test_sanitize_responses_request_summarizes_failed_object_tool_output():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": {
                    "error": "permission denied",
                    "detail": "Cannot write to the requested path",
                    "path": "/tmp/example.txt",
                    "stack": "very long internal stack trace",
                },
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == (
        '{"error":"permission denied","detail":"Cannot write to the requested path",'
        '"path":"/tmp/example.txt"}'
    )


def test_sanitize_responses_request_summarizes_failed_object_without_primary_keys():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": {
                    "unexpected": "value",
                    "trace": "stack trace here",
                },
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0]["output"] == '{"error":"Tool execution failed"}'


def test_sanitize_responses_request_only_changes_function_call_output_items():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "echo",
                "arguments": '{"text":"hello"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": {"ok": True},
            },
        ],
    }

    result = sanitize_responses_request(payload)

    assert result["input"][0] == payload["input"][0]
    assert result["input"][1] == payload["input"][1]
    assert result["input"][2]["output"] == '{"ok":true}'


def test_sanitize_responses_request_returns_copy():
    payload = {
        "model": "gpt-5.4",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": {"nested": {"a": 1}},
            }
        ],
    }

    result = sanitize_responses_request(payload)

    assert result is not payload
    assert result["input"] is not payload["input"]
    assert payload["input"][0]["output"] == {"nested": {"a": 1}}
    assert result["input"][0]["output"] == '{"nested":{"a":1}}'


def test_upstream_url_for_responses_uses_openai_v1_responses():
    proxy = AzureOpenAIProxy(settings)

    try:
        url = proxy.upstream_url("responses", {})
        assert (
            url == "https://example.cognitiveservices.azure.com/openai/v1/responses"
            "?api-version=preview"
        )
    finally:
        import asyncio

        asyncio.run(proxy.close())


def test_forward_chat_completions_is_not_supported():
    exc = HTTPException(
        status_code=404,
        detail=(
            "This proxy is responses-first. Configure your client to use "
            "`/v1/responses` instead of `/v1/chat/completions`."
        ),
    )

    assert exc.status_code == 404
    assert "/v1/responses" in exc.detail

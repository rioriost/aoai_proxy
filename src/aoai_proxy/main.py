from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import httpx
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError
from azure.identity.aio import AzureCliCredential
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("aoai_proxy")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AOAI_PROXY_",
        case_sensitive=False,
        extra="ignore",
    )

    azure_openai_endpoint: str = Field(
        ...,
        description="Azure OpenAI endpoint, e.g. https://your-resource.cognitiveservices.azure.com",
    )
    azure_openai_api_version: str = Field(
        default="preview",
        description="API version used when proxying Azure OpenAI requests",
    )
    azure_openai_deployment: str = Field(
        ...,
        description="Azure OpenAI deployment name, e.g. gpt-5.4",
    )
    azure_openai_bearer_token: str | None = Field(
        default=None,
        description="Optional bearer token to use instead of AzureCliCredential",
    )
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    request_timeout_seconds: float = Field(default=600.0)
    token_scope: str = Field(
        default="https://cognitiveservices.azure.com/.default",
    )

    @property
    def normalized_endpoint(self) -> str:
        return self.azure_openai_endpoint.rstrip("/")


settings = Settings()


def _json_loads(payload: bytes) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_json_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "application/json" in content_type.lower()


def _is_streaming_request(payload: bytes, content_type: str | None) -> bool:
    if not _is_json_content_type(content_type):
        return False
    parsed = _json_loads(payload)
    return bool(parsed and parsed.get("stream") is True)


def _first_text_from_response_output(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if not isinstance(output, list):
        return ""

    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            if content_item.get("type") == "output_text":
                text = content_item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

    return "".join(text_parts)


def _response_content_type_for_role(role: str) -> str:
    if role == "assistant":
        return "output_text"
    return "input_text"


def _message_content_to_input_items(role: str, content: Any) -> list[dict[str, Any]]:
    content_type = _response_content_type_for_role(role)

    if isinstance(content, str):
        return [
            {
                "type": "message",
                "role": role,
                "content": [{"type": content_type, "text": content}],
            }
        ]

    if isinstance(content, list):
        converted_parts: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text")
                if isinstance(text, str):
                    converted_parts.append({"type": content_type, "text": text})
        if converted_parts:
            return [{"type": "message", "role": role, "content": converted_parts}]

    return []


def chat_completions_to_responses(
    payload: dict[str, Any], deployment: str
) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="`messages` must be an array")

    input_items: list[dict[str, Any]] = []
    instructions_parts: list[str] = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        content = message.get("content")

        if role == "system":
            if isinstance(content, str):
                instructions_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if isinstance(text, str):
                            instructions_parts.append(text)
            continue

        if role in {"user", "assistant", "developer"}:
            input_items.extend(_message_content_to_input_items(role, content))

    response_payload: dict[str, Any] = {
        "model": deployment,
        "input": input_items,
    }

    if instructions_parts:
        response_payload["instructions"] = "\n\n".join(instructions_parts)

    passthrough_fields = (
        "stream",
        "temperature",
        "top_p",
        "max_output_tokens",
        "metadata",
        "parallel_tool_calls",
        "user",
        "store",
    )
    for field in passthrough_fields:
        if field in payload:
            response_payload[field] = payload[field]

    if "tools" in payload and isinstance(payload["tools"], list):
        normalized_tools: list[dict[str, Any]] = []
        for tool in payload["tools"]:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                continue

            function_def = tool.get("function")
            if not isinstance(function_def, dict):
                continue

            name = function_def.get("name")
            if not isinstance(name, str) or not name:
                continue

            normalized_tool: dict[str, Any] = {
                "type": "function",
                "name": name,
            }

            description = function_def.get("description")
            if isinstance(description, str) and description:
                normalized_tool["description"] = description

            parameters = function_def.get("parameters")
            if isinstance(parameters, dict):
                normalized_tool["parameters"] = parameters

            strict = function_def.get("strict")
            if isinstance(strict, bool):
                normalized_tool["strict"] = strict

            normalized_tools.append(normalized_tool)

        if normalized_tools:
            response_payload["tools"] = normalized_tools

    if "tool_choice" in payload:
        tool_choice = payload["tool_choice"]
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            function_def = tool_choice.get("function")
            if isinstance(function_def, dict):
                name = function_def.get("name")
                if isinstance(name, str) and name:
                    response_payload["tool_choice"] = {
                        "type": "function",
                        "name": name,
                    }
        else:
            response_payload["tool_choice"] = tool_choice

    if "max_tokens" in payload and "max_output_tokens" not in response_payload:
        response_payload["max_output_tokens"] = payload["max_tokens"]

    if "n" in payload and payload["n"] not in (None, 1):
        logger.warning(
            "Ignoring unsupported chat.completions field `n=%s`", payload["n"]
        )

    if "response_format" in payload:
        response_format = payload["response_format"]
        if response_format == {"type": "json_object"} or (
            isinstance(response_format, dict)
            and response_format.get("type") == "json_object"
        ):
            response_payload["text"] = {"format": {"type": "json_object"}}

    return response_payload


def responses_to_chat_completions(
    payload: dict[str, Any], deployment: str
) -> dict[str, Any]:
    response_id = payload.get("id", f"chatcmpl-{uuid.uuid4().hex}")
    created_at = payload.get("created_at")
    created = (
        int(created_at) if isinstance(created_at, int | float) else int(time.time())
    )
    text = _first_text_from_response_output(payload)

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

    finish_reason = "stop"
    status = payload.get("status")
    if status == "incomplete":
        finish_reason = "length"

    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": deployment,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def responses_stream_event_to_chat_chunk(
    event_data: dict[str, Any],
    deployment: str,
) -> dict[str, Any] | None:
    event_type = event_data.get("type")
    response_id = (
        event_data.get("response_id")
        or event_data.get("id")
        or f"chatcmpl-{uuid.uuid4().hex}"
    )
    created = int(time.time())

    if event_type == "response.created":
        return {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": deployment,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }

    if event_type == "response.output_text.delta":
        delta = event_data.get("delta", "")
        if not isinstance(delta, str):
            delta = ""
        return {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": deployment,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": delta},
                    "finish_reason": None,
                }
            ],
        }

    if event_type == "response.output_text.done":
        text = event_data.get("text", "")
        if not isinstance(text, str) or not text:
            return None
        return {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": deployment,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": text},
                    "finish_reason": None,
                }
            ],
        }

    if event_type == "response.completed":
        return {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": deployment,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }

    return None


class AzureOpenAIProxy:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self.credential = AzureCliCredential()
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=True,
        )

    async def startup_diagnostics(self) -> None:
        az_path = shutil.which("az")
        if az_path:
            logger.info("Azure CLI detected at path=%s", az_path)
        else:
            logger.warning(
                "Azure CLI executable `az` was not found on PATH. "
                "Set AOAI_PROXY_AZURE_OPENAI_BEARER_TOKEN or install Azure CLI in the runtime."
            )

    async def close(self) -> None:
        await self.client.aclose()
        await self.credential.close()

    async def bearer_token(self) -> str:
        if self.config.azure_openai_bearer_token:
            return self.config.azure_openai_bearer_token

        try:
            token = await self.credential.get_token(self.config.token_scope)
        except ClientAuthenticationError as exc:
            logger.warning("Azure CLI authentication failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Azure CLI authentication failed. Ensure `az` is installed and "
                    "`az login` has been completed, or set "
                    "`AOAI_PROXY_AZURE_OPENAI_BEARER_TOKEN`."
                ),
            ) from exc
        except CredentialUnavailableError as exc:
            logger.warning("Azure CLI credential unavailable: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Azure CLI credential unavailable. Ensure `az` is installed and "
                    "available on PATH inside the runtime container, or set "
                    "`AOAI_PROXY_AZURE_OPENAI_BEARER_TOKEN`."
                ),
            ) from exc
        except Exception as exc:
            logger.warning("Unable to acquire Azure OpenAI bearer token: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Unable to acquire Azure OpenAI bearer token. Ensure `az` is "
                    "installed and available on PATH inside the runtime container, "
                    "or set `AOAI_PROXY_AZURE_OPENAI_BEARER_TOKEN`."
                ),
            ) from exc

        return token.token

    def upstream_url(self, path: str, query_params: dict[str, str]) -> str:
        normalized_path = path.lstrip("/")

        if normalized_path.startswith("openai/"):
            query = query_params.copy()
            if "api-version" not in query:
                query["api-version"] = self.config.azure_openai_api_version
            suffix = f"?{urlencode(query)}" if query else ""
            return f"{self.config.normalized_endpoint}/{normalized_path}{suffix}"

        if normalized_path == "responses":
            query = query_params.copy()
            if "api-version" not in query:
                query["api-version"] = self.config.azure_openai_api_version
            return (
                f"{self.config.normalized_endpoint}/openai/v1/responses"
                f"?{urlencode(query)}"
            )

        if normalized_path in {"chat/completions", "completions", "embeddings"}:
            operation = normalized_path
            query = query_params.copy()
            if "api-version" not in query:
                query["api-version"] = self.config.azure_openai_api_version
            return (
                f"{self.config.normalized_endpoint}/openai/deployments/"
                f"{self.config.azure_openai_deployment}/{operation}"
                f"?{urlencode(query)}"
            )

        query = query_params.copy()
        if "api-version" not in query and normalized_path.startswith("openai/"):
            query["api-version"] = self.config.azure_openai_api_version
        suffix = f"?{urlencode(query)}" if query else ""
        return f"{self.config.normalized_endpoint}/{normalized_path}{suffix}"

    def models_payload(self) -> dict[str, object]:
        model_id = self.config.azure_openai_deployment
        return {
            "object": "list",
            "data": [
                {
                    "id": model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": "azure-openai",
                }
            ],
        }

    async def forward(self, request: Request, path: str) -> Response:
        normalized_path = path.lstrip("/")

        if normalized_path == "models":
            return JSONResponse(content=self.models_payload())

        body = await request.body()
        headers = await self._build_headers(request)
        request_json = (
            _json_loads(body)
            if _is_json_content_type(request.headers.get("content-type"))
            else None
        )

        if normalized_path == "chat/completions":
            if request_json is None:
                raise HTTPException(
                    status_code=400, detail="Expected JSON request body"
                )

            response_payload = chat_completions_to_responses(
                request_json,
                self.config.azure_openai_deployment,
            )
            is_stream = bool(response_payload.get("stream") is True)
            upstream = self.upstream_url("responses", dict(request.query_params))
            upstream_body = json.dumps(response_payload).encode("utf-8")

            logger.info(
                "Adapting /v1/chat/completions to Azure Responses API: deployment=%s stream=%s upstream=%s",
                self.config.azure_openai_deployment,
                is_stream,
                upstream,
            )

            if is_stream:
                return await self._forward_chat_completions_streaming(
                    request=request,
                    upstream=upstream,
                    headers=headers,
                    body=upstream_body,
                )

            upstream_response = await self._request_upstream(
                method=request.method,
                url=upstream,
                headers=headers,
                body=upstream_body,
            )

            payload = self._decode_json_response(upstream_response)
            adapted = responses_to_chat_completions(
                payload, self.config.azure_openai_deployment
            )
            return JSONResponse(
                status_code=upstream_response.status_code, content=adapted
            )

        upstream = self.upstream_url(normalized_path, dict(request.query_params))
        is_stream = _is_streaming_request(body, request.headers.get("content-type"))

        logger.info(
            "Forwarding request path=%s deployment=%s upstream=%s stream=%s",
            normalized_path,
            self.config.azure_openai_deployment,
            upstream,
            is_stream,
        )

        if is_stream:
            return await self._forward_streaming(
                request=request,
                upstream=upstream,
                headers=headers,
                body=body,
            )

        upstream_response = await self._request_upstream(
            method=request.method,
            url=upstream,
            headers=headers,
            body=body,
        )

        response_headers = self._filter_response_headers(upstream_response.headers)
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=upstream_response.headers.get("content-type"),
        )

    async def _request_upstream(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> httpx.Response:
        try:
            return await self.client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Proxy request failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Upstream request failed",
            ) from exc

    async def _forward_streaming(
        self,
        request: Request,
        upstream: str,
        headers: dict[str, str],
        body: bytes,
    ) -> Response:
        try:
            upstream_request = self.client.build_request(
                method=request.method,
                url=upstream,
                headers=headers,
                content=body,
            )
            upstream_response = await self.client.send(
                upstream_request,
                stream=True,
            )
        except Exception as exc:
            logger.exception("Streaming proxy request failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Upstream streaming request failed",
            ) from exc

        async def iterator() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_response.aiter_raw():
                    if chunk:
                        yield chunk
            finally:
                await upstream_response.aclose()

        response_headers = self._filter_response_headers(upstream_response.headers)
        media_type = upstream_response.headers.get(
            "content-type",
            "text/event-stream",
        )
        return StreamingResponse(
            iterator(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=media_type,
        )

    async def _forward_chat_completions_streaming(
        self,
        request: Request,
        upstream: str,
        headers: dict[str, str],
        body: bytes,
    ) -> Response:
        try:
            upstream_request = self.client.build_request(
                method=request.method,
                url=upstream,
                headers=headers,
                content=body,
            )
            upstream_response = await self.client.send(
                upstream_request,
                stream=True,
            )
        except Exception as exc:
            logger.exception("Streaming adapter request failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Upstream streaming request failed",
            ) from exc

        if upstream_response.status_code >= 400:
            content = await upstream_response.aread()
            await upstream_response.aclose()
            return Response(
                content=content,
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get(
                    "content-type", "application/json"
                ),
            )

        async def iterator() -> AsyncIterator[bytes]:
            buffer = ""
            done_sent = False
            emitted_role = False
            try:
                async for chunk in upstream_response.aiter_text():
                    if not chunk:
                        continue
                    buffer += chunk
                    while "\n\n" in buffer:
                        event_block, buffer = buffer.split("\n\n", 1)
                        event_lines = [
                            line
                            for line in event_block.splitlines()
                            if line.startswith("data:")
                        ]
                        if not event_lines:
                            continue
                        data_text = "\n".join(
                            line[5:].lstrip() for line in event_lines
                        ).strip()
                        if not data_text:
                            continue
                        if data_text == "[DONE]":
                            done_sent = True
                            yield b"data: [DONE]\n\n"
                            continue
                        try:
                            event_json = json.loads(data_text)
                        except json.JSONDecodeError:
                            continue

                        event_type = event_json.get("type")
                        if (
                            event_type == "response.output_text.delta"
                            and not emitted_role
                        ):
                            emitted_role = True
                            role_chunk = {
                                "id": event_json.get("response_id")
                                or event_json.get("id")
                                or f"chatcmpl-{uuid.uuid4().hex}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": self.config.azure_openai_deployment,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"role": "assistant"},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                            yield f"data: {json.dumps(role_chunk, ensure_ascii=False)}\n\n".encode(
                                "utf-8"
                            )

                        adapted = responses_stream_event_to_chat_chunk(
                            event_json,
                            self.config.azure_openai_deployment,
                        )
                        if adapted is None:
                            continue
                        yield f"data: {json.dumps(adapted, ensure_ascii=False)}\n\n".encode(
                            "utf-8"
                        )
            finally:
                await upstream_response.aclose()

            if not done_sent:
                yield b"data: [DONE]\n\n"

        return StreamingResponse(
            iterator(),
            status_code=200,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    async def _build_headers(self, request: Request) -> dict[str, str]:
        incoming = request.headers
        token = await self.bearer_token()

        headers: dict[str, str] = {
            "authorization": f"Bearer {token}",
        }

        for header_name in (
            "content-type",
            "accept",
            "openai-beta",
            "user-agent",
            "x-request-id",
        ):
            header_value = incoming.get(header_name)
            if header_value:
                headers[header_name] = header_value

        return headers

    @staticmethod
    def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
        excluded = {
            "content-length",
            "content-encoding",
            "transfer-encoding",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "upgrade",
        }
        return {
            key: value for key, value in headers.items() if key.lower() not in excluded
        }

    @staticmethod
    def _decode_json_response(response: httpx.Response) -> dict[str, Any]:
        try:
            parsed = response.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail="Upstream returned non-JSON response",
            ) from exc

        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=502,
                detail="Upstream returned unexpected JSON shape",
            )

        return parsed


@asynccontextmanager
async def lifespan(app: FastAPI):
    proxy = AzureOpenAIProxy(settings)
    app.state.proxy = proxy
    logger.info(
        "Starting Azure OpenAI proxy for endpoint=%s deployment=%s",
        settings.normalized_endpoint,
        settings.azure_openai_deployment,
    )
    await proxy.startup_diagnostics()
    try:
        yield
    finally:
        await proxy.close()


app = FastAPI(
    title="Azure OpenAI OpenAI-Compatible Proxy",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "aoai-proxy",
        "status": "ok",
        "deployment": settings.azure_openai_deployment,
    }


@app.get("/v1/models")
async def list_models() -> Response:
    return JSONResponse(content=app.state.proxy.models_payload())


@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_v1(path: str, request: Request) -> Response:
    return await app.state.proxy.forward(request, path)


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_root(path: str, request: Request) -> Response:
    normalized_path = path.lstrip("/")
    if normalized_path == "":
        return PlainTextResponse("aoai-proxy", status_code=200)
    return await app.state.proxy.forward(request, normalized_path)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "aoai_proxy.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

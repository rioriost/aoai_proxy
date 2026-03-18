from __future__ import annotations

import json
import logging
import os
import shutil
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


def _truncate_middle(text: str, max_length: int = 12000) -> str:
    if len(text) <= max_length:
        return text

    keep = max_length // 2
    return f"{text[:keep]} ... {text[-keep:]}"


def _looks_like_tool_error(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return any(
            marker in lowered
            for marker in (
                "error",
                "failed",
                "exception",
                "traceback",
                "denied",
                "unsaved changes",
                "permission",
            )
        )

    if isinstance(value, dict):
        if any(
            key in value for key in ("error", "errors", "message", "detail", "code")
        ):
            return True

        return any(
            key in value
            for key in ("trace", "traceback", "stack", "exception", "stderr")
        )

    return False


def _summarize_tool_error_value(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "Tool error: <empty error message>"

        first_line = text.splitlines()[0].strip()
        first_sentence = first_line.split(". ")[0].strip()
        summary = first_sentence if first_sentence else first_line
        return _truncate_middle(f"Tool error: {summary}", max_length=2000)

    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        for key in ("error", "message", "detail", "code", "path", "tool", "tool_name"):
            if key in value:
                summary[key] = value[key]

        if not summary:
            summary = {"error": "Tool execution failed"}

        serialized = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        return _truncate_middle(serialized, max_length=2000)

    return "Tool error: Tool execution failed"


def _sanitize_function_call_output_value(value: Any) -> str:
    if _looks_like_tool_error(value):
        return _summarize_tool_error_value(value)

    if isinstance(value, str):
        return value if value else "<Tool returned an empty string>"

    if value is None:
        return "<Tool returned no output>"

    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(value)

    if not serialized:
        return "<Tool returned an empty string>"

    return _truncate_middle(serialized)


def sanitize_responses_request(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(payload))
    input_items = sanitized.get("input")
    if not isinstance(input_items, list):
        return sanitized

    for item in input_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call_output":
            continue

        item["output"] = _sanitize_function_call_output_value(item.get("output"))

    return sanitized


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

        if normalized_path == "embeddings":
            query = query_params.copy()
            if "api-version" not in query:
                query["api-version"] = self.config.azure_openai_api_version
            return (
                f"{self.config.normalized_endpoint}/openai/deployments/"
                f"{self.config.azure_openai_deployment}/embeddings"
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
            raise HTTPException(
                status_code=404,
                detail=(
                    "This proxy is responses-first. Configure your client to use "
                    "`/v1/responses` instead of `/v1/chat/completions`."
                ),
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
        if normalized_path == "responses" and request_json is not None:
            request_json = sanitize_responses_request(request_json)
            body = json.dumps(request_json).encode("utf-8")

            input_items = request_json.get("input")
            input_count = len(input_items) if isinstance(input_items, list) else 0
            tools_count = (
                len(request_json.get("tools", []))
                if isinstance(request_json.get("tools"), list)
                else 0
            )

            item_type_counts: dict[str, int] = {}
            message_role_counts: dict[str, int] = {}
            content_type_counts: dict[str, int] = {}

            if isinstance(input_items, list):
                for item in input_items:
                    if not isinstance(item, dict):
                        item_type_counts["<non-dict>"] = (
                            item_type_counts.get("<non-dict>", 0) + 1
                        )
                        continue

                    item_type = item.get("type")
                    item_type_key = (
                        item_type if isinstance(item_type, str) else "<missing>"
                    )
                    item_type_counts[item_type_key] = (
                        item_type_counts.get(item_type_key, 0) + 1
                    )

                    if item_type == "message":
                        role = item.get("role")
                        role_key = role if isinstance(role, str) else "<missing>"
                        message_role_counts[role_key] = (
                            message_role_counts.get(role_key, 0) + 1
                        )

                        content = item.get("content")
                        if isinstance(content, list):
                            for part in content:
                                if not isinstance(part, dict):
                                    content_type_counts["<non-dict>"] = (
                                        content_type_counts.get("<non-dict>", 0) + 1
                                    )
                                    continue

                                part_type = part.get("type")
                                part_type_key = (
                                    part_type
                                    if isinstance(part_type, str)
                                    else "<missing>"
                                )
                                content_type_counts[part_type_key] = (
                                    content_type_counts.get(part_type_key, 0) + 1
                                )

            logger.info(
                "Incoming responses shape: input_items=%s tools=%s tool_choice=%s stream=%s item_types=%s message_roles=%s content_types=%s",
                input_count,
                tools_count,
                request_json.get("tool_choice"),
                request_json.get("stream"),
                item_type_counts,
                message_role_counts,
                content_type_counts,
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

        if upstream_response.status_code >= 400:
            content = await upstream_response.aread()
            await upstream_response.aclose()
            logger.error(
                "Upstream streaming request failed: status=%s body=%s",
                upstream_response.status_code,
                content.decode("utf-8", errors="replace"),
            )
            return Response(
                content=content,
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get(
                    "content-type", "application/json"
                ),
            )

        async def iterator() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_response.aiter_text():
                    if chunk:
                        yield chunk.encode("utf-8")
            finally:
                await upstream_response.aclose()

        response_headers = self._filter_response_headers(upstream_response.headers)
        response_headers["Cache-Control"] = "no-cache"
        response_headers["Connection"] = "keep-alive"
        response_headers["X-Accel-Buffering"] = "no"
        return StreamingResponse(
            iterator(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type="text/event-stream; charset=utf-8",
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

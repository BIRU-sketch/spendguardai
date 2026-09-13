"""
Gateway entrypoint. Auth is now enforced on every request. Streaming is
now wired end-to-end at the transport level — usage from a completed
stream is extracted but not yet fed into any quota/ledger logic, since
those don't exist yet. That's the next roadmap step, not this one.
"""

from __future__ import annotations

import json
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from dotenv import load_dotenv
from app.auth import InMemoryAPIKeyStore, Project, extract_api_key,AuthError
from app.providers.base import InternalRequest, ProviderError
from app.providers.openai_compatible import OpenAICompatibleAdapter
load_dotenv()
app = FastAPI(title="SpendGuard AI — gateway")

_adapter = OpenAICompatibleAdapter(
    base_url=os.getenv("UPSTREAM_BASE_URL"),
    api_key=os.getenv("UPSTREAM_BASE_KEY"),
)
_key_store = InMemoryAPIKeyStore()


async def get_current_project(authorization: str | None = Header(default=None)) -> Project:
    try:
        api_key = extract_api_key(authorization)
        return await _key_store.resolve(api_key)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _build_internal_request(body: dict, stream: bool) -> InternalRequest:
    return InternalRequest(
        model=body["model"],
        messages=body["messages"],
        stream=stream,
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        extra={
            k: v
            for k, v in body.items()
            if k not in {"model", "messages", "stream", "max_tokens", "temperature"}
        },
    )


@app.post("/chat/completions")
async def chat_completions(body: dict, project: Project = Depends(get_current_project)):
    stream = bool(body.get("stream"))
    internal_req = _build_internal_request(body, stream=stream)
    provider_payload = _adapter.translate_request(internal_req)

    if stream:
        return StreamingResponse(
            _stream_response(provider_payload, project),
            media_type="text/event-stream",
        )

    try:
        response = await _adapter.send(provider_payload)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Extracted but unused until quota.py/ledger.py exist — see module docstring.
    _adapter.extract_usage(response)

    return JSONResponse(status_code=response.status_code, content=response.payload)


async def _stream_response(provider_payload: dict, project: Project):
    last_chunk: dict | None = None
    try:
        async for chunk in _adapter.stream(provider_payload):
            last_chunk = chunk
            yield f"data: {json.dumps(chunk)}\n\n"
    except ProviderError as exc:
        # Mid-stream failure: can't raise HTTPException once the response
        # has started, so emit an SSE error event instead.
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        return

    if last_chunk is not None:
        # Logged only for now — will feed the reservation true-up in
        # streaming.py once that step exists.
        usage = _adapter.extract_stream_usage(last_chunk)
        print(f"[stream complete] project={project.project_id} usage={usage}")

    yield "data: [DONE]\n\n"


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
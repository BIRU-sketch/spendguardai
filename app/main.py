"""
Gateway entrypoint. Auth is now enforced on every request. Quota is now
enforced too — check_and_reserve happens right after auth resolves, before
either the streaming or non-streaming path ever touches the adapter.
Streaming is wired end-to-end at the transport level — usage from a
completed stream is extracted but not yet fed into the ledger, since
ledger.py doesn't exist yet. That's the next roadmap step, not this one.
"""

from __future__ import annotations

import json
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from dotenv import load_dotenv
from app.auth import InMemoryAPIKeyStore, Project, extract_api_key, AuthError
from app.providers.base import InternalRequest, ProviderError
from app.providers.openai_compatible import OpenAICompatibleAdapter
from app.quota import RedisQuotaStore  
from app.ledger import Ledger
_ledger = Ledger(database_url="postgresql://postgres.wvkzmmjbmdqnkbnvntpo:1234509876%21%40%23%24%25%29%28%2A%26%5E@aws-1-eu-west-1.pooler.supabase.com:6543/postgres")
import time
load_dotenv()
_quota_store = RedisQuotaStore(redis_url=os.getenv("REDIS_URL")) 

app = FastAPI(title="SpendGuard AI — gateway")

_adapter = OpenAICompatibleAdapter(
    base_url=os.getenv("UPSTREAM_BASE_URL"),
    api_key=os.getenv("UPSTREAM_BASE_KEY"),
)
_key_store = InMemoryAPIKeyStore()
_quota_store = RedisQuotaStore(redis_url=os.getenv("REDIS_URL"))  # was: InMemoryQuotaStore()

# Flat placeholder cost per request until real per-token pricing exists —
# intentionally crude, just needs to prove the circuit breaker works.
_ESTIMATED_COST_CENTS = 1


@app.on_event("startup")
async def seed_test_balance():
    # Placeholder for real budget provisioning (belongs to the future
    # signup/dashboard flow). Seeds the test project so the circuit
    # breaker has something real to trip against.
    await _quota_store.set_balance("local-dev", 3)
    await _ledger.start()


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
    t0= time.perf_counter()
    quota_result = await _quota_store.check_and_reserve(project.project_id, _ESTIMATED_COST_CENTS)
    t1= time.perf_counter()
    print(f"[timing] quota check: {(t1 - t0) * 1000:.0f}ms")
    print(quota_result.allowed)
    if not quota_result.allowed:
        raise HTTPException(status_code=429, detail="Quota exceeded")

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

    # Extracted but unused until ledger.py exists — see module docstring.
    usage=_adapter.extract_usage(response)
    _ledger.record(project.project_id, end_user_id=None, model=body["model"], usage=usage)

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
        _ledger.record(project.project_id, end_user_id=None, model=provider_payload.model, usage=usage)

    yield "data: [DONE]\n\n"

@app.on_event("shutdown")
async def shutdown():
    await _ledger.stop()
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
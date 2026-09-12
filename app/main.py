"""
Gateway entrypoint — step 1 on the roadmap. Deliberately bare: a request
comes in, goes through the provider adapter, and the response goes back
out unmodified in effect. No auth, no quota check, no ledger, no anomaly
detection yet — those are separate roadmap steps and stay separate files
(auth.py, quota.py, ledger.py, anomaly.py) so this file doesn't grow into
doing all of it at once.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.providers.base import InternalRequest, ProviderError
from app.providers.openai_compatible import OpenAICompatibleAdapter
from dotenv import load_dotenv
load_dotenv()
app = FastAPI(title="SpendGuard AI — gateway")

# Single adapter instance for now, pointed at whatever upstream you're
# testing against (OpenRouter free tier, your mock server, etc.) via env
# vars. Once auth.py exists, this becomes "one adapter per project" based
# on the project's configured provider — not a global singleton.
_adapter = OpenAICompatibleAdapter(
    base_url=os.getenv("UPSTREAM_BASE_URL"),
    api_key=os.getenv("UPSTREAM_BASE_KEY"),
)


@app.post("/chat/completions")
async def chat_completions(body: dict) -> JSONResponse:
    if body.get("stream"):
        # send() doesn't support streaming yet (see openai_compatible.py) —
        # fail loudly here rather than silently ignoring the flag.
        raise HTTPException(
            status_code=501,
            detail="Streaming isn't wired up yet — see streaming.py on the roadmap.",
        )

    internal_req = InternalRequest(
        model=body["model"],
        messages=body["messages"],
        stream=False,
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        extra={
            k: v
            for k, v in body.items()
            if k not in {"model", "messages", "stream", "max_tokens", "temperature"}
        },
    )

    provider_payload = _adapter.translate_request(internal_req)

    try:
        response = await _adapter.send(provider_payload)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Usage extraction is wired here but unused for now — nothing reads
    # it yet since quota.py and ledger.py don't exist. Calling it early
    # anyway is a cheap way to catch a broken extractor before it matters.
    _adapter.extract_usage(response)

    return JSONResponse(status_code=response.status_code, content=response.payload)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
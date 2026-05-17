import asyncio
import os

import httpx

BASE_URL = "https://elyos-interview-907656039105.europe-west2.run.app"


async def _get(endpoint: str, params: dict) -> dict:
    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            r = await client.get(
                f"{BASE_URL}{endpoint}",
                headers={"X-API-Key": os.environ["ELYOS_API_KEY"]},
                params=params,
                timeout=10.0,
            )
            if r.status_code == 404:
                raise ValueError(r.json().get("error", "Not found"))

            body = r.json()
            if isinstance(body, dict) and body.get("status") == "throttled":
                wait = body.get("retry_after_seconds", 5) * (2 ** attempt)
                print(f"\r  Rate limited — retrying in {wait}s...{' ' * 20}", flush=True)
                await asyncio.sleep(wait)
                continue

            return body

    raise RuntimeError("Still rate limited after retries")


TOOL_SUMMARIES = {
    "get_weather": lambda args: f"Getting weather for {args.get('location', '')}...",
    "research_topic": lambda args: f"Researching {args.get('topic', '')}...",
}


async def get_weather(location: str) -> dict:
    body = await _get("/weather", {"location": location})
    # API sometimes returns {conditions: [...]} array instead of flat fields
    if "conditions" in body:
        return {"location": body["location"], **body["conditions"][0]}
    return body


async def research_topic(topic: str) -> dict:
    body = await _get("/research", {"topic": topic})
    result = {"topic": body["topic"], "summary": body["summary"]}
    if body.get("cached"):
        result["note"] = f"Cached result ({body.get('cache_age_seconds', '?')}s old) — may be outdated"
    return result

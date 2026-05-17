"""
Probe the Elyos interview APIs to discover undocumented behaviors.
Hits each endpoint multiple times and logs status, latency, and response shape.

Usage:
    uv run python scripts/probe_api_errors.py                  # concurrent weather, sequential research
    uv run python scripts/probe_api_errors.py --delay 2.0      # 2s between every request (finds rate limit threshold)
    uv run python scripts/probe_api_errors.py --delay 1.0 --weather-runs 10 --research-runs 5
"""

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://elyos-interview-907656039105.europe-west2.run.app"
API_KEY = os.environ["ELYOS_API_KEY"]
HEADERS = {"X-API-Key": API_KEY}

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


async def probe(client: httpx.AsyncClient, endpoint: str, params: dict, run_id: int) -> dict:
    start = time.monotonic()
    try:
        r = await client.get(f"{BASE_URL}{endpoint}", headers=HEADERS, params=params, timeout=15.0)
        elapsed = time.monotonic() - start
        try:
            body = r.json()
        except Exception:
            body = r.text
        throttled = isinstance(body, dict) and body.get("status") == "throttled"
        return {
            "run": run_id,
            "status": r.status_code,
            "throttled": throttled,
            "retry_after": body.get("retry_after_seconds") if throttled else None,
            "elapsed": round(elapsed, 3),
            "body": body,
            "headers": dict(r.headers),
        }
    except httpx.TimeoutException:
        return {"run": run_id, "status": "TIMEOUT", "throttled": False, "retry_after": None, "elapsed": round(time.monotonic() - start, 3), "body": None}
    except Exception as e:
        return {"run": run_id, "status": "ERROR", "throttled": False, "retry_after": None, "elapsed": round(time.monotonic() - start, 3), "body": str(e)}


def summarize(label: str, results: list[dict]):
    print(f"\n{'='*60}")
    print(f"  {label} ({len(results)} runs)")
    print(f"{'='*60}")

    statuses = Counter(r["status"] for r in results)
    throttled = sum(1 for r in results if r.get("throttled"))
    print(f"Status codes:  {dict(statuses)}  |  throttled (200+body): {throttled}/{len(results)}")

    latencies = [r["elapsed"] for r in results if isinstance(r["status"], int)]
    if latencies:
        print(f"Latency (s):   min={min(latencies):.3f}  max={max(latencies):.3f}  avg={sum(latencies)/len(latencies):.3f}")

    # collect unique top-level keys across non-throttled JSON responses
    key_sets = []
    for r in results:
        if isinstance(r["body"], dict) and not r.get("throttled"):
            key_sets.append(frozenset(r["body"].keys()))
    if key_sets:
        all_keys = frozenset().union(*key_sets)
        common_keys = frozenset.intersection(*key_sets)
        if all_keys != common_keys:
            print(f"ANOMALY: inconsistent response keys")
            print(f"  always present: {sorted(common_keys)}")
            print(f"  sometimes present: {sorted(all_keys - common_keys)}")
        else:
            print(f"Response keys: {sorted(common_keys)} (consistent)")

    print()
    for r in results:
        body_preview = json.dumps(r["body"])[:120] if r["body"] else "None"
        flag = " [THROTTLED]" if r.get("throttled") else ""
        print(f"  [{r['run']:02d}] {r['status']}{flag}  {r['elapsed']}s  {body_preview}")


async def run_sequential(client: httpx.AsyncClient, endpoint: str, params: dict, runs: int, delay: float) -> list[dict]:
    results = []
    for i in range(runs):
        if i > 0 and delay > 0:
            await asyncio.sleep(delay)
        label = f"{endpoint} run {i+1}/{runs}"
        print(f"  {label}...", end=" ", flush=True)
        result = await probe(client, endpoint, params, i)
        flag = " [THROTTLED]" if result.get("throttled") else ""
        print(f"{result['status']}{flag}  {result['elapsed']}s")
        results.append(result)
    return results


async def main(weather_runs: int, research_runs: int, delay: float):
    log: dict = {"started_at": datetime.now().isoformat(), "delay_between_requests": delay, "runs": {}}

    async with httpx.AsyncClient() as client:
        print(f"Probing /weather ({weather_runs} runs, delay={delay}s) ...")
        if delay == 0:
            # concurrent — fast but burns through rate limit
            weather_tasks = [probe(client, "/weather", {"location": "London"}, i) for i in range(weather_runs)]
            weather_results = list(await asyncio.gather(*weather_tasks))
            for r in weather_results:
                flag = " [THROTTLED]" if r.get("throttled") else ""
                print(f"  [{r['run']:02d}] {r['status']}{flag}  {r['elapsed']}s")
        else:
            weather_results = await run_sequential(client, "/weather", {"location": "London"}, weather_runs, delay)
        summarize("/weather?location=London", weather_results)
        log["runs"]["weather"] = weather_results

        print(f"\nProbing /research ({research_runs} runs, delay={delay}s) ...")
        research_results = await run_sequential(client, "/research", {"topic": "solar energy"}, research_runs, delay)
        summarize("/research?topic=solar+energy", research_results)
        log["runs"]["research"] = research_results

        print("\n--- Edge case probes ---")
        edge_cases = [
            ("/weather", {"location": ""}),
            ("/weather", {"location": "zzz_notaplace_123"}),
            ("/research", {"topic": ""}),
        ]
        edge_results = []
        for endpoint, params in edge_cases:
            if delay > 0:
                await asyncio.sleep(delay)
            r = await probe(client, endpoint, params, 0)
            r["endpoint"] = endpoint
            r["params"] = params
            edge_results.append(r)
            flag = " [THROTTLED]" if r.get("throttled") else ""
            print(f"  {endpoint} {params} -> {r['status']}{flag}  {r['elapsed']}s  {json.dumps(r['body'])[:100]}")
        log["runs"]["edge_cases"] = edge_results

    log["finished_at"] = datetime.now().isoformat()
    log_file = LOGS_DIR / f"probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log_file.write_text(json.dumps(log, indent=2))
    print(f"\nFull results written to {log_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Probe Elyos interview APIs")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to wait between requests (0 = concurrent for weather). Try 2.0, 1.0, 0.5 to probe rate limit threshold.")
    parser.add_argument("--weather-runs", type=int, default=20)
    parser.add_argument("--research-runs", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(main(args.weather_runs, args.research_runs, args.delay))
# Elyos Chat CLI

Streaming CLI chat with weather and research tools.

## Setup

```bash
cp .env.example .env   # fill in ELYOS_API_KEY and ANTHROPIC_API_KEY
uv sync
```

## Run

```bash
uv run python src/main.py
```
Note: Tool calls are logged to logs/tool_calls.log

## Probe API behavipur

```bash
uv run python scripts/probe_apis.py --delay 6.0 --weather-runs 10 --research-runs 5
```

| Flag | Description |
| --- | --- |
| `--delay` | Minimum delay in seconds between API calls (default `0`) |
| `--weather-runs` | Number of `/weather` requests |
| `--research-runs` | Number of `/research` requests |


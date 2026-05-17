import json
import logging
import os
from pathlib import Path

os.environ["LITELLM_LOG"] = "ERROR"
logging.getLogger("LiteLLM").setLevel(logging.ERROR)

import litellm

litellm.suppress_debug_info = True

from tools import TOOL_SUMMARIES, get_weather, research_topic

MODEL = os.environ.get("ANTHROPIC_MODEL", "anthropic/claude-haiku-4-5-20251001")

_log_dir = Path(__file__).parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_tool_logger = logging.getLogger("tool_calls")
_tool_logger.setLevel(logging.DEBUG)
_tool_logger.addHandler(logging.FileHandler(_log_dir / "tool_calls.log"))

SYSTEM_PROMPT = """You are a focused assistant with two tools: get_weather and research_topic.
Only answer questions that use one of these tools. For anything else, politely say you can only help with weather lookups and topic research."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location. Fast (~200ms).",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, e.g. London"},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_topic",
            "description": "Research a topic in depth. Takes 3-8 seconds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic to research"},
                },
                "required": ["topic"],
            },
        },
    },
]

TOOL_FNS = {"get_weather": get_weather, "research_topic": research_topic}


async def call_llm(messages: list):
    """
    Async generator. Yields str chunks (LLM text) or event dicts for tool lifecycle:
      {"event": "tool_start", "name": ..., "args": ...}
      {"event": "tool_done",  "name": ...}
    Mutates messages in-place with assistant turns and tool results.
    Loops until LLM responds without requesting a tool call.
    """
    while True:
        response = await litellm.acompletion(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            tools=TOOLS,
            stream=True,
        )

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}  # index -> accumulated call

        async for chunk in response:
            delta = chunk.choices[0].delta

            if delta.content:
                yield delta.content
                content_parts.append(delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function.name:
                        tool_calls_acc[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls_acc[idx]["args"] += tc.function.arguments

        if not tool_calls_acc:
            messages.append({"role": "assistant", "content": "".join(content_parts)})
            break

        # Append assistant turn with tool call requests
        messages.append({
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["args"]}}
                for tc in tool_calls_acc.values()
            ],
        })

        # Execute tools and append results before next LLM turn
        for tc in tool_calls_acc.values():
            args = json.loads(tc["args"])
            summary = TOOL_SUMMARIES.get(tc["name"], lambda a: f"Running {tc['name']}...")(args)
            yield {"event": "tool_start", "name": tc["name"], "args": args, "summary": summary}
            _tool_logger.info("call  %s %s", tc["name"], json.dumps(args))
            error: str | None = None
            try:
                result = await TOOL_FNS[tc["name"]](**args)
                _tool_logger.info("ok    %s %s", tc["name"], json.dumps(result))
            except Exception as e:
                error = str(e)
                result = {"error": error}
                _tool_logger.error("error %s %s", tc["name"], error)
            yield {"event": "tool_done", "name": tc["name"], "error": error}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result),
            })

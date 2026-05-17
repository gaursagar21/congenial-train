import asyncio
import signal
import sys

from dotenv import load_dotenv

from llm import call_llm

load_dotenv()

_active_task: asyncio.Task | None = None


async def _spinner(message: str):
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while True:
        print(f"\r{frames[i % len(frames)]} {message}", end="", flush=True)
        i += 1
        await asyncio.sleep(0.1)


async def _respond(messages: list, partial: list):
    spin: asyncio.Task | None = asyncio.create_task(_spinner("Thinking..."))
    first_chunk = True

    def clear_spinner():
        nonlocal spin
        if spin:
            spin.cancel()
            spin = None
            print("\r" + " " * 60 + "\r", end="", flush=True)

    try:
        async for item in call_llm(messages):
            if isinstance(item, str):
                clear_spinner()
                if first_chunk:
                    print("Assistant: ", end="", flush=True)
                    first_chunk = False
                print(item, end="", flush=True)
                partial.append(item)
            elif item["event"] == "tool_start":
                clear_spinner()
                partial.clear()  # tool call starting, clear any preamble text (e.g. "Let me check that for you...")
                spin = asyncio.create_task(_spinner(f"{item['summary']} (Ctrl+C to cancel)"))
            elif item["event"] == "tool_done":
                clear_spinner()
                if item.get("error"):
                    print(f"  ! Tool error: {item['error']}", flush=True)
                spin = asyncio.create_task(_spinner("Thinking..."))
    except asyncio.CancelledError:
        clear_spinner()
        raise


async def main():
    global _active_task

    def on_sigint():
        # Handle Ctrl+C
        if _active_task and not _active_task.done():
            _active_task.cancel()
        else:
            sys.exit(0)

    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, on_sigint)

    messages = []
    print("╭─ Chat CLI ─────────────────────────────────╮")
    print("│  Ctrl+C to cancel  ·  'quit' to exit       │")
    print("╰────────────────────────────────────────────╯")

    while True:
        try:
            user_input = (await asyncio.to_thread(input, "\nYou › ")).strip()
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break

        messages.append({"role": "user", "content": user_input})
        partial: list = []
        _active_task = asyncio.create_task(_respond(messages, partial))
        try:
            await _active_task
            print()
        except asyncio.CancelledError:
            print("\n  ✗ Cancelled")
            text = "".join(partial) + " [interrupted]" if partial else "[interrupted]"
            messages.append({"role": "assistant", "content": text})
        finally:
            _active_task = None

    print("\nGoodbye!")


if __name__ == "__main__":
    asyncio.run(main())

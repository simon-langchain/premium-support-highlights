"""Check every model in llm.AVAILABLE_MODELS works through the LLM Gateway.

For each model: one short prompt, one tool call (the account summary runs as a
tool-calling agent, so a model that can't call tools breaks it), and a temperature=0 call
checked against llm.supports_temperature (the duplicate check uses it). Providers retire
models without notice, so run this before deploying a model-list change.

    cd backend && uv run python check_models.py              # all models
    uv run python check_models.py anthropic:claude-sonnet-5  # just these

Exits non-zero if any model fails.
"""

import asyncio
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from langchain_core.tools import tool  # noqa: E402

import llm  # noqa: E402

_TIMEOUT = 90
_CONCURRENCY = 6


@tool
def get_ticket_count(account: str, state: str) -> int:
    """Return how many tickets an account has in a given state (e.g. 'on_hold')."""
    return 3


async def _check(model_id: str, sem: asyncio.Semaphore) -> tuple[str, str, str]:
    async with sem:
        start = time.monotonic()
        try:
            chat = llm.get_chat_model(model_id)
            await asyncio.wait_for(chat.ainvoke("Reply with the single word OK"), _TIMEOUT)
            reply = await asyncio.wait_for(
                chat.bind_tools([get_ticket_count]).ainvoke(
                    "How many on_hold tickets does the account Orange have? Use the tool."
                ),
                _TIMEOUT,
            )
            calls = getattr(reply, "tool_calls", None) or []
            if not calls or calls[0].get("name") != "get_ticket_count":
                return model_id, "FAIL", "answered without calling the tool"
            try:
                await asyncio.wait_for(chat.bind(temperature=0).ainvoke("Reply with the single word OK"), _TIMEOUT)
                accepts = True
            except Exception as exc:  # noqa: BLE001
                if "temperature" not in str(exc).lower():
                    raise
                accepts = False
            if accepts != llm.supports_temperature(model_id):
                fix = "remove from" if accepts else "add to"
                return model_id, "FAIL", f"{'accepts' if accepts else 'rejects'} temperature: {fix} llm._NO_TEMPERATURE"
            return model_id, "ok", f"{time.monotonic() - start:.1f}s"
        except Exception as exc:  # noqa: BLE001 — report every failure, keep checking the rest
            first_line = (str(exc).splitlines() or [""])[0]
            return model_id, "FAIL", f"{type(exc).__name__}: {first_line[:120]}"


async def main(model_ids: list[str]) -> int:
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(*[_check(m, sem) for m in model_ids])
    width = max(len(m) for m in model_ids)
    for model_id, status, detail in results:
        default = "  (default)" if model_id == llm.DEFAULT_MODEL_ID else ""
        print(f"{status:4}  {model_id:{width}}  {detail}{default}")
    failed = sum(1 for _, status, _ in results if status != "ok")
    print(f"\n{len(results) - failed}/{len(results)} models OK")
    return 1 if failed else 0


if __name__ == "__main__":
    ids = sys.argv[1:] or [m["id"] for m in llm.AVAILABLE_MODELS]
    sys.exit(asyncio.run(main(ids)))

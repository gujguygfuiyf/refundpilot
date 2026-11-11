"""Smoke test of the Insights assistant against the real model, with the key in backend/.env.

Usage (from backend/):
  python scripts/assistant_smoke.py [request.json ...]

The unit tests drive app/assistant.py with a scripted model; this is the part they cannot cover: whether the
configured Gemini model answers AG Studio's turns in the JSON envelope the server expects. It runs:
  1. every Studio-shaped request in tests/fixtures/studio_turn_*.json, plus any request files named on the
     command line (a turn request saved from the browser's network tab is the most realistic input);
  2. a two-turn conversation with one made-up tool, where this script plays Studio and supplies the tool result.

It calls the model directly (run_turn), not through the API, so it uses Gemini quota but not the API's daily turn
counter. With the two fixtures, at most 8 model calls when every turn needs its repair attempt (plus one retry per transient failure).
Exit code 0 only if every check passes, 1 if one fails, 2 if the assistant is off or has no key.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.assistant import AssistantError, build_turn_model, run_turn   # noqa: E402
from app.config import settings                                       # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# A number the model cannot know without reading the tool result, so quoting it shows the result was used.
CAP_SKU = "MUG-01"
CAP_AMOUNT = "37.25"
CAP_TOOL = {
    "name": "get_refund_cap",
    "description": "Returns the highest refund, in USD, that the merchant's policy allows for one product.",
    "parameters": {"type": "object", "properties": {"sku": {"type": "string", "description": "The product SKU"}},
                   "required": ["sku"], "additionalProperties": False},
}
CAP_INSTRUCTIONS = ("You answer questions about refund caps. Always look a cap up with get_refund_cap; never guess "
                    "one. Answer in one sentence that states the amount.")


def ascii_text(value, limit: int) -> str:
    """Windows consoles choke on characters outside their code page, and the model may write any of them."""
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "..."
    return text.encode("ascii", "replace").decode("ascii")


def calls_of(response: dict) -> list[dict]:
    return [{"name": o["name"], "arguments": json.loads(o["arguments"] or "{}")}
            for o in response["output"] if o.get("type") == "function_call"]


def message_of(response: dict) -> str:
    return "\n".join(c.get("text", "") for o in response["output"] if o.get("type") == "message"
                     for c in o.get("content", []) if c.get("type") == "text").strip()


def schema_problem(call: dict, tools: list[dict]) -> str | None:
    """The first way the call's arguments break the tool's schema, if jsonschema is installed. Studio does this
    check in the browser; a failure here is what the model would be told to correct, not a crash."""
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError:
        return None
    schema = next((t.get("parameters") or {} for t in tools if t["name"] == call["name"]), None)
    if schema is None:
        return None
    try:
        error = next(iter(Draft202012Validator(schema).iter_errors(call["arguments"])), None)
    except SchemaError as e:
        return f"(the tool's schema itself did not load: {e.message})"
    if error is None:
        return None
    where = "/".join(str(p) for p in error.absolute_path) or "(top level)"
    return f"{where}: {error.message}"


def timed_turn(model, **kw):
    started = time.monotonic()
    result = run_turn(model, **kw)
    return result, time.monotonic() - started


def report(result, seconds: float, tools: list[dict]) -> None:
    message = message_of(result.response)
    print(f"  message:      {ascii_text(message, 160) if message else '(none)'}")
    for call in calls_of(result.response):
        print(f"  tool call:    {call['name']} {ascii_text(call['arguments'], 300)}")
        problem = schema_problem(call, tools)
        if problem:
            print(f"  schema:       {ascii_text(problem, 300)}")
    repaired = " (the first reply needed a repair)" if result.model_calls > 1 else ""
    print(f"  model calls:  {result.model_calls}{repaired}")
    print(f"  tokens:       {result.input_tokens} in, {result.output_tokens} out")
    print(f"  seconds:      {seconds:.1f}")


def verdict(ok: bool, why: str) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}: {why}")
    return ok


def check_fixture(model, path: Path) -> bool:
    req = json.loads(path.read_text(encoding="utf-8"))
    tools, choice = req.get("tools") or [], req.get("tool_choice", "auto")
    print(f"\n[{path.stem}]")
    print(f"  tools:        {', '.join(t['name'] for t in tools) or '(none)'}")
    print(f"  tool_choice:  {ascii_text(choice, 80)}")
    try:
        result, seconds = timed_turn(model, items=req["input"], instructions=req.get("instructions", ""),
                                     tools=tools, tool_choice=choice)
    except AssistantError as e:
        print(f"  error:        {e.status} {ascii_text(e.message, 300)}")
        return verdict(False, "the turn failed")
    report(result, seconds, tools)
    calls, message = calls_of(result.response), message_of(result.response)
    forced = choice.get("name") if isinstance(choice, dict) else None
    if forced:
        if [c["name"] for c in calls] != [forced]:
            return verdict(False, f"expected exactly one call to {forced}")
        if forced == "complete_task":
            summary = calls[0]["arguments"].get("summary")
            ok = isinstance(summary, str) and bool(summary.strip())
            return verdict(ok, "complete_task with a non-empty summary" if ok else "complete_task without a summary")
        return verdict(True, f"called {forced}")
    offered = {t["name"] for t in tools}
    if calls:
        stray = sorted({c["name"] for c in calls} - offered)
        return verdict(not stray, f"called {', '.join(c['name'] for c in calls)}"
                                  + (f"; not offered: {', '.join(stray)}" if stray else ""))
    return verdict(bool(message), "answered in prose" if message else "neither a message nor a tool call")


def check_tool_round_trip(model) -> bool:
    print("\n[two-turn conversation with get_refund_cap]")
    question = {"id": "smoke-q", "kind": "input", "type": "message", "role": "user", "status": "completed",
                "content": [{"type": "text", "text": f"What is the refund cap for {CAP_SKU}?"}]}
    try:
        first, seconds = timed_turn(model, items=[question], instructions=CAP_INSTRUCTIONS, tools=[CAP_TOOL])
        print(" turn 1")
        report(first, seconds, [CAP_TOOL])
        asked = [o for o in first.response["output"] if o.get("type") == "function_call"]
        if not any(o["name"] == CAP_TOOL["name"] for o in asked):
            return verdict(False, "the first reply did not call get_refund_cap")
        # Studio sends the model's own output items back with a result after each call; this does the same.
        items = [question]
        for o in first.response["output"]:
            items.append(o)
            if o.get("type") == "function_call":
                result = {"success": True, "response": {"sku": CAP_SKU, "cap_usd": float(CAP_AMOUNT)}}
                items.append({"type": "function_call_output", "callId": o["callId"], "output": json.dumps(result)})
        second, seconds = timed_turn(model, items=items, instructions=CAP_INSTRUCTIONS, tools=[CAP_TOOL])
        print(" turn 2")
        report(second, seconds, [CAP_TOOL])
    except AssistantError as e:
        print(f"  error:        {e.status} {ascii_text(e.message, 300)}")
        return verdict(False, "a turn failed")
    message = message_of(second.response)
    return verdict(CAP_AMOUNT in message, f"answered with {CAP_AMOUNT}" if CAP_AMOUNT in message else f"the answer does not mention {CAP_AMOUNT}")


def main(extra: list[str] | None = None) -> int:
    model = build_turn_model(settings)
    if model is None:
        if not settings.assistant_enabled:
            print("The assistant is switched off: set ASSISTANT_ENABLED=true in backend/.env.")
        else:
            print("The assistant has no model: set GEMINI_API_KEY (or ASSISTANT_API_KEY) in backend/.env, "
                  "and run pip install -e . if google-genai is missing.")
        return 2
    print(f"Model: {model.model}")
    fixtures = sorted(FIXTURES.glob("studio_turn_*.json")) + [Path(p) for p in extra or []]
    results = [check_fixture(model, path) for path in fixtures] + [check_tool_round_trip(model)]
    print(f"\nSMOKE: {sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

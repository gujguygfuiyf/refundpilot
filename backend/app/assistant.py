"""Model turns for the Insights assistant (AG Studio's agent framework), served by Gemini.

AG Studio runs its agents in the browser: it owns the agents, their tools and the tool loop, and asks a "language
model adapter" for one model turn at a time. This module is that one turn, on the server, so the Gemini key never
reaches the browser.

A turn arrives provider-neutral: the conversation so far (user messages, the model's earlier tool calls, the tool
results), the agent's instructions, and the JSON Schema of every tool it may call. Studio writes those schemas for
another provider's strict mode (recursive $defs, sentinel branches), which Gemini's function declarations do not
take as they are. So the turn runs as plain JSON generation instead: the tools and their schemas go into the system
instruction, the model answers with one JSON envelope naming the calls it wants, and Studio validates every
argument on its side and returns any problem as that tool's result, which the model then corrects.

Nothing here can change a claim or move money. The tools are executed by Studio in the browser; this module only
asks a model what to call next.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .ai import TRANSIENT_HTTP, is_transient

log = logging.getLogger("refundpilot")

ASSISTANT_TIMEOUT_MS = 45_000        # per model call; two attempts must fit in one HTTP request
RETRY_DELAY_S = 1.5                  # one retry after a transient failure (rate limit, overload, timeout)
MAX_RESULT_CHARS = 30_000            # one tool result, as JSON text
MAX_TRANSCRIPT_CHARS = 400_000       # the whole conversation; older tool results are dropped first

ENVELOPE_RULES = """\
# How to reply (strict)
You have no native tool calling here. Every reply is exactly one JSON object, with nothing before or after it:

{"message": "<text>", "tool_calls": [{"name": "<tool name>", "arguments": {}}]}

- To use tools, list one or more calls in "tool_calls". Their results arrive in the next message and you continue \
from there. "message" is then "" or one short sentence saying what you are doing.
- When you need no more tools, send "tool_calls": [] and put your whole answer in "message". Markdown is fine there.
- Use only the tools listed below. "arguments" must satisfy that tool's JSON Schema: every required property \
present, no property the schema does not list, and optional properties you do not need left out (never null).
- A tool result with "success": false says what was wrong with the call. Correct the call and make it again.
- Never invent a tool result, and never describe a call instead of making it."""


class AssistantError(Exception):
    """A turn that could not be answered. `status` is the HTTP status the API returns for it."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class ModelReply:
    text: str
    finish_reason: str = "STOP"
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0


class TurnModel(Protocol):
    name: str
    model: str

    def generate(self, system: str, contents: list[tuple[str, str]]) -> ModelReply: ...


@dataclass
class TurnResult:
    response: dict           # AG Studio's AgLlmResponse, as JSON
    model_calls: int
    input_tokens: int
    output_tokens: int


# ----------------------------------------------------------------- request -> prompt

def prompt_schema(node: Any) -> Any:
    """A tool's JSON Schema as the model reads it. Two things in Studio's schemas only cost tokens or mislead:
    `additionalProperties: false` on every object (the reply rules say it once), and the `{"not": {}}` branch Studio
    uses to mark an optional value, which reads as "may be undefined" and is said by leaving the property out."""
    if isinstance(node, list):
        return [prompt_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict = {}
    for key, value in node.items():
        if key == "additionalProperties" and value is False:
            continue
        if key == "anyOf" and isinstance(value, list):
            branches = [prompt_schema(b) for b in value if b != {"not": {}}]
            if len(branches) == 1 and isinstance(branches[0], dict):
                out = {**branches[0], **out}
            elif branches:
                out[key] = branches
            continue
        out[key] = prompt_schema(value)
    return out


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_system(instructions: str, tools: list[dict], tool_choice: Any) -> str:
    """The system instruction: the agent's own instructions from Studio, the reply rules, the tools, and what this
    particular turn requires (Studio can force a tool, for instance to make a delegate hand its result back)."""
    parts = [instructions.strip(), ENVELOPE_RULES]
    if tools and tool_choice != "none":
        catalogue = ["# Tools"]
        for tool in tools:
            catalogue.append(f"## {tool['name']}\n{(tool.get('description') or '').strip()}\n"
                             f"Arguments (JSON Schema): {_compact(prompt_schema(tool.get('parameters') or {}))}")
        parts.append("\n\n".join(catalogue))
    else:
        parts.append('# Tools\nNone in this turn. "tool_calls" must be [].')
    directive = choice_directive(tool_choice)
    if directive:
        parts.append(f"# This turn\n{directive}")
    return "\n\n".join(p for p in parts if p)


def forced_tool(tool_choice: Any) -> str | None:
    return tool_choice.get("name") if isinstance(tool_choice, dict) else None


def choice_directive(tool_choice: Any) -> str:
    name = forced_tool(tool_choice)
    if name:
        return f'Reply with exactly one tool call, to "{name}".'
    if tool_choice == "required":
        return "Reply with at least one tool call."
    if tool_choice == "none":
        return 'Reply without tool calls: "tool_calls" must be [].'
    return ""


def _text_of(item: dict) -> str:
    """The text of a message item. Anything that isn't text (an image, a file) is named, not sent."""
    content = item.get("content")
    if isinstance(content, str):
        return content
    chunks = []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            chunks.append(str(c.get("text") or ""))
        elif c.get("type") == "refusal":
            chunks.append(str(c.get("refusal") or ""))
        else:
            chunks.append(f"[{c.get('type', 'attachment')} omitted]")
    return "\n".join(chunks)


def _loads(text: Any) -> Any:
    """Tool arguments and tool results travel as JSON text. Parsed here so the transcript isn't doubly encoded."""
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def _result_entry(name: str, output: Any) -> dict:
    result = _loads(output)
    if len(_compact(result)) > MAX_RESULT_CHARS:
        result = {"truncated": True, "note": f"The result was longer than {MAX_RESULT_CHARS} characters; this is its "
                                             "start. Narrow the call (fewer rows or fields) if you need the rest.",
                  "start": _compact(result)[:MAX_RESULT_CHARS]}
    return {"tool": name, "result": result}


def render_contents(items: list[dict]) -> list[tuple[str, str]]:
    """Studio's conversation items as alternating ("user" | "model", text) turns.

    One model reply can hold a message and several tool calls. Studio stores those as separate items and puts each
    tool result right after its call, so the items of one reply are found by the `turnId` this module stamps on
    everything it returns (Studio keeps unknown properties for as long as a run lasts). Items without one, such as
    history from before a page reload, are grouped by position, which reads as the same conversation.
    """
    turns: list[list] = []            # [role, text, droppable result entries | None]
    reply: dict | None = None         # the model reply being collected

    def flush() -> None:
        nonlocal reply
        if reply is None:
            return
        calls = [{"name": c["name"], "arguments": c["arguments"]} for c in reply["calls"]]
        turns.append(["model", _compact({"message": "\n".join(m for m in reply["messages"] if m),
                                         "tool_calls": calls}), None])
        if reply["results"]:
            entries = [_result_entry(c["name"], reply["results"].get(c["callId"], "(no result was recorded)"))
                       for c in reply["calls"]]
            turns.append(["user", "", entries])
        reply = None

    for item in items:
        kind = item.get("type")
        if kind == "function_call_output":
            if reply is not None and any(c["callId"] == item.get("callId") for c in reply["calls"]):
                reply["results"][item.get("callId")] = item.get("output")
            else:                     # a result whose call isn't in view: keep it rather than lose information
                flush()
                turns.append(["user", "", [_result_entry("(unknown)", item.get("output"))]])
        elif kind == "message" and item.get("kind") != "output" and item.get("role") != "assistant":
            flush()
            turns.append(["user", _text_of(item), None])
        elif kind in ("message", "function_call"):
            turn_id = item.get("turnId")
            same = reply is not None and ((turn_id is not None and turn_id == reply["id"])
                                          or (turn_id is None and reply["id"] is None and not reply["results"]))
            if not same:
                flush()
                reply = {"id": turn_id, "messages": [], "calls": [], "results": {}}
            if kind == "message":
                reply["messages"].append(_text_of(item))
            else:
                reply["calls"].append({"callId": item.get("callId"), "name": str(item.get("name") or ""),
                                       "arguments": _loads(item.get("arguments") or "{}")})
        # reasoning items and anything unknown are not part of what the model needs to see again
    flush()

    # Budget: the newest results matter most, so the oldest ones give way first.
    def size() -> int:
        return sum(len(t[1]) + (len(_compact(t[2])) if t[2] else 0) for t in turns)

    for t in turns:
        if size() <= MAX_TRANSCRIPT_CHARS:
            break
        if t[2]:
            t[2] = [{"tool": e["tool"], "result": "(dropped to keep the conversation within the size limit)"}
                    for e in t[2]]

    out: list[tuple[str, str]] = []
    for role, text, entries in turns:
        if entries is not None:
            text = "Tool results, in the order of your calls:\n" + _compact(entries)
        if not text.strip():
            continue
        if out and out[-1][0] == role == "user":
            out[-1] = (role, out[-1][1] + "\n\n" + text)
            continue
        if out and out[-1][0] == role:           # two replies in a row (Studio asked again without new input): the
            out.append(("user", "Continue."))    # API wants the roles to alternate, one envelope per model turn
        out.append((role, text))
    if not out or out[0][0] != "user":
        out.insert(0, ("user", "(The conversation starts here.)"))
    if out[-1][0] != "user":
        out.append(("user", "Continue."))
    return out


# ----------------------------------------------------------------- reply -> Studio output items

def _strip_nulls(value: Any) -> Any:
    """Studio's tools treat an optional property as absent, not null. Null array items are left alone: some are
    meaningful (an open-ended range), and the rest are for Studio's validation to report."""
    if isinstance(value, list):
        return [_strip_nulls(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None}
    return value


def parse_envelope(text: str) -> tuple[str, list[dict]]:
    """The model's reply as (message, calls). Raises ValueError with a sentence the model can act on."""
    raw = (text or "").strip()
    if raw.startswith("```"):                     # JSON mode shouldn't fence its output, but it costs nothing
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("the reply was not valid JSON") from None
    if isinstance(data, list):
        data = {"tool_calls": data}
    if not isinstance(data, dict):
        raise ValueError("the reply was not a JSON object")
    if "tool_calls" not in data and "message" not in data and isinstance(data.get("name"), str):
        data = {"tool_calls": [data]}             # a single bare call
    message = data.get("message")
    if message is None:
        message = ""
    if not isinstance(message, str):
        raise ValueError('"message" must be a string')
    raw_calls = data.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise ValueError('"tool_calls" must be a list')
    calls = []
    for c in raw_calls:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"]:
            raise ValueError('every entry of "tool_calls" needs a "name"')
        args = _loads(c.get("arguments", c.get("args", {})))
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ValueError(f'the "arguments" of {c["name"]} must be a JSON object')
        calls.append({"name": c["name"], "arguments": _strip_nulls(args)})
    return message.strip(), calls


def check_calls(message: str, calls: list[dict], tools: list[dict], tool_choice: Any) -> str | None:
    """Why this reply can't be handed to Studio, or None if it can."""
    known = {t["name"] for t in tools}
    unknown = sorted({c["name"] for c in calls} - known)
    if unknown:
        return f"there is no tool named {', '.join(unknown)}; the tools are: {', '.join(sorted(known)) or 'none'}"
    name = forced_tool(tool_choice)
    if name and [c["name"] for c in calls] != [name]:
        return f'this turn needs exactly one tool call, to "{name}"'
    if tool_choice == "required" and not calls:
        return "this turn needs at least one tool call"
    if tool_choice == "none" and calls:
        return "this turn allows no tool calls"
    if not calls and not message:
        return 'the reply had neither a "message" nor a tool call'
    return None


def fill_required_lists(calls: list[dict], tools: list[dict]) -> None:
    """A required list the model left out becomes an empty list ("artifacts" of a delegate's hand-back, say), unless
    the schema asks for at least one item. It saves a round trip through Studio's validation for a formality."""
    by_name = {t["name"]: t.get("parameters") or {} for t in tools}
    for call in calls:
        schema = by_name.get(call["name"], {})
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            spec = props.get(key) or {}
            if key not in call["arguments"] and spec.get("type") == "array" and not spec.get("minItems"):
                call["arguments"][key] = []


def forced_call_from_text(name: str, message: str, tools: list[dict]) -> dict | None:
    """Last resort when Studio forces a tool and the model answered in prose twice: if the tool takes one required
    string (a delegate's "summary"), the prose becomes that string. This is what ends a delegate's run; without it
    the loop would go on to its turn limit."""
    schema = next((t.get("parameters") or {} for t in tools if t["name"] == name), None)
    if schema is None or not message:
        return None
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    strings = [k for k in required if (props.get(k) or {}).get("type") == "string" and "enum" not in props[k]]
    others = [k for k in required if k not in strings]
    if len(strings) != 1 or any((props.get(k) or {}).get("type") != "array" for k in others):
        return None
    return {"name": name, "arguments": {strings[0]: message, **{k: [] for k in others}}}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def build_response(message: str, calls: list[dict], model: str, usage: dict) -> dict:
    turn_id = _new_id("turn")
    output: list[dict] = []
    if message:
        output.append({"id": _new_id("msg"), "kind": "output", "type": "message", "role": "assistant",
                       "status": "completed", "turnId": turn_id,
                       "content": [{"type": "text", "text": message, "annotations": []}]})
    for call in calls:
        output.append({"id": _new_id("fc"), "kind": "output", "type": "function_call", "callId": _new_id("call"),
                       "name": call["name"], "arguments": _compact(call["arguments"]), "status": "completed",
                       "turnId": turn_id})
    return {"id": _new_id("resp"), "createdAt": int(time.time()), "model": model, "status": "completed",
            "output": output, "usage": usage}


# ----------------------------------------------------------------- one turn

def _call_model(model: TurnModel, system: str, contents: list[tuple[str, str]], sleep: Callable[[float], None]) -> ModelReply:
    for attempt in (1, 2):
        try:
            return model.generate(system, contents)
        except Exception as e:  # network, quota, bad key
            code = getattr(e, "code", None)
            log.warning("assistant: model call failed (attempt %d): %s: %s", attempt, type(e).__name__, str(e)[:200])
            if attempt == 1 and is_transient(e):
                sleep(RETRY_DELAY_S)
                continue
            if code == 429:
                raise AssistantError(429, "The AI model is rate limited right now. Try again in a minute.") from e
            if isinstance(code, int) and code in TRANSIENT_HTTP or is_transient(e):
                raise AssistantError(503, "The AI model did not answer in time. Try again.") from e
            detail = str(getattr(e, "message", "") or "")[:200]     # the provider's own sentence, for diagnosis
            raise AssistantError(502, f"The AI model rejected the request ({type(e).__name__}"
                                      f"{f' {code}' if code else ''}{f': {detail}' if detail else ''}).") from e
    raise AssertionError("unreachable")


def run_turn(model: TurnModel, *, items: list[dict], instructions: str, tools: list[dict], tool_choice: Any = "auto",
             sleep: Callable[[float], None] = time.sleep) -> TurnResult:
    """One model turn for Studio. The model gets one chance to repair a reply that can't be used."""
    system = render_system(instructions, tools, tool_choice)
    contents = render_contents(items)
    directive = choice_directive(tool_choice)
    if directive:                     # said again where the model reads last
        contents[-1] = ("user", f"{contents[-1][1]}\n\n({directive})")

    calls_made = 0
    tokens = {"inputTokens": 0, "outputTokens": 0, "reasoningTokens": 0, "cachedInputTokens": 0}
    message, calls, problem = "", [], "no reply"
    for attempt in (1, 2):
        reply = _call_model(model, system, contents, sleep)
        calls_made += 1
        tokens["inputTokens"] += reply.input_tokens
        tokens["outputTokens"] += reply.output_tokens
        tokens["reasoningTokens"] += reply.reasoning_tokens
        tokens["cachedInputTokens"] += reply.cached_tokens
        try:
            message, calls = parse_envelope(reply.text)
            fill_required_lists(calls, tools)
            problem = check_calls(message, calls, tools, tool_choice)
        except ValueError as e:
            message, calls, problem = "", [], str(e)
            if reply.finish_reason == "MAX_TOKENS":
                problem = "the reply was cut off at the length limit; answer more briefly"
            elif not (reply.text or "").strip():
                problem = f"the reply was empty (finish reason {reply.finish_reason})"
        if problem is None:
            break
        log.warning("assistant: unusable reply (attempt %d): %s", attempt, problem)
        if attempt == 1:
            contents = contents + [("model", (reply.text or "").strip()[:4000] or "{}"),
                                   ("user", f"That reply could not be used: {problem}. Reply again with one JSON "
                                            f"object in the required shape.{f' ({directive})' if directive else ''}")]

    if problem is not None:               # still unusable: keep what can be kept, otherwise say so
        name = forced_tool(tool_choice)
        rescued = forced_call_from_text(name, message, tools) if name else None
        known = {t["name"] for t in tools}
        if rescued:
            message, calls = "", [rescued]
        elif tool_choice == "auto" and (message or any(c["name"] in known for c in calls)):
            calls = [c for c in calls if c["name"] in known]
        elif tool_choice == "none" and message:
            calls = []
        else:
            raise AssistantError(502, f"The AI model's reply could not be used: {problem}.")

    tokens["totalTokens"] = tokens["inputTokens"] + tokens["outputTokens"] + tokens["reasoningTokens"]
    return TurnResult(response=build_response(message, calls, model.model, tokens), model_calls=calls_made,
                      input_tokens=tokens["inputTokens"], output_tokens=tokens["outputTokens"] + tokens["reasoningTokens"])


# ----------------------------------------------------------------- Gemini

class GeminiTurns:
    name = "gemini"

    def __init__(self, api_key: str, model: str, max_output_tokens: int = 16384, thinking_level: str = ""):
        from google import genai
        from google.genai import types
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(api_version="v1beta",
                                                                                   timeout=ASSISTANT_TIMEOUT_MS))
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.thinking_level = thinking_level

    def generate(self, system: str, contents: list[tuple[str, str]]) -> ModelReply:
        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json",
            max_output_tokens=self.max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level) if self.thinking_level else None)
        resp = self.client.models.generate_content(
            model=self.model, config=config,
            contents=[types.Content(role=role, parts=[types.Part(text=text)]) for role, text in contents])
        candidate = (resp.candidates or [None])[0]
        finish = getattr(getattr(candidate, "finish_reason", None), "name", None) or "UNKNOWN"
        if candidate is None:         # nothing generated: the prompt itself was blocked
            finish = getattr(getattr(getattr(resp, "prompt_feedback", None), "block_reason", None), "name", None) or finish
        try:
            text = resp.text or ""
        except Exception:             # no text parts in the candidate
            text = ""
        um = getattr(resp, "usage_metadata", None)
        return ModelReply(text=text, finish_reason=finish,
                          input_tokens=getattr(um, "prompt_token_count", 0) or 0,
                          output_tokens=getattr(um, "candidates_token_count", 0) or 0,
                          reasoning_tokens=getattr(um, "thoughts_token_count", 0) or 0,
                          cached_tokens=getattr(um, "cached_content_token_count", 0) or 0)


def build_turn_model(settings) -> TurnModel | None:
    """The assistant's model, or None when it is switched off or has no key. It can have its own key and model
    (ASSISTANT_API_KEY, ASSISTANT_MODEL) so its usage never competes with claim assessments for the same quota."""
    key = settings.assistant_api_key or settings.gemini_api_key
    if not settings.assistant_enabled or not key:
        return None
    try:
        return GeminiTurns(key, settings.assistant_model or settings.gemini_model,
                           max_output_tokens=settings.assistant_max_output_tokens,
                           thinking_level=settings.assistant_thinking_level)
    except ImportError:
        log.warning("the assistant has a key but google-genai is not installed; run pip install -e .")
        return None

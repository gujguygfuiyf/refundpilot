"""The Insights assistant: an AG Studio turn becomes a Gemini prompt, and the reply becomes Studio's output items.
The model is scripted. The request fixtures are written by hand in the shape AG Studio 3.1.0 sends: items with a
kind and a type, recursive $defs, the {"not": {}} branch for an optional value, a forced tool choice."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.assistant import (AssistantError, ModelReply, parse_envelope, prompt_schema, render_contents,
                           render_system, run_turn)
from app.db import get_db
from app.main import app
from app.models import AssistantUsage

STUDIO = json.loads((Path(__file__).parent / "fixtures" / "studio_turn_data_agent.json").read_text(encoding="utf-8"))
MERCHANT = {"Authorization": "Bearer test-merchant"}
COMPLETE_TASK = next(t for t in STUDIO["tools"] if t["name"] == "complete_task")
VIEW_SCHEMA = next(t for t in STUDIO["tools"] if t["name"] == "view_schema")


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"{code} from the provider")
        self.code = code


class Scripted:
    """A model that answers from a list and records what it was asked."""
    name = "scripted"
    model = "scripted-1"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen: list[tuple[str, list]] = []

    def generate(self, system, contents):
        self.seen.append((system, contents))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, ModelReply):
            return reply
        return ModelReply(text=reply if isinstance(reply, str) else json.dumps(reply), input_tokens=100, output_tokens=10)


def user(text):
    return {"id": "m1", "kind": "input", "type": "message", "role": "user", "status": "completed",
            "content": [{"type": "text", "text": text}]}


def turn(model, **kw):
    kw.setdefault("items", [user("How many runs were overpaid?")])
    kw.setdefault("instructions", "You are the data agent.")
    kw.setdefault("tools", STUDIO["tools"])
    return run_turn(model, sleep=lambda s: None, **kw)


# ----------------------------------------------------------------- request -> prompt

def test_prompt_schema_drops_what_only_costs_tokens():
    schema = {"type": "object", "additionalProperties": False, "required": ["a"],
              "properties": {"a": {"anyOf": [{"type": "string"}, {"not": {}}], "description": "optional text"},
                             "b": {"anyOf": [{"type": "number"}, {"type": "null"}, {"not": {}}]},
                             "c": {"$ref": "#/$defs/C"}},
              "$defs": {"C": {"type": "object", "additionalProperties": False, "properties": {"c": {"$ref": "#/$defs/C"}}}}}
    out = prompt_schema(schema)
    assert out["properties"]["a"] == {"type": "string", "description": "optional text"}
    assert out["properties"]["b"] == {"anyOf": [{"type": "number"}, {"type": "null"}]}
    assert out["$defs"]["C"] == {"type": "object", "properties": {"c": {"$ref": "#/$defs/C"}}}   # recursion is kept
    assert "additionalProperties" not in json.dumps(out) and '"not"' not in json.dumps(out)
    assert out["required"] == ["a"]


def test_system_instruction_carries_the_agent_the_rules_and_every_tool():
    system = render_system(STUDIO["instructions"], STUDIO["tools"], STUDIO["tool_choice"])
    assert system.startswith(STUDIO["instructions"].strip()[:200])
    assert '{"message": "<text>", "tool_calls"' in system
    for tool in STUDIO["tools"]:
        assert f"## {tool['name']}\n" in system
    assert '"$ref":"#/$defs/Expression"' in system               # recursive schemas go through as they are
    assert '{"not":{}}' not in system and '"additionalProperties":false' not in system
    assert system.rstrip().endswith('Reply with exactly one tool call, to "complete_task".')


def test_no_tools_are_listed_when_the_turn_allows_none():
    system = render_system("Be brief.", STUDIO["tools"], "none")
    assert "## execute_query" not in system and '"tool_calls" must be []' in system


def test_a_studio_conversation_becomes_alternating_turns():
    contents = render_contents(STUDIO["input"])
    roles = [role for role, _ in contents]
    assert roles[0] == "user" and roles[-1] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert contents[0][1] == "How many evaluation runs were overpaid?"
    first_reply = json.loads(contents[1][1])                    # every model turn reads as one envelope
    assert first_reply == {"message": "", "tool_calls": [{"name": "view_schema", "arguments": {}}]}
    results = json.loads(contents[2][1].split("\n", 1)[1])
    assert results[0]["tool"] == "view_schema" and results[0]["result"]["success"] is True    # not doubly encoded
    for role, text in contents:
        if role == "model":
            assert set(json.loads(text)) == {"message", "tool_calls"}
    assert '"issues"' in contents[-3][1] or '"issues"' in contents[-1][1]      # the failed call's reason is kept


def test_calls_of_one_reply_are_grouped_by_turn_id_even_when_results_sit_between_them():
    items = [user("Compare the policies"),
             {"type": "function_call", "kind": "output", "callId": "c1", "name": "simulate", "arguments": '{"p":"a"}', "turnId": "t1"},
             {"type": "function_call_output", "callId": "c1", "output": '{"success":true,"response":"$10"}'},
             {"type": "function_call", "kind": "output", "callId": "c2", "name": "simulate", "arguments": '{"p":"b"}', "turnId": "t1"},
             {"type": "function_call_output", "callId": "c2", "output": '{"success":true,"response":"$20"}'}]
    contents = render_contents(items)
    assert [role for role, _ in contents] == ["user", "model", "user"]
    assert [c["arguments"]["p"] for c in json.loads(contents[1][1])["tool_calls"]] == ["a", "b"]
    assert [r["result"]["response"] for r in json.loads(contents[2][1].split("\n", 1)[1])] == ["$10", "$20"]


def test_history_without_turn_ids_still_reads_in_order():
    items = [user("q"),
             {"type": "function_call", "kind": "output", "callId": "c1", "name": "a", "arguments": "{}"},
             {"type": "function_call_output", "callId": "c1", "output": '"one"'},
             {"type": "function_call", "kind": "output", "callId": "c2", "name": "b", "arguments": "{}"},
             {"type": "function_call_output", "callId": "c2", "output": '"two"'},
             {"type": "message", "kind": "output", "role": "assistant", "content": [{"type": "text", "text": "Done.", "annotations": []}]},
             user("thanks")]
    contents = render_contents(items)
    assert [role for role, _ in contents] == ["user", "model", "user", "model", "user", "model", "user"]
    assert json.loads(contents[5][1]) == {"message": "Done.", "tool_calls": []}


def test_an_oversized_tool_result_is_cut_with_a_note():
    rows = [{"n": i, "text": "x" * 100} for i in range(1000)]
    items = [user("q"),
             {"type": "function_call", "kind": "output", "callId": "c1", "name": "execute_query", "arguments": "{}", "turnId": "t"},
             {"type": "function_call_output", "callId": "c1", "output": json.dumps({"success": True, "data": rows})}]
    text = render_contents(items)[-1][1]
    assert len(text) < 40_000 and '"truncated":true' in text


# ----------------------------------------------------------------- reply -> Studio items

def test_a_tool_call_reply_becomes_function_call_items():
    model = Scripted({"message": "Reading the schema.", "tool_calls": [{"name": "view_schema", "arguments": {}}]})
    result = turn(model)
    msg, call = result.response["output"]
    assert result.response["status"] == "completed" and result.response["model"] == "scripted-1"
    assert msg["type"] == "message" and msg["content"][0] == {"type": "text", "text": "Reading the schema.", "annotations": []}
    assert call["type"] == "function_call" and call["kind"] == "output" and call["name"] == "view_schema"
    assert call["arguments"] == "{}" and call["callId"].startswith("call_") and call["turnId"] == msg["turnId"]
    assert result.response["usage"]["inputTokens"] == 100 and result.response["usage"]["totalTokens"] == 110
    assert result.model_calls == 1


def test_a_final_answer_becomes_one_message():
    result = turn(Scripted({"message": "No run was overpaid: **0 of 123**.", "tool_calls": []}))
    assert [o["type"] for o in result.response["output"]] == ["message"]


def test_what_this_module_returns_reads_back_as_the_same_turn():
    first = turn(Scripted({"message": "", "tool_calls": [{"name": "view_schema", "arguments": {}},
                                                           {"name": "view_schema", "arguments": {}}]}))
    a, b = first.response["output"]
    history = [user("q"), a, {"type": "function_call_output", "callId": a["callId"], "output": '"A"'},
               b, {"type": "function_call_output", "callId": b["callId"], "output": '"B"'}]
    contents = render_contents(history)
    assert [role for role, _ in contents] == ["user", "model", "user"]
    assert len(json.loads(contents[1][1])["tool_calls"]) == 2


def test_optional_nulls_are_removed_and_a_missing_required_list_is_filled():
    model = Scripted({"message": "", "tool_calls": [{"name": "complete_task", "arguments": {"summary": "Done", "extra": None}}]})
    call = turn(model).response["output"][0]
    assert json.loads(call["arguments"]) == {"summary": "Done", "artifacts": []}


def test_an_unusable_reply_gets_one_chance_to_be_repaired():
    model = Scripted("I will look at the schema first.", {"message": "", "tool_calls": [{"name": "view_schema", "arguments": {}}]})
    result = turn(model)
    assert result.model_calls == 2 and result.response["output"][0]["name"] == "view_schema"
    assert "could not be used: the reply was not valid JSON" in model.seen[1][1][-1][1]


def test_an_unknown_tool_is_refused_then_left_out():
    bad = {"message": "Here is what I found.", "tool_calls": [{"name": "delete_everything", "arguments": {}}]}
    model = Scripted(bad, bad)
    result = turn(model)
    assert [o["type"] for o in result.response["output"]] == ["message"]
    assert "there is no tool named delete_everything" in model.seen[1][1][-1][1]


def test_a_forced_tool_is_rescued_from_prose_so_a_delegate_always_ends():
    prose = {"message": "No run was overpaid.", "tool_calls": []}
    model = Scripted(prose, prose)
    result = turn(model, tool_choice={"name": "complete_task"})
    (call,) = result.response["output"]
    assert call["name"] == "complete_task"
    assert json.loads(call["arguments"]) == {"summary": "No run was overpaid.", "artifacts": []}
    assert model.seen[0][1][-1][1].endswith('(Reply with exactly one tool call, to "complete_task".)')


def test_a_reply_that_stays_unusable_is_an_error_not_a_guess():
    with pytest.raises(AssistantError) as e:
        turn(Scripted("nope", "still nope"))
    assert e.value.status == 502 and "not valid JSON" in e.value.message


def test_a_cut_off_reply_says_so():
    cut = ModelReply(text='{"message": "The answer is', finish_reason="MAX_TOKENS")
    with pytest.raises(AssistantError) as e:
        turn(Scripted(cut, cut))
    assert "cut off" in e.value.message


def test_envelope_parsing_is_tolerant_where_it_is_safe():
    assert parse_envelope('```json\n{"message": "hi", "tool_calls": []}\n```') == ("hi", [])
    assert parse_envelope('{"name": "view_schema", "arguments": "{}"}') == ("", [{"name": "view_schema", "arguments": {}}])
    assert parse_envelope('[{"name": "a", "arguments": {"x": [1, null]}}]') == ("", [{"name": "a", "arguments": {"x": [1, None]}}])
    with pytest.raises(ValueError):
        parse_envelope('{"message": "hi", "tool_calls": [{"arguments": {}}]}')


def test_a_transient_failure_is_retried_once():
    model = Scripted(ApiError(503), {"message": "ok", "tool_calls": []})
    assert turn(model).response["output"][0]["content"][0]["text"] == "ok"


def test_provider_errors_map_to_clear_statuses():
    with pytest.raises(AssistantError) as quota:
        turn(Scripted(ApiError(429), ApiError(429)))
    assert quota.value.status == 429
    with pytest.raises(AssistantError) as bad:
        turn(Scripted(ApiError(400)))
    assert bad.value.status == 502 and "400" in bad.value.message


# ----------------------------------------------------------------- HTTP

@pytest.fixture
def client(db):
    from app.routers.assistant import per_ip
    per_ip.reset()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        per_ip.reset()


def body(**kw):
    return {"input": [user("Summarise this page")], "instructions": "You are the lead.", "tools": [VIEW_SCHEMA], **kw}


def test_assistant_is_off_without_a_key(client):
    app.dependency_overrides[deps.get_turn_model] = lambda: None
    assert client.get("/merchant/assistant/status").status_code == 401
    status = client.get("/merchant/assistant/status", headers=MERCHANT).json()
    assert status["enabled"] is False and "GEMINI_API_KEY" in status["reason"]
    assert client.post("/merchant/assistant/turn", json=body(), headers=MERCHANT).status_code == 503


def test_turn_endpoint_returns_studio_items_and_counts_usage(client, db):
    model = Scripted({"message": "", "tool_calls": [{"name": "view_schema", "arguments": {}}]},
                     "not json", {"message": "Two tables.", "tool_calls": []})
    app.dependency_overrides[deps.get_turn_model] = lambda: model
    assert client.get("/merchant/assistant/status", headers=MERCHANT).json() == {"enabled": True, "model": "scripted-1"}
    assert client.post("/merchant/assistant/turn", json=body()).status_code == 401

    r = client.post("/merchant/assistant/turn", json=body(tool_choice="auto"), headers=MERCHANT)
    assert r.status_code == 200 and r.json()["output"][0]["name"] == "view_schema"
    r = client.post("/merchant/assistant/turn", json=body(), headers=MERCHANT)       # one repair inside this turn
    assert r.json()["output"][0]["content"][0]["text"] == "Two tables."
    usage = db.query(AssistantUsage).one()
    assert usage.turns == 3 and usage.input_tokens == 300 and usage.output_tokens == 30


def test_a_studio_shaped_request_is_accepted_as_it_is(client):
    model = Scripted({"message": "", "tool_calls": [{"name": "complete_task", "arguments": {"summary": "No run was overpaid.", "artifacts": []}}]})
    app.dependency_overrides[deps.get_turn_model] = lambda: model
    r = client.post("/merchant/assistant/turn", json=STUDIO, headers=MERCHANT)
    assert r.status_code == 200 and r.json()["output"][0]["name"] == "complete_task"
    system, contents = model.seen[0]
    assert "## execute_query" in system and contents[-1][0] == "user"


def test_a_lead_agent_request_offers_delegation_to_the_policy_analyst(client):
    lead = json.loads((Path(__file__).parent / "fixtures" / "studio_turn_lead.json").read_text(encoding="utf-8"))
    model = Scripted({"message": "", "tool_calls": [{"name": "delegate_to", "arguments": {
        "type": {"id": "refund-policy"}, "instructions": "List the refund policies."}}]})
    app.dependency_overrides[deps.get_turn_model] = lambda: model
    r = client.post("/merchant/assistant/turn", json=lead, headers=MERCHANT)
    assert r.status_code == 200 and r.json()["output"][0]["name"] == "delegate_to"
    system, contents = model.seen[0]
    delegate = system.split("## delegate_to\n", 1)[1].split("\n## ", 1)[0]
    assert '"enum":["refund-policy"]' in delegate              # our own agent sits beside Studio's built-in ones
    assert contents == [("user", lead["input"][0]["content"][0]["text"])]


def test_daily_ceiling_and_rate_limit(client, monkeypatch):
    from app.config import settings
    ok = {"message": "ok", "tool_calls": []}
    app.dependency_overrides[deps.get_turn_model] = lambda: Scripted(ok, ok, ok)
    monkeypatch.setattr(settings, "assistant_turns_per_day", 2)
    assert client.post("/merchant/assistant/turn", json=body(), headers=MERCHANT).status_code == 200
    assert client.post("/merchant/assistant/turn", json=body(), headers=MERCHANT).status_code == 200
    r = client.post("/merchant/assistant/turn", json=body(), headers=MERCHANT)
    assert r.status_code == 429 and "today's usage limit" in r.json()["detail"]

    monkeypatch.setattr(settings, "assistant_turns_per_day", 0)
    monkeypatch.setattr(settings, "assistant_turns_per_ip_per_minute", 3)       # three hits were just recorded
    r = client.post("/merchant/assistant/turn", json=body(), headers=MERCHANT)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_provider_failure_reaches_the_browser_as_a_sentence(client):
    app.dependency_overrides[deps.get_turn_model] = lambda: Scripted(ApiError(429), ApiError(429))
    r = client.post("/merchant/assistant/turn", json=body(), headers=MERCHANT)
    assert r.status_code == 429 and "rate limited" in r.json()["detail"]


def test_malformed_requests_are_rejected(client):
    app.dependency_overrides[deps.get_turn_model] = lambda: Scripted()
    assert client.post("/merchant/assistant/turn", json=body(input=[]), headers=MERCHANT).status_code == 422
    assert client.post("/merchant/assistant/turn", json=body(tools=[{"name": "bad name!", "parameters": {}}]),
                       headers=MERCHANT).status_code == 422
    assert client.post("/merchant/assistant/turn", json=body(instructions="x" * 90_000), headers=MERCHANT).status_code == 422

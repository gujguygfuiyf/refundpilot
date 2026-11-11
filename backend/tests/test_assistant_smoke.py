"""scripts/assistant_smoke.py with a scripted model in place of Gemini: the checks, the verdicts, the exit codes."""
import importlib.util
import json
from pathlib import Path

from app.assistant import ModelReply

spec = importlib.util.spec_from_file_location(
    "assistant_smoke", Path(__file__).resolve().parent.parent / "scripts" / "assistant_smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class Scripted:
    name = "scripted"
    model = "scripted-1"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def generate(self, system, contents):
        self.seen.append((system, contents))
        return ModelReply(text=json.dumps(self.replies.pop(0)), input_tokens=50, output_tokens=5)


def call(name, **arguments):
    return {"message": "", "tool_calls": [{"name": name, "arguments": arguments}]}


def answer(text):
    return {"message": text, "tool_calls": []}


def run(monkeypatch, capsys, model):
    monkeypatch.setattr(smoke, "build_turn_model", lambda settings: model)
    code = smoke.main()
    return code, capsys.readouterr().out


def test_every_check_passes_with_well_behaved_replies(monkeypatch, capsys):
    # Fixtures run in name order (data agent, then lead), then the two-turn conversation.
    model = Scripted(call("complete_task", summary="No run was overpaid — 0 of 123.", artifacts=[]),
                     call("delegate_to", type={"id": "refund-policy"}, instructions="List the policies."),
                     call("get_refund_cap", sku="MUG-01"),
                     answer("The refund cap for MUG-01 is $37.25."))
    code, out = run(monkeypatch, capsys, model)
    assert code == 0 and out.rstrip().endswith("SMOKE: 3/3 passed")
    assert "[studio_turn_data_agent]" in out and "[studio_turn_lead]" in out
    assert "tool call:    delegate_to" in out and "model calls:  1" in out
    assert out.isascii()                                   # the em dash in the summary must not reach the console
    assert model.seen[0][0][:200] not in out               # nor does the system instruction
    second_turn = model.seen[3][1]
    assert '"cap_usd":37.25' in second_turn[-1][1]         # the script played Studio and returned the result


def test_a_delegate_that_does_not_hand_back_and_a_guessed_number_fail(monkeypatch, capsys):
    model = Scripted(call("complete_task", summary="  ", artifacts=[]),
                     answer("This page shows the evaluation runs."),
                     call("get_refund_cap", sku="MUG-01"),
                     answer("The cap is about $40."))
    code, out = run(monkeypatch, capsys, model)
    assert code == 1 and "SMOKE: 1/3 passed" in out
    assert "FAIL: complete_task without a summary" in out and "FAIL: the answer does not mention 37.25" in out


def test_the_script_stops_with_a_sentence_when_the_assistant_is_off(monkeypatch, capsys):
    monkeypatch.setattr(smoke.settings, "assistant_enabled", False)
    code, out = run(monkeypatch, capsys, None)
    assert code == 2 and "ASSISTANT_ENABLED=true" in out

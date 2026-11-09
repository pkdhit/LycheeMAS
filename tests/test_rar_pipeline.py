"""检查真实运行适配的可见性、盲标隔离、错误和续跑契约；不调用 API。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rar_api_utils as api  # noqa: E402
import run_rar_pipeline as pipeline  # noqa: E402
from lychee_mas.backends.openai_api_backend import APIGenResult  # noqa: E402
from lychee_mas.methods.postrun.rar_trace_adapter import prepare_steps  # noqa: E402


def test_generation_uses_existing_prompts_and_actual_visible_history():
    if importlib.util.find_spec("langgraph") is None:
        pytest.skip("requires langgraph extra")
    seen = []

    def call(key, messages):
        seen.append(messages)
        return ["private plan", "candidate result", "APPROVE: 42"][len(seen) - 1]

    trajectory = pipeline.generate(
        {"question": "6*7?", "trajectory_id": "t", "task_id": "q", "source_index": 0},
        {"team": "reason"}, SimpleNamespace(call=call))
    steps = prepare_steps(trajectory)
    assert [s.message.meta["visible_message_ids"] for s in steps] == [
        ["u0"], ["u0", "m-planner"], ["u0", "m-solver"]]
    assert "private plan" not in seen[2][1]["content"]
    assert "candidate result" in seen[2][1]["content"]
    assert seen[0][0]["content"] == pipeline.team_to_agentspecs("reason")[0].system_prompt

    class Scorer:
        def scoring(self, text):
            prompt = json.loads(text)
            if prompt["stage"] == "rubric":
                assert "output" not in prompt
                assert prompt["context"]["message_id"] not in {
                    h["message_id"] for h in prompt["context"]["history"]}
                return json.dumps({"rubrics": [{"id": "c1", "description": "correct",
                                              "weight": 1, "rationale": "required"}]})
            return json.dumps({"judgements": [{"criterion_id": "c1", "satisfied": False,
                                              "reason": "INITIAL_JUDGE_SECRET"}]})

    pipeline.score(trajectory, {"max_rubrics": 6}, Scorer(), Scorer())
    requests = pipeline.blind_requests(trajectory)
    encoded = json.dumps(requests)
    assert "INITIAL_JUDGE_SECRET" not in encoded
    assert '"judgements"' not in encoded and '"reward"' not in encoded
    assert len(requests) == 3


def test_cache_does_not_reuse_truncation_or_changed_request(tmp_path):
    responses = iter([APIGenResult("partial", 1, 1, 0.1, finish_reason="length"),
                      APIGenResult("OK", 1, 1, 0.1, finish_reason="stop")])
    backend = SimpleNamespace(model_name="test", temperature=0,
                              generate_chat=lambda *a, **kw: next(responses))
    calls = api.AuditedCalls(tmp_path, backend, 100)
    messages = [{"role": "user", "content": "hello"}]
    with pytest.raises(ValueError, match="未完整完成"):
        calls.call("one", messages)
    assert not (tmp_path / "one/success.json").exists()
    assert calls.call("one", messages) == "OK"
    assert calls.call("one", messages) == "OK"  # no third response exists
    with pytest.raises(ValueError, match="请求已改变"):
        calls.call("one", [{"role": "user", "content": "changed"}])


def test_environment_is_required_and_standard_names_take_precedence(monkeypatch):
    for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "RAR_API_BASE_URL", "RAR_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        api.backend_for("test", 0)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(api, "OpenAICompatibleBackend", lambda **kw: kw)
    assert api.backend_for("test", 0)["api_key_env"] == "OPENAI_API_KEY"


def test_judge_rejects_string_boolean():
    prompt = {"stage": "judge", "rubrics": [{"id": "c", "description": "test",
                                             "weight": 1, "rationale": "test"}]}
    with pytest.raises(ValueError, match="布尔"):
        api.validate_scoring(prompt, json.dumps({"judgements": [
            {"criterion_id": "c", "satisfied": "false", "reason": "test"}]}))

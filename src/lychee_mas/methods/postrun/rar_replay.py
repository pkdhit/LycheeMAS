"""显式离线回放回调：固定样例只验证调用契约，不代表 LLM 的语义判断能力。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .rar_trace_adapter import strict_json


class ReplayCallbacks:
    """按 message_id 和 stage 回放 JSON；验证上下文和输出与录制样例一致。"""

    def __init__(self, path: str | Path) -> None:
        data = strict_json(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("mode") != "offline_replay":
            raise ValueError("回放文件必须声明 mode=offline_replay")
        self.entries = data.get("steps")
        if not isinstance(self.entries, dict) or not self.entries:
            raise ValueError("回放文件缺少 steps 对象")
        self.calls: list[dict[str, Any]] = []
        self.used: set[tuple[str, str]] = set()

    def rubric(self, prompt: str) -> str:
        return self._reply(prompt, "rubric")

    def judge(self, prompt: str) -> str:
        return self._reply(prompt, "judge")

    def _reply(self, prompt: str, stage: str) -> str:
        request = strict_json(prompt)
        if request.get("stage") != stage:
            raise ValueError("回放调用 stage 不匹配")
        context = request["context"]
        message_id = context["message_id"]
        key = (message_id, stage)
        if key in self.used or message_id not in self.entries:
            raise ValueError(f"重复或未提供回放回复: {key}")
        entry = self.entries[message_id]
        if entry["context"] != context:
            raise ValueError(f"{message_id}: 回放上下文不匹配，禁止套用另一任务的回复")
        if stage == "rubric" and "output" in request:
            raise ValueError("细则生成阶段泄漏实际输出")
        if stage == "judge":
            if request["output"] != entry["output"]:
                raise ValueError("评审输出与回放样例不同")
            if request["rubrics"] != entry["rubric_response"]["rubrics"]:
                raise ValueError("评审细则与回放样例不同")
        response = json.dumps(entry[f"{stage}_response"], ensure_ascii=False, allow_nan=False)
        self.used.add(key)
        self.calls.append({"stage": stage, "message_id": message_id,
                           "request": request, "response": strict_json(response)})
        return response

    def require_complete(self) -> None:
        expected = {(mid, stage) for mid in self.entries for stage in ("rubric", "judge")}
        if self.used != expected:
            raise ValueError(f"存在未消费的回放回复: {sorted(expected - self.used)}")

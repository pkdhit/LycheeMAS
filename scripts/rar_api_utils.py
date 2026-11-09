"""API 实验脚本共用：环境配置、可审计调用与断点缓存。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from lychee_mas.backends.openai_api_backend import OpenAICompatibleBackend
from lychee_mas.methods.postrun.rar_export import write_json
from lychee_mas.methods.postrun.rar_trace_adapter import strict_json
from lychee_mas.methods.postrun.rubric_step_reward import (
    _coerce_judgements,
    _coerce_rubrics,
    _json_from_text,
)

SCORING_SYSTEM = (
    "严格遵循评分任务，只输出一个完整合法的JSON对象。"
    "不输出Markdown、前后说明或思考过程。"
    "description、rationale、reason保持简短，分别尽量不超过80字。"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_for(stage: str) -> str:
    name = os.environ.get(f"RAR_{stage.upper()}_MODEL") or os.environ.get("RAR_API_MODEL")
    if not name:
        raise ValueError(f"请设置 RAR_{stage.upper()}_MODEL 或 RAR_API_MODEL")
    return name


def backend_for(model: str, temperature: float) -> OpenAICompatibleBackend:
    # 不回退到任何供应商地址；URL 和密钥只从环境读取，也不写入运行快照。
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("RAR_API_BASE_URL")
    key_env = "OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else "RAR_API_KEY"
    if not base_url or not os.environ.get(key_env):
        raise ValueError("请设置 OPENAI_BASE_URL 和 OPENAI_API_KEY（或 RAR_API_*）")
    return OpenAICompatibleBackend(
        model=model, base_url=base_url, api_key_env=key_env,
        temperature=temperature, timeout=120.0,
    )


def validate_scoring(prompt: dict, response: str) -> None:
    value = _json_from_text(response)
    if prompt["stage"] == "rubric":
        rubrics = _coerce_rubrics(value)
        if len(rubrics) > prompt["max_rubrics"]:
            raise ValueError("生成细则超过 max_rubrics")
    else:
        _coerce_judgements(value, _coerce_rubrics(prompt["rubrics"]))


class AuditedCalls:
    """仅复用通过结束原因、内容及领域校验的完全相同请求；失败原文保留。"""

    def __init__(self, directory: Path, backend: OpenAICompatibleBackend, max_tokens: int):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.max_tokens = max_tokens
        self.records: list[dict] = []

    def call(self, key: str, messages: list[dict],
             validate: Callable[[str], None] | None = None) -> str:
        request = {
            "model": self.backend.model_name, "messages": messages,
            "max_tokens": self.max_tokens, "temperature": self.backend.temperature,
        }
        directory = self.directory / key
        directory.mkdir(exist_ok=True)
        success = directory / "success.json"
        if success.exists():
            saved = strict_json(success.read_text())
            if saved["request"] != request:
                raise ValueError(f"缓存请求已改变，请选择新的运行目录：{directory}")
            if validate:
                validate(saved["response"]["text"])
            self.records.append(saved)
            return saved["response"]["text"]
        attempt = len(list(directory.glob("attempt-*.request.json"))) + 1
        prefix = directory / f"attempt-{attempt:03d}"
        write_json(Path(str(prefix) + ".request.json"), request)
        print(f"[API] {key} / {self.backend.model_name}", flush=True)
        try:
            response = self.backend.generate_chat(messages, max_new_tokens=self.max_tokens)
        except Exception as exc:
            # 不落供应商异常正文，避免其中回显认证头或带凭据的 URL。
            write_json(Path(str(prefix) + ".error.json"), {"error_type": type(exc).__name__})
            raise RuntimeError(f"API 调用失败（{type(exc).__name__}），日志：{directory}") from None
        record = {"request": request, "response": asdict(response)}
        write_json(Path(str(prefix) + ".response.json"), record["response"])
        if response.finish_reason != "stop" or not response.text.strip():
            raise ValueError(f"模型未完整完成：{response.finish_reason!r}，日志：{directory}")
        if validate:
            validate(response.text)
        write_json(success, record)
        self.records.append(record)
        return response.text

    def scoring(self, prompt: str) -> str:
        request = strict_json(prompt)
        key = f'{request["stage"]}-{request["context"]["message_id"]}'
        return self.call(key, [
            {"role": "system", "content": SCORING_SYSTEM},
            {"role": "user", "content": prompt},
        ], lambda text: validate_scoring(request, text))


def totals(records: list[dict]) -> dict:
    return {
        "model_calls": len(records),
        "prompt_tokens": sum(r["response"]["n_prompt_pos"] for r in records),
        "completion_tokens": sum(r["response"]["n_gen_tokens"] for r in records),
        "latency_s": sum(r["response"]["latency_s"] for r in records),
    }


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)

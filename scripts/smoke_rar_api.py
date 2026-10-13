"""真实 API 小样本检查：保留原始回复，截断或非法 JSON 显式报错。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lychee_mas.backends.openai_api_backend import OpenAICompatibleBackend  # noqa: E402
from lychee_mas.methods.postrun.rar_export import export_step_rewards, write_json  # noqa: E402
from lychee_mas.methods.postrun.rar_trace_adapter import (  # noqa: E402
    load_trajectory,
    prepare_steps,
)
from lychee_mas.methods.postrun.rubric_step_reward import _json_from_text  # noqa: E402
from lychee_mas.plugins.postrun import analyze_run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path, default=ROOT / "examples/data/rar_trajectory.json")
    args = parser.parse_args()
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens 必须大于0")
    trajectory = load_trajectory(args.input)
    prepare_steps(trajectory)
    backend = OpenAICompatibleBackend(
        model=os.environ["RAR_API_MODEL"], base_url=os.environ["RAR_API_BASE_URL"],
        api_key_env="RAR_API_KEY", timeout=60.0,
    )
    run_dir = args.output or ROOT / "runs/rar" / datetime.now().strftime("api-%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True, exist_ok=False)
    print(f"原始回复和诊断目录：{run_dir}", flush=True)
    config = {
        "method": "rubric_step_reward", "credit_assigner": "step_reward",
        "evaluation_mode": "model", "history_mode": "explicit",
        "max_rubrics": 4, "aggregation": "mean",
    }
    git_cmd = ["git", "-c", f"safe.directory={ROOT.as_posix()}", "--no-optional-locks"]
    revision = subprocess.check_output(
        [*git_cmd, "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = {
        "mode": "model", "model": backend.model_name, "git_sha": revision,
        "git_dirty": bool(subprocess.check_output(
            [*git_cmd, "status", "--porcelain"], cwd=ROOT)),
        "max_output_tokens": args.max_output_tokens, "config": config,
        "input": str(args.input.resolve()),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "trajectory_assumptions": {k: v for k, v in trajectory.meta.items()
                                   if k.endswith("_assumption")},
        "source_dataset": trajectory.meta.get("source_dataset", "unspecified"),
    }
    write_json(run_dir / "config.json", metadata)
    calls: list[dict[str, Any]] = []

    def call_model(prompt: str) -> str:
        request = json.loads(prompt)
        stage = request["stage"]
        number = len(calls) + 1
        prefix = run_dir / f"call-{number:03d}-{stage}"
        write_json(prefix.with_suffix(".request.json"), request)
        print(f"正在调用模型：{stage}，第 {number} 次", flush=True)
        result = backend.generate_chat(
            messages=[
                {"role": "system", "content": (
                    "严格遵循评分任务，只输出一个完整合法的JSON对象。"
                    "不输出Markdown、前后说明或思考过程。"
                    "description、rationale、reason保持简短，分别尽量不超过80字。")},
                {"role": "user", "content": prompt},
            ],
            max_new_tokens=args.max_output_tokens,
        )
        raw_path = prefix.with_suffix(".response.txt")
        raw_path.write_text(result.text, encoding="utf-8")
        record = {
            "stage": stage, "message_id": request["context"]["message_id"],
            "request": request, "response_text": result.text,
            "finish_reason": result.finish_reason,
            "prompt_tokens": result.n_prompt_pos, "completion_tokens": result.n_gen_tokens,
            "latency_s": result.latency_s,
        }
        calls.append(record)
        write_json(prefix.with_suffix(".metadata.json"), {
            key: value for key, value in record.items() if key not in {"request", "response_text"}
        })
        print(
            f"finish_reason={result.finish_reason!r}，输出token={result.n_gen_tokens}",
            flush=True,
        )
        if result.finish_reason == "length":
            raise ValueError(
                f"模型达到输出长度上限，不能评分。原始回复：{raw_path}；"
                "可在服务商允许范围内增大 --max-output-tokens，或使用较短输出模型。")
        if result.finish_reason not in (None, "stop"):
            raise ValueError(f"模型非正常结束：{result.finish_reason!r}；原始回复：{raw_path}")
        try:
            _json_from_text(result.text)
        except ValueError as exc:
            raise ValueError(
                f"第{number}次 {stage} 返回的JSON无效：{exc}；原始回复：{raw_path}。"
                "未尝试补括号、伪造内容或自动重试。") from exc
        return result.text

    credits = analyze_run(trajectory, rubric_llm=call_model, judge_llm=call_model, **config)
    metadata.update(
        successful_responses=len(calls),
        prompt_tokens=sum(c["prompt_tokens"] for c in calls),
        completion_tokens=sum(c["completion_tokens"] for c in calls),
        latency_s=sum(c["latency_s"] for c in calls),
    )
    export_step_rewards(trajectory, run_dir / "results", metadata, calls)
    print("智能体汇总奖励：", credits)
    print(f"评分结果已保存：{run_dir / 'results'}")


if __name__ == "__main__":
    main()

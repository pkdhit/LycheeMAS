"""RaR 步骤奖励离线实验入口（回放后端，不进行网络或模型调用）。"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lychee_mas import REGISTRY  # noqa: E402
from lychee_mas.methods.postrun.rar_export import export_step_rewards  # noqa: E402
from lychee_mas.methods.postrun.rar_replay import ReplayCallbacks  # noqa: E402
from lychee_mas.methods.postrun.rar_trace_adapter import load_trajectory, strict_json  # noqa: E402
from lychee_mas.plugins.postrun import analyze_run  # noqa: E402


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "--no-optional-locks", *args],
        cwd=ROOT, text=True, encoding="utf-8").strip()


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        config = strict_json(text)
    else:
        import yaml  # 惰性、可选；不传 --config 时零第三方依赖。
        config = yaml.safe_load(text)
    if not isinstance(config, dict):
        raise ValueError("配置必须为对象")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="标准化轨迹 JSON")
    parser.add_argument("--replay", type=Path, required=True, help="显式离线回复 JSON")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的实验目录")
    parser.add_argument("--config", type=Path, help="可选 JSON 或 YAML 配置")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请选新目录，避免覆盖结果或人工标注")
    random.seed(args.seed)
    config = load_config(args.config)
    method = config.pop("method", "rubric_step_reward")
    credit_assigner = config.pop("credit_assigner", "step_reward")
    for key in ("rubric_llm", "judge_llm", "evaluation_mode", "default_rubrics"):
        if key in config:
            parser.error(f"离线回放入口不允许配置 {key}")
    parameters = inspect.signature(REGISTRY.get("attributor", method)).parameters
    defaults = {name: parameter.default for name, parameter in parameters.items()
                if parameter.default is not inspect.Parameter.empty
                and name not in {"rubric_llm", "judge_llm", "default_rubrics"}}
    config = {**defaults, **config, "evaluation_mode": "offline_replay"}
    trajectory = load_trajectory(args.input)
    replay = ReplayCallbacks(args.replay)
    # 在评分前检查 git 元数据，避免评分完才发现缺少可复现信息。
    revision = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    started = time.perf_counter()
    credits = analyze_run(trajectory, method=method, credit_assigner=credit_assigner,
                          rubric_llm=replay.rubric, judge_llm=replay.judge, **config)
    replay.require_complete()
    latency = time.perf_counter() - started
    sources = [Path(__file__), *sorted((ROOT / "src/lychee_mas/methods/postrun").glob("*.py"))]
    metadata = {
        "mode": "offline_replay", "model": None, "seed": args.seed,
        "git_sha": revision, "git_dirty": dirty,
        "source_sha256": {str(p.relative_to(ROOT)): _digest(p) for p in sources},
        "input_sha256": _digest(args.input), "replay_sha256": _digest(args.replay),
        "config": {"method": method, "credit_assigner": credit_assigner, **config},
        "config_file_sha256": _digest(args.config) if args.config else None,
        "callback_calls": len(replay.calls), "model_calls": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "latency_s": latency,
        "accuracy": None, "accuracy_status": "not_evaluated_no_real_model_or_human_labels",
        "note": "预设回放只验证输入输出、流程和计算；不能证明真实 LLM 的评分合理性。",
        "credits": credits,
    }
    directory = export_step_rewards(trajectory, args.output, metadata, replay.calls)
    print(json.dumps({"mode": metadata["mode"], "output": str(directory),
                      "steps": len(trajectory.meta[method]["steps"]),
                      "credits": credits, "model_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

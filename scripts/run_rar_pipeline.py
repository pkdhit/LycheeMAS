"""复用静态 MAS 队伍：真实 API 生成轨迹、RaR 评分、导出独立 LLM 盲标包。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from lychee_mas.core.types import Message, Trajectory  # noqa: E402
from lychee_mas.eval.benchmarks.math500 import load_math500  # noqa: E402
from lychee_mas.methods.build.templates import team_to_agentspecs  # noqa: E402
from lychee_mas.methods.memory.channels.nl import PREV_OUTPUT_HEADER  # noqa: E402
from lychee_mas.methods.postrun.rar_export import (  # noqa: E402
    _write_csv,
    export_step_rewards,
    write_json,
)
from lychee_mas.methods.postrun.rar_trace_adapter import (  # noqa: E402
    load_trajectory,
    prepare_steps,
    strict_json,
)
from lychee_mas.methods.postrun.rubric_step_reward import (  # noqa: E402
    _coerce_rubrics,
    _judge_prompt,
)
from lychee_mas.plugins import build_langgraph  # noqa: E402
from lychee_mas.plugins.postrun import analyze_run  # noqa: E402
from rar_api_utils import (  # noqa: E402
    AuditedCalls,
    backend_for,
    digest,
    json_text,
    model_for,
    totals,
)


class RunState(TypedDict):
    question: str
    messages: list[Message]


def generate(task: dict, config: dict, calls: AuditedCalls) -> Trajectory:
    specs = team_to_agentspecs(config["team"])
    if any(s.tools or s.meta.get("agent_type") != "assistant" for s in specs):
        raise ValueError("此入口仅支持已有纯文本 assistant 队伍，不能伪造工具执行")

    def make_node(spec: Any, is_terminal: bool):
        def node(state: RunState) -> dict:
            previous = [m for m in state["messages"] if m.sender in spec.meta["predecessors"]]
            user = state["question"]
            for message in previous:
                user += f"\n\n{PREV_OUTPUT_HEADER}\n{message.content}"
            text = calls.call(f"agent-{spec.name}", [
                {"role": "system", "content": spec.system_prompt},
                {"role": "user", "content": user},
            ])
            message = Message(
                id=f"m-{spec.name}", sender=spec.name, role="assistant", content=text,
                meta={"action_type": "utterance", "agent_role": spec.system_prompt,
                      "visible_message_ids": ["u0", *[m.id for m in previous]]},
            )
            return {"messages": [*state["messages"], message]}
        return node

    graph = build_langgraph(method="static", node_factory=make_node,
                            state_schema=RunState, agents=specs)
    result = graph.compile().invoke({
        "question": task["question"],
        "messages": [Message(id="u0", sender="user", role="user", content=task["question"])],
    })
    trajectory = Trajectory(
        id=task["trajectory_id"], task_id=task["task_id"], messages=result["messages"],
        meta={"task_goal": task["question"],
              "agent_roles": {s.name: s.system_prompt for s in specs},
              "source_dataset": "math500", "source_index": task["source_index"],
              "generation": "live_openai_compatible_api", "team": config["team"],
              "communication": "task plus immediate predecessor output, verbatim",
              "topology": "static single pass; no retry edge"},
    )
    prepare_steps(trajectory)
    return trajectory


def score(trajectory: Trajectory, config: dict, rubric: AuditedCalls, judge: AuditedCalls) -> None:
    analyze_run(
        trajectory, method="rubric_step_reward", credit_assigner="step_reward",
        rubric_llm=rubric.scoring, judge_llm=judge.scoring,
        max_rubrics=config["max_rubrics"], history_mode="explicit", aggregation="mean",
    )


def blind_requests(trajectory: Trajectory) -> list[dict]:
    result = trajectory.meta["rubric_step_reward"]
    steps = {s.message.id: s for s in prepare_steps(trajectory)}
    return [{
        "trajectory_id": trajectory.id, "task_id": trajectory.task_id,
        "message_id": record["message_id"],
        "request": strict_json(_judge_prompt(
            steps[record["message_id"]], result["task_goal"],
            _coerce_rubrics(record["rubrics"]))),
    } for record in result["steps"]]


def process_task(task: dict, config: dict, models: dict, run: Path, phase: str) -> dict:
    case = run / "cases" / task["task_id"]
    case.mkdir(parents=True, exist_ok=True)
    trace_path = case / "trajectory.json"
    if not trace_path.exists():
        if phase == "score":
            raise ValueError(f"缺少轨迹，先运行 generate：{trace_path}")
        calls = AuditedCalls(case / "calls", backend_for(
            models["agent"], config["agent_temperature"]), config["max_output_tokens"])
        trajectory = generate(task, config, calls)
        write_json(trace_path, {
            "id": trajectory.id, "task_id": trajectory.task_id, "meta": trajectory.meta,
            "messages": [asdict(m) for m in trajectory.messages],
        })
    trajectory = load_trajectory(trace_path)
    if phase == "generate":
        return {"task_id": task["task_id"], "steps": len(prepare_steps(trajectory))}
    results = case / "scoring" / "results.json"
    if not results.exists():
        rubric = AuditedCalls(case / "calls", backend_for(
            models["rubric"], config["scoring_temperature"]), config["max_output_tokens"])
        judge = AuditedCalls(case / "calls", backend_for(
            models["judge"], config["scoring_temperature"]), config["max_output_tokens"])
        score(trajectory, config, rubric, judge)
        metadata = {"mode": "model", "label_source": "llm_initial",
                    "models": models, "trajectory_sha256": digest(trace_path),
                    "config": config, **totals(rubric.records + judge.records)}
        with tempfile.TemporaryDirectory(prefix=".scoring-", dir=case) as staging:
            staged = Path(staging) / "results"
            export_step_rewards(trajectory, staged, metadata, rubric.records + judge.records)
            staged.rename(case / "scoring")
    else:
        saved = strict_json(results.read_text())
        if saved["metadata"]["trajectory_sha256"] != digest(trace_path):
            raise ValueError(f"轨迹在评分后被修改：{trace_path}")
        trajectory.meta["rubric_step_reward"] = saved["result"]
    print(f"[完成] {task['task_id']}：{len(prepare_steps(trajectory))} 步", flush=True)
    return {"task_id": task["task_id"], "trajectory": trajectory}


def export_batch(run: Path, completed: list[dict]) -> None:
    # 可恢复：每个批量文件独立写入；已完成文件必须内容相同，不覆盖任何标签。
    requests, criteria, steps, records = [], [], [], []
    for item in completed:
        trajectory = item["trajectory"]
        requests.extend(blind_requests(trajectory))
        result = trajectory.meta["rubric_step_reward"]
        criteria.extend(result["criteria_rows"])
        steps.extend({"trajectory_id": trajectory.id, "task_id": trajectory.task_id, **s}
                     for s in result["steps"])
    for path in sorted((run / "cases").glob("*/calls/*/success.json")):
        records.append(strict_json(path.read_text()))
    summary = {
        "trajectories": len(completed), "steps": len(steps), "criteria": len(criteria),
        "initial_label_source": "llm", "independent_review_status": "pending",
        "human_annotations": 0, "accuracy": None,
        "note": "局部细则满足度；未衡量答案准确率、人工一致率或强模型一致率。",
        "mean_step_reward": sum(s["reward"] for s in steps) / len(steps),
        **totals(records),
    }
    review = run / "review_blind"
    review.mkdir(exist_ok=True)
    documents = {
        run / "summary.json": summary,
        run / "steps.json": steps,
        review / "requests.json": requests,
        review / "labels.template.json": [{
            "trajectory_id": r["trajectory_id"], "message_id": r["message_id"],
            "annotator": "", "model": "", "label_source": "llm",
            "judgements": [{"criterion_id": c["id"], "satisfied": None, "reason": ""}
                           for c in r["request"]["rubrics"]],
        } for r in requests],
    }
    for path, value in documents.items():
        if path.exists():
            if strict_json(path.read_text()) != value:
                raise ValueError(f"已有导出与本次结果不同：{path}")
        else:
            write_json(path, value)
    if not (run / "criteria.csv").exists():
        _write_csv(run / "criteria.csv", criteria, list(criteria[0]))
    print(json_text(summary), flush=True)


def initialize(run: Path, config: dict, models: dict, resume: bool) -> list[dict]:
    sources = [Path(__file__), ROOT / "scripts/rar_api_utils.py",
               ROOT / "src/lychee_mas/methods/build/templates.py",
               *sorted((ROOT / "src/lychee_mas/methods/postrun").glob("*.py"))]
    signature = {"config": config, "models": models,
                 "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sources}}
    if run.exists():
        if not resume:
            raise ValueError("目录已存在。使用 --resume 续跑，或选择新目录")
        manifest = strict_json((run / "manifest.json").read_text())
        if manifest["signature"] != signature:
            raise ValueError("配置、模型或源码已变化，请选择新目录，避免混合实验")
        if digest(run / "tasks.json") != manifest["tasks_sha256"]:
            raise ValueError("任务文件在初始化后被修改")
        return strict_json((run / "tasks.json").read_text())
    records = load_math500()
    if config["n"] > len(records):
        raise ValueError("n 超过数据集大小")
    indexes = random.Random(config["seed"]).sample(range(len(records)), config["n"])
    tasks = [{"task_id": f"math500-{i:04d}",
              "trajectory_id": f"math500-{i:04d}-{config['team']}-s{config['seed']}",
              "source_index": i, "question": records[i]["question"]} for i in indexes]
    run.mkdir(parents=True)
    write_json(run / "tasks.json", tasks)
    # 标准答案单独保存；生成、rubric、judge 和盲标包均不读取。
    write_json(run / "references.json", [{"task_id": t["task_id"],
               "gold": records[t["source_index"]]["gold"]} for t in tasks])
    write_json(run / "manifest.json", {
        "signature": signature, "tasks_sha256": digest(run / "tasks.json"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                           text=True).strip(),
        "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "python": sys.version,
        "packages": {p: importlib.metadata.version(p) for p in ("openai", "langgraph")},
        "dataset_sha256": hashlib.sha256(json_text(records).encode()).hexdigest(),
        "dataset_source": "lychee_mas.eval.benchmarks.math500 (existing project loader)",
        "seed_scope": "task sampling only; provider generation is not bitwise reproducible",
    })
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/postrun/rar_pipeline.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("all", "generate", "score"), default="all")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--n", type=int)
    args = parser.parse_args()
    config = strict_json(args.config.read_text())
    if args.n is not None:
        config["n"] = args.n
    for field in ("n", "max_output_tokens", "max_rubrics", "workers"):
        if type(config[field]) is not int or config[field] <= 0:
            parser.error(f"{field} 必须为正整数")
    if config["benchmark"] != "math500":
        parser.error("此入口目前使用项目现有 MATH-500 loader")
    models = {stage: model_for(stage) for stage in ("agent", "rubric", "judge")}
    # 付费调用前确认全部模型和环境配置齐全。
    for stage in models:
        backend_for(models[stage], config["agent_temperature"] if stage == "agent"
                    else config["scoring_temperature"])
    tasks = initialize(args.output, config, models, args.resume)
    with ThreadPoolExecutor(max_workers=config["workers"]) as pool:
        completed = list(pool.map(
            lambda task: process_task(task, config, models, args.output, args.phase), tasks))
    if args.phase != "generate":
        export_batch(args.output, completed)
    else:
        print(f"已生成 {len(completed)} 条轨迹；运行 --phase score --resume 继续评分")


if __name__ == "__main__":
    main()

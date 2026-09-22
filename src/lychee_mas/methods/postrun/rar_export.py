"""步骤结果导出和人工抽查；只使用标准库，不把回放标签当成人工标注。"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ...core.types import Trajectory
from .rar_trace_adapter import prepare_steps

KEY_FIELDS = ("trajectory_id", "message_id", "criterion_id")
ANNOTATION_FIELDS = [
    "trajectory_id", "message_id", "criterion_id", "task_id", "step", "agent", "role",
    "action_type", "task_goal", "visible_history", "output", "评分细则", "weight",
    "annotator", "human_satisfied", "human_reason", "rubric_relevant", "rubric_clear",
]


def write_json(path: str | Path, value: Any) -> None:
    # x 模式避免覆盖已有实验/人工标注。
    with Path(path).open("x", encoding="utf-8", newline="") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _csv_value(value: Any) -> Any:
    # CSV 只是展示文件；原始字符串完整保存在 JSON。避免表格软件执行公式。
    if isinstance(value, str) and (value.startswith("\'")
                                   or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: _csv_value(value) for key, value in row.items()} for row in rows)


def export_step_rewards(trajectory: Trajectory, output_dir: str | Path,
                        metadata: dict[str, Any],
                        model_calls: list[dict[str, Any]]) -> Path:
    result = trajectory.meta["rubric_step_reward"]
    rows = result["criteria_rows"]
    if not rows:
        raise ValueError("没有结果行可导出")
    # 原始可见历史用于盲标；不显示模型判断、模型理由或奖励。
    contexts = {step.message.id: step for step in prepare_steps(
        trajectory, history_mode=result["history_mode"])}
    annotations = []
    for row in rows:
        step = contexts[row["message_id"]]
        entry = {key: row[key] for key in (
            *KEY_FIELDS, "task_id", "step", "agent", "role", "action_type", "评分细则", "weight")}
        entry.update(task_goal=result["task_goal"], output=step.output,
                     visible_history=json.dumps(step.history, ensure_ascii=False),
                     annotator="", human_satisfied="", human_reason="",
                     rubric_relevant="", rubric_clear="")
        annotations.append(entry)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "results.json", {"metadata": metadata, "result": result})
    write_json(directory / "model_calls.json", model_calls)
    _write_csv(directory / "criteria.csv", rows, list(rows[0]))
    _write_csv(directory / "human_annotations.csv", annotations, ANNOTATION_FIELDS)
    return directory


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    values = tuple(row.get(key) for key in KEY_FIELDS)
    if any(not isinstance(v, str) or not v for v in values):
        raise ValueError("标注缺少 trajectory_id/message_id/criterion_id")
    return values


def _boolean(value: str, field: str) -> bool:
    if value not in {"true", "false"}:
        raise ValueError(f"{field} 必须为小写 true 或 false")
    return value == "true"


def evaluate_annotations(result: dict[str, Any], annotation_path: str | Path) -> dict[str, Any]:
    rows = result["criteria_rows"]
    expected = {_key(row): row for row in rows}
    if not rows or len(expected) != len(rows):
        raise ValueError("结果表为空或含重复细则键")
    # ID 同样经过 CSV 展示转义，用已知结果建立可逆映射，不猜测或删改原始 ID。
    csv_keys = {tuple(_csv_value(value) for value in key): key for key in expected}
    if len(csv_keys) != len(expected):
        raise ValueError("CSV 中的细则标识发生冲突")
    seen = set()
    labelled: dict[tuple[str, str, str], bool] = {}
    quality: dict[str, list[bool]] = defaultdict(list)
    disagreements = []
    with Path(annotation_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {*KEY_FIELDS, "annotator", "human_satisfied", "human_reason"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("人工标注 CSV 缺少必要列")
        for row in reader:
            key = csv_keys.get(_key(row))
            if key is None or key in seen:
                raise ValueError(f"重复或未知标注键: {key}")
            seen.add(key)
            value = row["human_satisfied"]
            if value == "":
                if any(row.get(field, "") for field in (
                        "annotator", "human_reason", "rubric_relevant", "rubric_clear")):
                    raise ValueError("部分填写的标注缺少 human_satisfied")
                continue
            if not row["annotator"].strip() or not row["human_reason"].strip():
                raise ValueError("已标注行必须有 annotator 和 human_reason")
            labelled[key] = _boolean(value, "human_satisfied")
            for field in ("rubric_relevant", "rubric_clear"):
                if row.get(field):
                    quality[field].append(_boolean(row[field], field))
            if labelled[key] != expected[key]["satisfied"]:
                disagreements.append({"key": list(key), "human_satisfied": labelled[key],
                                      "model_satisfied": expected[key]["satisfied"],
                                      "human_reason": row["human_reason"],
                                      "model_reason": expected[key]["评分依据"]})
    if not labelled:
        raise ValueError("尚无人工标注；不能生成一致率或把空白当作不满足")
    by_step: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for key in expected:
        by_step[key[:2]].append(key)
    comparisons = []
    for step_key, keys in by_step.items():
        if all(key in labelled for key in keys):
            total = sum(expected[key]["weight"] for key in keys)
            human_reward = sum(expected[key]["weight"] for key in keys if labelled[key]) / total
            model_reward = expected[keys[0]]["step_reward"]
            comparisons.append({"trajectory_id": step_key[0], "message_id": step_key[1],
                                "human_reward": human_reward, "model_reward": model_reward,
                                "absolute_error": abs(human_reward - model_reward)})
    return {
        "evaluation_mode": result["evaluation_mode"],
        "note": "回放一致率只验证数据处理；真实评分合理性仍需真实模型及独立人工标注。",
        "total_criteria": len(expected), "labelled_criteria": len(labelled),
        "coverage": len(labelled) / len(expected),
        "criterion_agreement": 1 - len(disagreements) / len(labelled),
        "fully_labelled_steps": len(comparisons),
        "step_reward_mae": (sum(row["absolute_error"] for row in comparisons) / len(comparisons)
                            if comparisons else None),
        "rubric_quality": {field: {"count": len(values), "positive_rate": sum(values) / len(values)}
                           for field, values in quality.items()},
        "disagreements": disagreements, "step_comparisons": comparisons,
    }

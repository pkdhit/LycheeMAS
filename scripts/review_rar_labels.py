"""独立 LLM 盲标与一致性比较；不把模型标签称作人工真值。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from lychee_mas.methods.postrun.rar_export import write_json  # noqa: E402
from lychee_mas.methods.postrun.rar_trace_adapter import require_text, strict_json  # noqa: E402
from lychee_mas.methods.postrun.rubric_step_reward import (  # noqa: E402
    _coerce_judgements,
    _coerce_rubrics,
    _json_from_text,
    _normalize_reward,
)
from rar_api_utils import AuditedCalls, backend_for, digest, json_text, totals  # noqa: E402


def step_key(row: dict) -> tuple[str, str]:
    return (require_text(row.get("trajectory_id"), "trajectory_id"),
            require_text(row.get("message_id"), "message_id"))


def compare(requests: list[dict], labels: list[dict], baseline: list[dict]) -> dict:
    expected = {step_key(r): r for r in requests}
    originals = {step_key(r): r for r in baseline}
    if not expected or len(expected) != len(requests) or set(expected) != set(originals):
        raise ValueError("盲标请求与原始步骤不匹配或重复")
    if len(originals) != len(baseline):
        raise ValueError("原始步骤存在重复键")
    seen, comparisons, disagreements, pairs, annotators = set(), [], [], [], set()
    for label in labels:
        key = step_key(label)
        if key in seen or key not in expected:
            raise ValueError(f"重复或未知步骤：{key}")
        seen.add(key)
        if label.get("label_source") != "llm":
            raise ValueError("此入口仅接受明确标记 label_source=llm 的独立模型标注")
        annotator = require_text(label.get("annotator"), "annotator")
        model = require_text(label.get("model"), "model")
        annotators.add((annotator, model))
        rubrics = _coerce_rubrics(expected[key]["request"]["rubrics"])
        if originals[key]["rubrics"] != expected[key]["request"]["rubrics"]:
            raise ValueError("复核必须冻结原 rubrics；不能在比较时替换细则")
        if originals[key]["output"] != expected[key]["request"]["output"]:
            raise ValueError("复核的步骤输出已改变")
        judged = _coerce_judgements(label, rubrics)
        original = _coerce_judgements(originals[key], rubrics)
        reference_reward = _normalize_reward(rubrics, judged)
        original_reward = _normalize_reward(rubrics, original)
        comparisons.append({
            "trajectory_id": key[0], "message_id": key[1],
            "initial_reward": original_reward, "review_reward": reference_reward,
            "absolute_error": abs(original_reward - reference_reward),
        })
        for a, b in zip(original, judged):
            pairs.append((a.satisfied, b.satisfied))
            if a.satisfied != b.satisfied:
                disagreements.append({
                    "trajectory_id": key[0], "message_id": key[1],
                    "criterion_id": a.criterion_id,
                    "initial_satisfied": a.satisfied, "review_satisfied": b.satisfied,
                    "initial_reason": a.reason, "review_reason": b.reason,
                })
    if not pairs:
        raise ValueError("没有完整的独立模型标注")
    agreement = sum(a == b for a, b in pairs) / len(pairs)
    pa = sum(a for a, _ in pairs) / len(pairs)
    pb = sum(b for _, b in pairs) / len(pairs)
    chance = pa * pb + (1 - pa) * (1 - pb)
    return {
        "label_source": "llm", "annotators": sorted(annotators),
        "note": "模型间一致性，不是人工一致率或答案准确率；高一致性不保证两者正确。",
        "total_steps": len(expected), "reviewed_steps": len(seen),
        "step_coverage": len(seen) / len(expected), "reviewed_criteria": len(pairs),
        "criterion_agreement": agreement,
        "cohen_kappa": (agreement - chance) / (1 - chance) if chance < 1 else None,
        "step_reward_mae": sum(c["absolute_error"] for c in comparisons) / len(comparisons),
        "disagreements": disagreements, "step_comparisons": comparisons,
    }


def annotate(request_path: Path, output: Path, resume: bool, max_tokens: int) -> None:
    # 复核模型必须单独指定，不能不知情地回落到初评模型。
    model = require_text(os.environ.get("RAR_REVIEW_MODEL"), "RAR_REVIEW_MODEL 环境变量")
    backend = backend_for(model, 0.0)
    manifest = {"requests_sha256": digest(request_path), "model": model,
                "label_source": "llm", "max_output_tokens": max_tokens}
    if output.exists():
        if not resume or strict_json((output / "manifest.json").read_text()) != manifest:
            raise ValueError("复核目录存在或配置变化；选择新目录，或用 --resume 续跑原任务")
        if (output / "labels.json").exists():
            print(f"已完成：{output / 'labels.json'}")
            return
    else:
        output.mkdir(parents=True)
        write_json(output / "manifest.json", manifest)
    calls = AuditedCalls(output / "calls", backend, max_tokens)
    labels = []
    for index, item in enumerate(strict_json(request_path.read_text())):
        # 每步独立上下文；日志也不读取 baseline。
        prompt = item["request"]
        from rar_api_utils import SCORING_SYSTEM, validate_scoring
        response = calls.call(f"review-{index:05d}", [
            {"role": "system", "content": SCORING_SYSTEM},
            {"role": "user", "content": json_text(prompt)},
        ], lambda text: validate_scoring(prompt, text))
        labels.append({
            "trajectory_id": item["trajectory_id"], "message_id": item["message_id"],
            "label_source": "llm", "annotator": "independent_api_judge", "model": model,
            "judgements": _json_from_text(response)["judgements"],
        })
    write_json(output / "labels.json", labels)
    write_json(output / "usage.json", totals(calls.records))
    print(f"独立模型标签已保存：{output / 'labels.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("annotate", "compare"))
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--baseline", type=Path, help="原批次 steps.json；仅比较时读取")
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens 必须为正整数")
    if args.action == "annotate":
        annotate(args.requests, args.output, args.resume, args.max_output_tokens)
    else:
        if not args.labels or not args.baseline:
            parser.error("compare 需要 --labels 和 --baseline")
        report = compare(strict_json(args.requests.read_text()),
                         strict_json(args.labels.read_text()),
                         strict_json(args.baseline.read_text()))
        report.update(requests_sha256=digest(args.requests), labels_sha256=digest(args.labels),
                      baseline_sha256=digest(args.baseline))
        write_json(args.output, report)
        print(json_text({k: v for k, v in report.items()
                         if k not in {"disagreements", "step_comparisons"}}))


if __name__ == "__main__":
    main()

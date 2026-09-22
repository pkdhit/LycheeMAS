"""人工抽查结果分析入口；需真实填写的标注 CSV，不生成模拟人工标签。"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lychee_mas.methods.postrun.rar_export import (  # noqa: E402
    evaluate_annotations,
    write_json,
)
from lychee_mas.methods.postrun.rar_trace_adapter import strict_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = strict_json(args.results.read_text(encoding="utf-8-sig"))
    summary = evaluate_annotations(report["result"], args.annotations)
    summary["source_results_sha256"] = hashlib.sha256(args.results.read_bytes()).hexdigest()
    summary["annotations_sha256"] = hashlib.sha256(args.annotations.read_bytes()).hexdigest()
    summary["source_metadata"] = report["metadata"]
    write_json(args.output, summary)
    print(f"人工标注对照结果已写入 {args.output}")


if __name__ == "__main__":
    main()

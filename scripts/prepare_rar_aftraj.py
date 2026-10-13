"""Prepare local AFTraj math trajectories for RaR; no download or model calls."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lychee_mas.methods.postrun.rar_aftraj_adapter import convert_aftraj  # noqa: E402
from lychee_mas.methods.postrun.rar_export import write_json  # noqa: E402
from lychee_mas.methods.postrun.rar_trace_adapter import strict_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path,
                        default=ROOT / "data/benchmarks/prepared/aftraj")
    parser.add_argument("--roles", type=Path, default=ROOT / "configs/postrun/rar_aftraj.json")
    parser.add_argument("--history-policy", required=True, choices=["shared-prefix"])
    parser.add_argument("--split", choices=["safe", "unsafe", "both"], default="both")
    parser.add_argument("--conv-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.output.exists():
        parser.error("--output must not already exist")
    import pyarrow.parquet as pq

    config = strict_json(args.roles.read_text(encoding="utf-8-sig"))
    sources = {}
    selected = []
    wanted = set(args.conv_id)
    splits = ["safe", "unsafe"] if args.split == "both" else [args.split]
    groups = []
    for split in splits:
        path = args.source_dir / f"aftraj_{split}.parquet"
        sources[split] = {"path": str(path.resolve()),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        rows = [row for row in pq.read_table(path).to_pylist()
                if row.get("domain", "").lower() == "math"
                and (not wanted or row["conv_id"] in wanted)]
        groups.append([(split, row) for row in rows])
    # Alternate splits deterministically, preserving source row order.
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group):
                selected.append(group[index])
    if wanted:
        found = [row["conv_id"] for _, row in selected]
        if set(found) != wanted or len(found) != len(wanted):
            parser.error("Requested conv-id missing, duplicated, or outside selected math split")
    else:
        selected = selected[:args.limit]
    if not selected:
        parser.error("No math records selected")
    prepared = []
    for split, row in selected:
        try:
            trajectory, annotations = convert_aftraj(
                row, agent_roles=config["agent_roles"], history_policy=args.history_policy)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Cannot convert {row['conv_id']}: {exc}") from exc
        annotations["source_split"] = split
        prepared.append((trajectory, annotations))
    args.output.mkdir(parents=True, exist_ok=False)
    entries = []
    for trajectory, annotations in prepared:
        folder = args.output / trajectory["id"]
        folder.mkdir()
        write_json(folder / "trajectory.json", trajectory)
        write_json(folder / "source_annotations.json", annotations)
        count = sum(item["step"] is not None for item in annotations["mapping"])
        entries.append({"id": trajectory["id"], "conv_id": annotations["conv_id"],
                        "trajectory": f"{trajectory['id']}/trajectory.json",
                        "steps": count, "expected_model_calls": 2 * count})
        print(f"{annotations['conv_id']}: {count} steps, {2 * count} model calls")
        print(folder / "trajectory.json")
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    write_json(args.output / "manifest.json", {
        "adapter_version": 1, "sources": sources, "config": config,
        "history_policy": args.history_policy,
        "selection": {"domain": "math", "split": args.split, "conv_ids": args.conv_id,
                      "limit": args.limit, "order": "source order, alternating splits"},
        "git_sha": subprocess.check_output([*git, "rev-parse", "HEAD"], cwd=ROOT,
                                           text=True).strip(),
        "git_dirty": bool(subprocess.check_output([*git, "status", "--porcelain"], cwd=ROOT)),
        "dataset": "https://huggingface.co/datasets/ZBox008003/AFTraj",
        "license": "CC BY 4.0", "entries": entries,
    })


if __name__ == "__main__":
    main()

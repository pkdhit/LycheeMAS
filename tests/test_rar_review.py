"""独立模型标签必须完整、可追溯，比较固定细则的二元判断。"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_rar_labels import compare  # noqa: E402


def fixture():
    rubrics = [
        {"id": "a", "description": "A", "weight": 3, "rationale": "A"},
        {"id": "b", "description": "B", "weight": 1, "rationale": "B"},
    ]
    request = {"trajectory_id": "t", "message_id": "m",
               "request": {"rubrics": rubrics, "output": "answer"}}
    baseline = {"trajectory_id": "t", "message_id": "m", "rubrics": rubrics,
                "output": "answer", "judgements": [
                    {"criterion_id": "a", "satisfied": True, "reason": "yes"},
                    {"criterion_id": "b", "satisfied": False, "reason": "no"}]}
    label = {"trajectory_id": "t", "message_id": "m", "label_source": "llm",
             "annotator": "independent", "model": "test-review-model", "judgements": [
                 {"criterion_id": "b", "satisfied": True, "reason": "yes"},
                 {"criterion_id": "a", "satisfied": True, "reason": "yes"}]}
    return request, baseline, label


def test_alignment_and_reward_comparison():
    request, baseline, label = fixture()
    result = compare([request], [label], [baseline])
    assert result["criterion_agreement"] == 0.5
    assert result["step_reward_mae"] == 0.25
    assert result["cohen_kappa"] == 0.0
    assert result["label_source"] == "llm"


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "string", "provenance", "rubrics"])
def test_invalid_review_rejected(mutation):
    request, baseline, label = fixture()
    labels = [label]
    if mutation == "duplicate":
        labels.append(copy.deepcopy(label))
    elif mutation == "missing":
        label["judgements"].pop()
    elif mutation == "string":
        label["judgements"][0]["satisfied"] = "false"
    elif mutation == "provenance":
        label["model"] = ""
    else:
        request = copy.deepcopy(request)
        request["request"]["rubrics"][0]["weight"] = 999
    with pytest.raises(ValueError):
        compare([request], labels, [baseline])

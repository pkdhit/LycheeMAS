"""Convert AFTraj math records without exposing detached evaluation labels."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from ...core.types import Message, Trajectory
from .rar_trace_adapter import prepare_steps, require_text, strict_json


def _decode(value: Any) -> Any:
    return strict_json(value) if isinstance(value, str) else value


def convert_aftraj(record: dict[str, Any], *, agent_roles: dict[str, str],
                   history_policy: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return normalized scoring input and a separate source/evaluation sidecar."""
    if history_policy != "shared-prefix":
        raise ValueError("Explicit history_policy='shared-prefix' is required")
    source_id = require_text(record.get("conv_id"), "conv_id")
    task = require_text(record.get("task"), "task")
    if record.get("domain", "").lower() != "math":
        raise ValueError("This adapter currently supports only math")
    turns = record.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("turns must be a nonempty list")
    identity = hashlib.sha256(source_id.encode()).hexdigest()[:20]
    messages: list[Message] = []
    mapping: list[dict[str, Any]] = []
    calls: dict[str, tuple[str, str]] = {}
    completed: set[str] = set()
    omitted_thoughts = 0
    omitted_turns = []

    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise ValueError(f"turn {index} must be an object")
        role = require_text(turn.get("role"), "turn.role")
        content = turn.get("content") or ""
        action = turn.get("action")
        if not isinstance(content, str):
            raise ValueError(f"turn {index}: content must be text")
        omitted_thoughts += bool(turn.get("thought"))
        visible = [message.id for message in messages]

        def append(sender: str, text: str, message_role: str,
                   meta: dict[str, Any], source_item: int | None = None,
                   original_call_id: str | None = None) -> None:
            mid = f"m-{identity}-{len(messages):04d}"
            messages.append(Message(id=mid, sender=sender, content=text,
                                    role=message_role, round=index, ts=0, meta=meta))
            mapping.append({"message_id": mid, "source_turn_index": index,
                            "source_item_index": source_item,
                            "original_call_id": original_call_id})

        if role in {"user", "system"}:
            if action:
                raise ValueError(f"turn {index}: context cannot carry an action")
            append(role, require_text(content, "context content"), role, {})
        elif role == "environment":
            if action:
                raise ValueError(f"turn {index}: environment action is unsupported")
            results = _decode(content)
            if not isinstance(results, list) or not results:
                raise ValueError(f"turn {index}: expected a list of tool results")
            for item_index, result in enumerate(results):
                if not isinstance(result, dict) or "result" not in result:
                    raise ValueError("Malformed tool result")
                cid = require_text(result.get("call_id"), "result.call_id")
                if cid not in calls or cid in completed:
                    raise ValueError(f"Unmatched or duplicate tool result: {cid}")
                normalized_id, name = calls[cid]
                if result.get("name") != name:
                    raise ValueError("Tool result name differs from its call")
                completed.add(cid)
                value = result["result"]
                text = value if isinstance(value, str) else json.dumps(
                    value, ensure_ascii=False, allow_nan=False, sort_keys=True)
                append(name, text, "tool", {"tool_call_id": normalized_id},
                       item_index, cid)
        else:
            require_text(agent_roles.get(role), f"configured role for {role}")
            meta = {"visible_message_ids": visible}
            if content:
                append(role, content, "assistant", dict(meta))
            if action:
                actions = _decode(action)
                if not isinstance(actions, list) or not actions:
                    raise ValueError(f"turn {index}: expected a list of calls")
                for item_index, call in enumerate(actions):
                    if not isinstance(call, dict):
                        raise ValueError("Tool call must be an object")
                    cid = require_text(call.get("id"), "call.id")
                    name = require_text(call.get("name"), "call.name")
                    arguments = _decode(call.get("arguments"))
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must decode to an object")
                    if cid in calls:
                        raise ValueError(f"Duplicate call ID: {cid}")
                    normalized_id = f"c-{len(calls):04d}"
                    calls[cid] = (normalized_id, name)
                    append(role, "", "assistant",
                           {**meta, "action_type": "tool_call", "tool_name": name,
                            "tool_call_id": normalized_id, "tool_arguments": arguments},
                           item_index, cid)
            if not content and not action:
                omitted_turns.append({"source_turn_index": index,
                                      "reason": "no public utterance or tool call"})

    trajectory = Trajectory(
        id=f"aftraj-{identity}", task_id=hashlib.sha256(task.encode()).hexdigest()[:20],
        messages=messages, meta={
            "task_goal": task, "agent_roles": agent_roles,
            "source_dataset": "AFTraj",
            "history_assumption": "shared-prefix: prior source turns; no same-turn siblings",
            "role_assumption": "experiment-configured duties, not recorded system prompts",
            "timestamp_assumption": "unknown; message timestamps set to zero",
        })
    steps = prepare_steps(trajectory)
    step_by_id = {step.message.id: step.step for step in steps}
    for entry in mapping:
        entry["step"] = step_by_id.get(entry["message_id"])
    sidecar = {key: record.get(key) for key in (
        "conv_id", "domain", "gold_answer", "mistake_step", "mistake_agent",
        "mistake_reason", "unsafe_source", "num_turns")}
    sidecar.update(
        mapping=mapping, omitted_thought_turns=omitted_thoughts,
        omitted_turns=omitted_turns,
        unmatched_call_ids=sorted(set(calls) - completed),
        annotation_index_assumption="mistake_step treated as zero-based; verify before metrics",
        candidate_mistake_steps=[
            entry["step"] for entry in mapping
            if entry["source_turn_index"] == record.get("mistake_step")
            and entry["step"] is not None],
    )
    return {"id": trajectory.id, "task_id": trajectory.task_id,
            "meta": trajectory.meta, "messages": [asdict(m) for m in messages]}, sidecar

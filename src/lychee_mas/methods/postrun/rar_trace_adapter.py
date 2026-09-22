"""RaR 的方法私有轨迹适配：只评分 agent 动作，显式声明可见历史。

输入是 core.Message / Trajectory，不猜测旧日志的角色、调用者或广播关系。
原始事件日志应由调用方按 README 的契约转换后交给本适配器。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.types import Message, Trajectory


def require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"rubric_step_reward: {name} 必须是非空字符串")
    return value


def strict_json(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"JSON 不允许 {value}")

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"JSON 重复字段: {key}")
            out[key] = value
        return out

    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_keys)


def load_trajectory(path: str | Path) -> Trajectory:
    """加载一份标准化 JSON；保留稳定的 trajectory/message id，拒绝未知顶层字段。"""
    raw = strict_json(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or set(raw) - {"id", "task_id", "meta", "messages"}:
        raise ValueError("轨迹需包含 id/task_id/meta/messages，不支持其他顶层字段")
    require_text(raw.get("id"), "trajectory.id")
    require_text(raw.get("task_id"), "task_id")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages 必须是非空列表")
    parsed = []
    for item in messages:
        if not isinstance(item, dict):
            raise ValueError("每条 message 必须是对象")
        require_text(item.get("id"), "message.id")
        parsed.append(Message(**item))
    return Trajectory(id=raw["id"], task_id=raw["task_id"],
                      messages=parsed, meta=raw.get("meta", {}))


@dataclass
class ScoringStep:
    step: int
    message: Message
    agent: str
    role: str
    action_type: str
    history: list[dict[str, Any]]
    output: str


def _action_type(message: Message) -> str:
    declared = message.meta.get("action_type")
    if message.role in {"user", "system"}:
        if declared not in (None, "context"):
            raise ValueError("用户/系统消息不能声明为 agent 动作")
        return "context"
    if message.role == "tool":
        if declared not in (None, "tool_result"):
            raise ValueError("role=tool 是工具返回，不是工具调用")
        return "tool_result"
    if message.role != "assistant":
        raise ValueError(f"未知消息 role={message.role!r}")
    if "tool_calls" in message.meta or "tool_call" in message.meta:
        raise ValueError("请先将 tool_calls 拆为独立 Message，并提供 tool_call_id/tool_arguments")
    action = declared or ("tool_call" if message.meta.get("tool_name") else "utterance")
    if action not in {"utterance", "tool_call"}:
        raise ValueError(f"未知 agent 动作: {action!r}")
    tool_fields = {"tool_name", "tool_call_id", "tool_arguments"}
    if action == "utterance" and tool_fields.intersection(message.meta):
        raise ValueError("发言消息不能携带工具调用字段；请拆分步骤")
    return action


def _output(message: Message, action: str) -> str:
    if action == "tool_call":
        return json.dumps({"content": message.content, "tool_name": message.meta["tool_name"],
                           "tool_arguments": message.meta["tool_arguments"]},
                          ensure_ascii=False, allow_nan=False, sort_keys=True)
    return message.content


def prepare_steps(trajectory: Trajectory, *, include_tool_steps: bool = True,
                  history_mode: str = "explicit") -> list[ScoringStep]:
    """explicit 要求逐步列出可见消息；all_previous 仅用于明确声明的全广播轨迹。"""
    if not isinstance(trajectory, Trajectory):
        raise TypeError("需要 core.Trajectory")
    require_text(trajectory.id, "trajectory.id")
    require_text(trajectory.task_id, "task_id")
    if not isinstance(trajectory.meta, dict):
        raise ValueError("trajectory.meta 必须是对象")
    if history_mode not in {"explicit", "all_previous"}:
        raise ValueError("history_mode 必须为 explicit 或 all_previous")
    if not isinstance(trajectory.messages, list) or not trajectory.messages:
        raise ValueError("需要至少一条 Message")
    roles = trajectory.meta.get("agent_roles", {})
    if not isinstance(roles, dict):
        raise ValueError("agent_roles 必须是 agent -> 工作角色的对象")
    previous: dict[str, dict[str, Any]] = {}
    calls: dict[str, str] = {}
    results: set[str] = set()
    steps: list[ScoringStep] = []
    step_number = 0
    for message in trajectory.messages:
        if not isinstance(message, Message) or not isinstance(message.meta, dict):
            raise TypeError("需要 Message 且 meta 为对象")
        require_text(message.id, "message.id")
        require_text(message.sender, "message.sender")
        if message.id in previous:
            raise ValueError(f"重复 message.id: {message.id}")
        if not isinstance(message.content, str):
            raise TypeError("message.content 必须是字符串")
        action = _action_type(message)
        if action == "tool_call":
            call_id = require_text(message.meta.get("tool_call_id"), "tool_call_id")
            require_text(message.meta.get("tool_name"), "tool_name")
            if not isinstance(message.meta.get("tool_arguments"), dict):
                raise ValueError("tool_arguments 必须是对象")
            if call_id in calls:
                raise ValueError(f"重复 tool_call_id: {call_id}")
            calls[call_id] = message.sender
        elif action == "tool_result":
            call_id = require_text(message.meta.get("tool_call_id"), "tool_call_id")
            if call_id not in calls or call_id in results:
                raise ValueError("工具返回必须对应此前唯一调用，不接受未关联或重复结果")
            results.add(call_id)
        role = message.role
        if action in {"utterance", "tool_call"}:
            step_number += 1
            role = require_text(message.meta.get("agent_role") or roles.get(message.sender),
                                f"{message.sender} 的 agent_role")
            visible = message.meta.get("visible_message_ids")
            if visible is None and history_mode == "all_previous":
                visible = list(previous)
            if (not isinstance(visible, list)
                    or any(not isinstance(mid, str) for mid in visible)):
                raise ValueError("每个 agent 动作必须提供 visible_message_ids 列表")
            if len(set(visible)) != len(visible) or any(mid not in previous for mid in visible):
                raise ValueError("可见历史含重复、当前、未来或不存在的 message id")
            visible_set = set(visible)
            history = [value for mid, value in previous.items() if mid in visible_set]
            if include_tool_steps or action != "tool_call":
                steps.append(ScoringStep(step_number, message, message.sender, role,
                                         action, history, _output(message, action)))
        entry = {"message_id": message.id, "agent": message.sender, "role": role,
                 "action_type": action, "content": _output(message, action)}
        if action in {"tool_call", "tool_result"}:
            entry["tool_call_id"] = message.meta["tool_call_id"]
            entry["initiating_agent"] = calls[message.meta["tool_call_id"]]
        previous[message.id] = entry
    if not steps:
        raise ValueError("没有可评分的 agent 发言或工具调用")
    return steps

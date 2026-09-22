"""步骤级 rubric 奖励（postrun 读侧可跑实现）。

复现目标：把一次智能体发言或工具调用视为一步，基于任务目标、当前角色与此前
交互历史自动生成该步评分细则；再由评审模型逐条判断“满足/不满足”并给理由，
按权重归一化得到步骤奖励，最终在 ``trajectory.meta`` 中产出可审计结果表。

本文件只依赖标准库。真实评审模型通过 ``judge_llm`` / ``rubric_llm`` 回调注入，
不在 methods 层直接 import transformers/openai。没有评审回调时，必须显式设置
``heuristic_judge=True`` 才会启用离线启发式评审；默认不会静默兜底。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ...core.registry import REGISTRY
from ...core.types import Message, Trajectory
from .base import Attribution

LLMCallback = Callable[[str], str]


@dataclass
class StepRubric:
    """单条评分细则。weight 会在单步内重新归一化。"""

    id: str
    description: str
    weight: float = 1.0
    rationale: str = ""


@dataclass
class RubricJudgement:
    """评审模型对一条评分细则的判断。"""

    criterion_id: str
    satisfied: bool
    reason: str


@dataclass
class StepRewardRecord:
    """一步的聚合奖励与审计明细。"""

    step: int
    agent: str
    role: str
    action_type: str
    output: str
    reward: float
    rubrics: list[dict[str, Any]] = field(default_factory=list)
    judgements: list[dict[str, Any]] = field(default_factory=list)


DEFAULT_RUBRICS: tuple[dict[str, Any], ...] = (
    {
        "id": "subtask_completion",
        "description": "是否完成当前角色在该步应推进的子任务，而不是只复述问题或空泛表态。",
        "weight": 0.40,
        "rationale": "步骤级奖励首先衡量本步对任务进展的直接贡献。",
    },
    {
        "id": "use_existing_information",
        "description": "是否正确使用此前交互历史中的已有信息，且没有捏造与历史冲突的内容。",
        "weight": 0.25,
        "rationale": "MAS 中每个 agent 应消费上游信息，而不是割裂地重新回答。",
    },
    {
        "id": "evidence_or_tool_grounding",
        "description": "是否提供必要依据；若是工具调用，是否说明或体现了合理的工具使用目标。",
        "weight": 0.20,
        "rationale": "鼓励可核查推理、引用证据和合规工具使用。",
    },
    {
        "id": "role_and_format_compliance",
        "description": "是否符合当前智能体角色、任务约束与必要输出格式。",
        "weight": 0.15,
        "rationale": "角色分工与格式约束是多智能体协作可控性的基础。",
    },
)


def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


def _json_from_text(text: str) -> Any:
    """从模型回复中解析 JSON；失败显式 ValueError。"""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    matches = re.findall(r"(\{.*\}|\[.*\])", raw, flags=re.DOTALL)
    for candidate in matches:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"rubric_step_reward: 评审模型输出不是可解析 JSON: {text[:200]!r}")


def _call_llm(callback: Callable[[str], Any], prompt: str, *, name: str) -> str:
    """调用同步/异步回调；若已在事件循环中收到 awaitable，显式报错。"""

    out = callback(prompt)
    if inspect.isawaitable(out):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            out = asyncio.run(out)  # type: ignore[arg-type]
        else:
            raise RuntimeError(
                f"rubric_step_reward: {name} 返回 awaitable，但当前已有运行中的事件循环；"
                "请传入同步包装器或在外层直接调用异步评审流程。")
    if not isinstance(out, str):
        raise TypeError(f"rubric_step_reward: {name} 必须返回 str，得到 {type(out).__name__}")
    return out


def _message_role(message: Message) -> str:
    return str(
        message.meta.get("agent_role")
        or message.meta.get("role")
        or message.role
        or "agent"
    )


def _action_type(message: Message) -> str:
    if message.meta.get("tool_name") or message.meta.get("tool_call") or message.role == "tool":
        return "tool_call"
    return "utterance"


def _history_text(messages: Sequence[Message], *, limit: int) -> str:
    chunks: list[str] = []
    for i, m in enumerate(messages, start=1):
        role = _message_role(m)
        chunks.append(f"[{i}] agent={m.sender} role={role}: {_clip(m.content, 600)}")
    return _clip("\n".join(chunks), limit)


def _task_goal_from(trajectory: Trajectory, context: Any, configured_goal: str | None) -> str:
    if configured_goal:
        return configured_goal
    if isinstance(context, Mapping):
        for key in ("task_goal", "goal", "question"):
            if context.get(key):
                return str(context[key])
    for key in ("task_goal", "goal", "question"):
        if trajectory.meta.get(key):
            return str(trajectory.meta[key])
    if trajectory.task_id:
        return str(trajectory.task_id)
    raise ValueError(
        "rubric_step_reward: 缺少任务目标；请在构造器传 task_goal，或在 "
        "trajectory.meta['task_goal'/'question'] / context 中提供。")


def _coerce_rubrics(raw: Any) -> list[StepRubric]:
    if isinstance(raw, Mapping):
        raw_items = raw.get("rubrics") or raw.get("criteria") or raw.get("items")
    else:
        raw_items = raw
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("rubric_step_reward: rubric 生成结果必须是非空列表")

    rubrics: list[StepRubric] = []
    for idx, item in enumerate(raw_items, start=1):
        if isinstance(item, str):
            rubrics.append(StepRubric(id=f"c{idx}", description=item, weight=1.0))
            continue
        if not isinstance(item, Mapping):
            raise TypeError("rubric_step_reward: 每条 rubric 必须是 dict 或 str")
        desc = item.get("description") or item.get("criterion") or item.get("text")
        if not desc:
            raise ValueError("rubric_step_reward: rubric 缺少 description/criterion/text")
        weight = float(item.get("weight", 1.0))
        if weight < 0:
            raise ValueError("rubric_step_reward: rubric weight 不能为负数")
        rubrics.append(StepRubric(
            id=str(item.get("id") or f"c{idx}"),
            description=str(desc),
            weight=weight,
            rationale=str(item.get("rationale") or item.get("reason") or ""),
        ))
    if sum(r.weight for r in rubrics) <= 0:
        raise ValueError("rubric_step_reward: rubric 权重和必须大于 0")
    return rubrics


def _coerce_judgements(raw: Any, rubrics: Sequence[StepRubric]) -> list[RubricJudgement]:
    if isinstance(raw, Mapping):
        raw_items = raw.get("judgements") or raw.get("judgments") or raw.get("criteria")
    else:
        raw_items = raw
    if not isinstance(raw_items, list):
        raise ValueError("rubric_step_reward: 评审结果必须包含 judgements 列表")
    if len(raw_items) != len(rubrics):
        raise ValueError(
            f"rubric_step_reward: 评审条数({len(raw_items)})与 rubric 条数({len(rubrics)})不一致")

    out: list[RubricJudgement] = []
    for rubric, item in zip(rubrics, raw_items):
        if not isinstance(item, Mapping):
            raise TypeError("rubric_step_reward: 每条 judgement 必须是 dict")
        if "satisfied" not in item:
            raise ValueError("rubric_step_reward: judgement 缺少 satisfied 字段")
        reason = item.get("reason") or item.get("rationale") or item.get("evidence")
        if not reason:
            raise ValueError("rubric_step_reward: judgement 缺少 reason/rationale/evidence")
        out.append(RubricJudgement(
            criterion_id=str(item.get("criterion_id") or item.get("id") or rubric.id),
            satisfied=bool(item["satisfied"]),
            reason=str(reason),
        ))
    return out


def _normalize_reward(
    rubrics: Sequence[StepRubric], judgements: Sequence[RubricJudgement]
) -> float:
    weight_sum = sum(r.weight for r in rubrics)
    if weight_sum <= 0:
        raise ValueError("rubric_step_reward: rubric 权重和必须大于 0")
    score = 0.0
    for rubric, judgement in zip(rubrics, judgements):
        if judgement.satisfied:
            score += rubric.weight
    return max(0.0, min(1.0, score / weight_sum))


def _rubric_prompt(
    *, task_goal: str, step: int, agent: str, role: str, action_type: str,
    history: str, output: str, max_rubrics: int,
) -> str:
        return f"""你是多智能体系统步骤级奖励的 rubric 生成器。
请基于任务目标、当前角色、此前交互历史与本步输出，
为“第 {step} 步”生成最多 {max_rubrics} 条评分细则。

要求：
1. 每条细则必须能由评审模型判断满足/不满足；
2. 覆盖：当前子任务完成度、已有信息使用、必要依据/工具合理性、角色/格式约束；
3. 权重为非负数，后续会在本步内归一化；
4. 只输出 JSON，不要 Markdown。

输出格式：
{{"rubrics": [{{"id": "c1", "description": "...", "weight": 0.4, "rationale": "..."}}]}}

任务目标：{task_goal}
当前步骤：{step}
智能体：{agent}
当前角色：{role}
动作类型：{action_type}
此前交互历史：
{history or "(无)"}
本步实际输出：
{output}
"""


def _judge_prompt(
    *, task_goal: str, step: int, agent: str, role: str, action_type: str,
    history: str, output: str, rubrics: Sequence[StepRubric],
) -> str:
    rubric_json = json.dumps([asdict(r) for r in rubrics], ensure_ascii=False, indent=2)
    return f"""你是多智能体系统步骤级奖励的严格评审模型。
请逐条判断本步实际输出是否满足评分细则，并给出简短依据。

判定规则：
- satisfied 只能是 true 或 false；
- 不确定、缺证据、与历史冲突，一律判 false；
- 只输出 JSON，不要 Markdown。

输出格式：
{{"judgements": [{{"criterion_id": "c1", "satisfied": true, "reason": "..."}}]}}

任务目标：{task_goal}
当前步骤：{step}
智能体：{agent}
当前角色：{role}
动作类型：{action_type}
此前交互历史：
{history or "(无)"}
本步实际输出：
{output}

评分细则：
{rubric_json}
"""


def _heuristic_judge(
    *, rubrics: Sequence[StepRubric], output: str, history: str, min_content_chars: int
) -> list[RubricJudgement]:
    """显式 opt-in 的离线启发式评审，供无模型 smoke/人工抽查前预览。"""

    text = output.strip()
    lower = text.lower()
    enough = len(text) >= min_content_chars
    has_evidence = any(k in text for k in ("因为", "因此", "根据", "依据", "证据", "所以"))
    has_evidence = has_evidence or bool(re.search(r"\d|because|therefore|evidence", lower))
    history_terms = {w for w in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", history)[:80]}
    uses_history = not history_terms or any(term in text for term in history_terms)

    out: list[RubricJudgement] = []
    for rubric in rubrics:
        desc = rubric.description
        if "已有" in desc or "历史" in desc or "信息" in desc:
            ok = enough and uses_history
        elif "依据" in desc or "工具" in desc or "证据" in desc:
            ok = enough and has_evidence
        elif "角色" in desc or "格式" in desc or "约束" in desc:
            ok = enough
        else:
            ok = enough and "不知道" not in text and "无法" not in text
        reason = (
            "启发式判定：满足基本文本/历史/依据信号。"
            if ok
            else "启发式判定：缺少必要文本、历史引用或依据。"
        )
        out.append(RubricJudgement(rubric.id, ok, reason))
    return out


@REGISTRY.register("attributor", "rubric_step_reward")
class RubricStepRewardAttributor:
    """自动生成步骤级 rubrics，并计算每步归一化奖励。

    典型用法::

        analyze_run(traj, method="rubric_step_reward", credit_assigner="step_reward",
                    task_goal="回答数学题并给出依据", judge_llm=my_sync_llm)

    结果写入 ``trajectory.meta["rubric_step_reward"]``，包含：
    ``steps``（每步聚合奖励）、``criteria_rows``（步骤-智能体-评分细则-奖励值-
    评分依据明细表）和 ``agent_rewards``（按 agent 聚合）。
    """

    name = "rubric_step_reward"

    def __init__(
        self,
        *,
        task_goal: str | None = None,
        rubric_llm: Callable[[str], Any] | None = None,
        judge_llm: Callable[[str], Any] | None = None,
        default_rubrics: Sequence[Mapping[str, Any]] | None = None,
        heuristic_judge: bool = False,
        include_tool_steps: bool = True,
        max_rubrics: int = 6,
        max_history_chars: int = 4000,
        max_output_chars: int = 3000,
        min_content_chars: int = 1,
        reward_threshold: float = 0.5,
        **kwargs: Any,
    ) -> None:
        if max_rubrics <= 0:
            raise ValueError("rubric_step_reward: max_rubrics 必须大于 0")
        if max_history_chars < 0 or max_output_chars < 0:
            raise ValueError("rubric_step_reward: max_history_chars/max_output_chars 不能为负数")
        self.task_goal = task_goal
        self.rubric_llm = rubric_llm
        self.judge_llm = judge_llm
        self.default_rubrics = list(default_rubrics or DEFAULT_RUBRICS)
        self.heuristic_judge = bool(heuristic_judge)
        self.include_tool_steps = bool(include_tool_steps)
        self.max_rubrics = int(max_rubrics)
        self.max_history_chars = int(max_history_chars)
        self.max_output_chars = int(max_output_chars)
        self.min_content_chars = int(min_content_chars)
        self.reward_threshold = float(reward_threshold)
        self.cfg = kwargs

    def attribute(self, trajectory: Trajectory, context: Any = None) -> list[Attribution]:
        if not isinstance(trajectory, Trajectory):
            raise TypeError(
                "rubric_step_reward: trajectory 必须是 Trajectory，"
                f"得到 {type(trajectory).__name__}"
            )
        if not trajectory.messages:
            raise ValueError("rubric_step_reward: 需要至少一条 Message 才能计算步骤级奖励")
        if self.judge_llm is None and not self.heuristic_judge:
            raise ValueError(
                "rubric_step_reward: 需要 judge_llm 评审回调；若仅做离线预览，"
                "请显式设置 heuristic_judge=True。")

        task_goal = _task_goal_from(trajectory, context, self.task_goal)
        records: list[StepRewardRecord] = []
        rows: list[dict[str, Any]] = []
        attributions: list[Attribution] = []

        for zero_idx, message in enumerate(trajectory.messages):
            action_type = _action_type(message)
            if action_type == "tool_call" and not self.include_tool_steps:
                continue
            step = zero_idx + 1
            agent = message.sender or f"step_{step}"
            role = _message_role(message)
            history = _history_text(trajectory.messages[:zero_idx], limit=self.max_history_chars)
            output = _clip(message.content, self.max_output_chars)

            rubrics = self._generate_rubrics(
                task_goal=task_goal, step=step, agent=agent, role=role,
                action_type=action_type, history=history, output=output)
            judgements = self._judge(
                task_goal=task_goal, step=step, agent=agent, role=role,
                action_type=action_type, history=history, output=output, rubrics=rubrics)
            reward = _normalize_reward(rubrics, judgements)

            record = StepRewardRecord(
                step=step,
                agent=agent,
                role=role,
                action_type=action_type,
                output=output,
                reward=reward,
                rubrics=[asdict(r) for r in rubrics],
                judgements=[asdict(j) for j in judgements],
            )
            records.append(record)

            for rubric, judgement in zip(rubrics, judgements):
                weight_sum = sum(r.weight for r in rubrics)
                contribution = (rubric.weight / weight_sum) if judgement.satisfied else 0.0
                rows.append({
                    "step": step,
                    "agent": agent,
                    "role": role,
                    "action_type": action_type,
                    "criterion_id": rubric.id,
                    "评分细则": rubric.description,
                    "weight": rubric.weight,
                    "satisfied": judgement.satisfied,
                    "reward_value": contribution,
                    "step_reward": reward,
                    "评分依据": judgement.reason,
                })

            attributions.append(Attribution(
                agent=agent,
                step=step,
                is_fault=reward < self.reward_threshold,
                reason=f"步骤奖励={reward:.4f}; 低于阈值={reward < self.reward_threshold}",
                confidence=reward,
                meta={
                    "method": self.name,
                    "step_reward": reward,
                    "role": role,
                    "action_type": action_type,
                    "rubrics": [asdict(r) for r in rubrics],
                    "judgements": [asdict(j) for j in judgements],
                },
            ))

        agent_rewards = _aggregate_agent_rewards(records)
        trajectory.meta["rubric_step_reward"] = {
            "task_goal": task_goal,
            "steps": [asdict(r) for r in records],
            "criteria_rows": rows,
            "agent_rewards": agent_rewards,
        }
        return attributions

    def _generate_rubrics(
        self, *, task_goal: str, step: int, agent: str, role: str, action_type: str,
        history: str, output: str,
    ) -> list[StepRubric]:
        if self.rubric_llm is None:
            return _coerce_rubrics(self.default_rubrics)[: self.max_rubrics]
        prompt = _rubric_prompt(
            task_goal=task_goal, step=step, agent=agent, role=role, action_type=action_type,
            history=history, output=output, max_rubrics=self.max_rubrics)
        raw = _call_llm(self.rubric_llm, prompt, name="rubric_llm")
        return _coerce_rubrics(_json_from_text(raw))[: self.max_rubrics]

    def _judge(
        self, *, task_goal: str, step: int, agent: str, role: str, action_type: str,
        history: str, output: str, rubrics: Sequence[StepRubric],
    ) -> list[RubricJudgement]:
        if self.judge_llm is None:
            return _heuristic_judge(
                rubrics=rubrics, output=output, history=history,
                min_content_chars=self.min_content_chars)
        prompt = _judge_prompt(
            task_goal=task_goal, step=step, agent=agent, role=role, action_type=action_type,
            history=history, output=output, rubrics=rubrics)
        raw = _call_llm(self.judge_llm, prompt, name="judge_llm")
        return _coerce_judgements(_json_from_text(raw), rubrics)


def _aggregate_agent_rewards(records: Sequence[StepRewardRecord]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for record in records:
        grouped.setdefault(record.agent, []).append(record.reward)
    return {agent: sum(vals) / len(vals) for agent, vals in grouped.items() if vals}


@REGISTRY.register("credit_assigner", "step_reward")
class StepRewardCreditAssigner:
    """把 rubric_step_reward 的步骤奖励聚合为 per-agent 信用。"""

    name = "step_reward"

    def __init__(
        self,
        *,
        aggregation: str = "mean",
        use_external_reward: bool = False,
        **kwargs: Any,
    ) -> None:
        if aggregation not in {"mean", "sum"}:
            raise ValueError("step_reward: aggregation 只支持 'mean' 或 'sum'")
        self.aggregation = aggregation
        self.use_external_reward = bool(use_external_reward)
        self.cfg = kwargs

    def credits(self, attributions: list[Attribution], reward: float) -> dict[str, float]:
        grouped: dict[str, list[float]] = {}
        for attr in attributions:
            value = attr.meta.get("step_reward", attr.confidence)
            grouped.setdefault(attr.agent, []).append(float(value))
        credits: dict[str, float] = {}
        for agent, vals in grouped.items():
            if not vals:
                continue
            score = sum(vals) if self.aggregation == "sum" else sum(vals) / len(vals)
            if self.use_external_reward:
                score *= float(reward)
            credits[agent] = score
        return credits


__all__ = [
    "RubricStepRewardAttributor",
    "StepRewardCreditAssigner",
    "StepRubric",
    "RubricJudgement",
    "StepRewardRecord",
]

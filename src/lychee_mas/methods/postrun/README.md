# `trace` — 归因 / 信用 + 轨迹落点（顶层包，读侧）

## 功能

「**读**」执行轨迹的一侧：回答「一条轨迹里谁该为成败负责」。三件事：错误归因（`attributor`）、信用分配（`credit_assigner`）、统一落点（`TraceStore`）。经 postrun 接缝 `analyze_run` / `optimize_postrun` 挂载；产出的信用后续可喂给 `train`。

## 接口（`base.py`）

```python
@dataclass
class Attribution:            # 一次归因结果
    agent: str; step: int; is_fault: bool; reason: str; confidence: float; meta: dict

class FailureAttributor(Protocol):
    def attribute(self, trajectory: Trajectory, context: Any = None) -> list[Attribution]: ...

class CreditAssigner(Protocol):
    def credits(self, attributions: list[Attribution], reward: float) -> dict[str, float]: ...
```

## `store.py` — `TraceStore`

```python
TraceStore(path=None)         # path 给定则同步 JSONL 落盘，否则仅内存累积
  .hook(message)              # 接 Runtime.intercept：逐条 Message 写入
  .log_decision(record)       # 决策/归因记录
  .reset(); len(store)
```

纯标准库。这是记忆/处理/训练的数据来源之一。

## 已注册组件

| 类别 | 注册名 |
|---|---|
| `attributor` | `rubric_step_reward`（可跑） / `all_at_once` / `step_by_step` / `binary_search`（桩） |
| `credit_assigner` | `step_reward`（可跑） / `attribution_guided`（桩） |

import 本包触发注册，零重依赖。

## `rubric_step_reward` — 步骤级细则生成与奖励计算

把每条 `Trajectory.messages` 视为一步（智能体发言或工具调用）：

1. 根据任务目标、当前 agent/role、此前历史和本步输出生成评分细则；
2. 调用评审模型逐条输出 `satisfied` 与 `reason`；
3. 按权重归一化得到 `step_reward ∈ [0, 1]`；
4. 在 `trajectory.meta["rubric_step_reward"]` 写入：
   - `steps`：每步奖励与 rubrics/judgements；
   - `criteria_rows`：步骤—智能体—评分细则—奖励值—评分依据结果表；
   - `agent_rewards`：按 agent 聚合的平均步骤奖励。

```python
from lychee_mas.plugins.postrun import analyze_run

credits = analyze_run(
    traj,
    method="rubric_step_reward",
    credit_assigner="step_reward",
    task_goal="完成题目并给出必要依据",
    judge_llm=my_sync_or_async_llm,      # async(prompt)->str 或 sync(prompt)->str
    rubric_llm=None,                    # 缺省使用通用四类细则；也可注入模型自动生成
)
rows = traj.meta["rubric_step_reward"]["criteria_rows"]
```

默认没有 `judge_llm` 会显式报错；如果只想本地预览，可显式传 `heuristic_judge=True` 启用启发式评审。配置模板见 `configs/postrun/rubric_step_reward.yaml`。

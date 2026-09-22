# postrun：归因协议与 RaR 步骤奖励

公共协议在 base.py，统一入口在 plugins/postrun/base.py。
`analyze_run(trajectory, score, method, credit_assigner, **kwargs)` 返回 per-agent 汇总，
完整步骤结果保存在 `trajectory.meta["rubric_step_reward"]`。
不修改公共 plugins/core 接口，不使用 trainer 或图优化器进行评分。

## 注册状态

| 类别 | 实现 | 桩 |
|---|---|---|
| attributor | rubric_step_reward | all_at_once / step_by_step / binary_search |
| credit_assigner | step_reward | attribution_guided |
| post_run_optimizer | 无 | attribution / train |

TraceStore 支持内存消息/决策和可选 JSONL。analyze_run 的决策日志只有汇总，
完整细则/依据由本方法的导出函数保存，不能只交付 TraceStore 汇总。

## 文件职责

- rubric_step_reward.py：注册类、生成/评审提示、严格 JSON 校验、奖励计算。
- rar_trace_adapter.py：规范化 JSON 加载，校验步骤、角色、调用归属、可见历史。
- rar_replay.py：显式离线回放回调，不模拟真实模型的语义能力。
- rar_export.py：完整结果、CSV、盲标模板和人工对照分析。
- scripts/run_rar_step_reward.py：离线实验入口。
- scripts/evaluate_rar_annotations.py：人工抽查入口，不自动填写人工标签。

## 输入契约

文件输入为单个 JSON 对象，只接受 id、task_id、meta、messages。
内存入口使用 core.Trajectory / core.Message。ID 必须稳定且唯一，不能依赖重跑随机生成。
完整样例见 examples/data/rar_trajectory.json。

```json
{
  "id": "trajectory-001",
  "task_id": "task-001",
  "meta": {
    "task_goal": "计算6×7并引用工具结果",
    "agent_roles": {"solver": "执行者：计算并提供依据"}
  },
  "messages": [{
    "id": "m1", "sender": "solver", "role": "assistant", "content": "计算乘积",
    "meta": {
      "action_type": "tool_call", "tool_name": "calculator",
      "tool_call_id": "call-1", "tool_arguments": {"expression": "6*7"},
      "visible_message_ids": []
    }
  }]
}
```

规则：

1. 目标来自显式 task_goal、直接调用 attribute 的 context 或 trajectory.meta。
   analyze_run 固定传 context=None，因此该入口应使用构造参数或 meta。
   task_id 不能代替任务文本。
2. 工作角色来自 Message.meta.agent_role 或 trajectory.meta.agent_roles[sender]。
   Message.role=assistant 是聊天协议角色，不能充当“规划者/执行者”。
3. assistant 发言或调用各算一步；user/system 是上下文；role=tool 是工具返回，
   必须关联此前唯一的 tool_call_id，不额外计分。调用 sender 必须是发起 agent。
4. 一条含多次调用的原始消息必须拆成多个稳定 ID 的 Message，独立记录调用名/参数；
   本适配器不猜测各框架的原始事件格式，不会自动改写旧日志。
5. 默认 history_mode=explicit，每个动作必须有 visible_message_ids，可以为空。
   引用只能是此前消息，不允许重复、当前或未来消息；保持原始时间顺序。
   调用方必须记录 agent 当时真实可见的消息。全广播任务才显式用 all_previous。
6. 工具返回应按实际可见性列入后续动作的历史；调用的评分不看未来结果。
   私有思考、未来工具返回、final_answer、标准答案不会自动进入提示。
7. 长度默认不限；设置正的长度预算后超限报错，不静默截断评分依据。

## 生成、评审和计算

生成细则只读取目标、当前角色、动作类型、此前可见历史，不传本步输出。
评审再读取实际发言或工具调用名称/参数。两种回调接收 JSON 提示并返回 JSON 字符串。

```json
{"rubrics": [{"id": "c1", "description": "正向、明确、可判定的要求", "weight": 0.7,
              "rationale": "该要求与当前子任务的关系"}]}
```

```json
{"judgements": [{"criterion_id": "c1", "satisfied": false, "reason": "具体评分依据"}]}
```

每步 rubric ID 唯一，weight 为有限正数，总权重也必须有限。
judgement 必须覆盖全部且仅有的 ID；允许乱序，按 ID 对齐，拒绝重复/缺失/未知 ID。
字符串 false、0、null 都不是合法布尔值，不能静默转换。
非 JSON、重复 JSON 字段、NaN、Infinity、空理由和超额条目均报错。

单步奖励 `sum(weight * satisfied) / sum(weight)` 在 [0,1]。
表中 reward_value 是单条细则的归一化贡献，step_reward 才是整步奖励。
同一步各条贡献求和等于 step_reward，不要对重复显示的 step_reward 求和。

aggregation=mean|sum 由 attributor 写进 Attribution.meta，默认构造的 assigner 读取它，
解决统一入口不透传 assigner kwargs 的问题；公共接口不变。
不乘外部任务 score。直接构造 assigner 时的配置须与元数据一致。
is_fault 仅为局部阈值标签，不是因果定位；confidence=0.0 是协议占位，
meta.confidence_available=false 表示未估计置信度。

## 回调使用

```python
from lychee_mas.plugins.postrun import analyze_run

credits = analyze_run(
    trajectory, method="rubric_step_reward", credit_assigner="step_reward",
    rubric_llm=generate_rubrics, judge_llm=judge_output,
    history_mode="explicit", aggregation="mean",
)
rows = trajectory.meta["rubric_step_reward"]["criteria_rows"]
```

默认两个回调都必须注入。同步回调返回 str；无运行中事件循环时可自动等待异步回调。
异步应用使用 `await asyncio.to_thread(analyze_run, ...)`，避免嵌套事件循环。
真实模型以后由脚本通过 backends 包装为回调，本次不配置/测试真实调用。
固定细则消融须显式传 default_rubrics，不能同时传 rubric_llm；报告标记 fixed_baseline。
删除了默认固定细则和启发式 judge 兜底；旧 heuristic_judge/min_content_chars/
use_external_reward 参数会显式报错。

## 离线运行（PowerShell，仓库根目录）

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe scripts/run_rar_step_reward.py `
  --input examples/data/rar_trajectory.json `
  --replay examples/data/rar_replay.json `
  --config configs/postrun/rubric_step_reward.yaml `
  --output runs/rar/my-offline-run
```

脚本只需 Python 标准库与 Git；使用 YAML 配置另需 pyyaml，不传 --config 也可运行。
这是实验入口，不是专项测试脚本。回放核对上下文、输出和消费次数。
样例含两名 agent、三步、一次调用、一条不可见私有消息，以及乱序评审回复。
预期步骤奖励 [1.0, 1.0, 0.7]，agent 均值 planner=1.0、solver=0.85。

输出目录必须不存在，防止覆盖标注：

- results.json：完整结果、实际参数、seed、Git SHA/dirty 状态、源码/输入哈希、耗时。
- criteria.csv：步骤—智能体—评分细则—单条贡献—步骤奖励—评分依据。
- model_calls.json：回放请求和回复，供审查生成阶段是否泄漏答案。
- human_annotations.csv：不含模型判断/理由/奖励的盲标模板，含目标/角色/历史/输出。

报告明确 mode=offline_replay、model_calls=0、token=0、accuracy=null。
回放验证机制与计算，不等价于真实生成或评分质量。产物位于被 gitignore 忽略的 runs/。

## 人工抽查

抽样保留整个步骤的细则。建议覆盖不同角色和工具动作，先取20～30步（不保证统计充分）。
填写 annotator、human_satisfied（true/false）、human_reason；
可另填 rubric_relevant/rubric_clear（true/false）审查相关性与可判定性。
保持三个 ID 不变；未标行全部留空，可保留或删除，不要复制模型标签当人工真值。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_rar_annotations.py `
  --results runs/rar/my-offline-run/results.json `
  --annotations runs/rar/my-offline-run/human_annotations.csv `
  --output runs/rar/my-offline-run/human_review.json
```

空标注报错；部分标注报告覆盖率；仅完整标注步骤计奖励 MAE；重复/未知 ID 报错。
一致率是对独立人工判断的一致程度，不是答案准确率。本次交付工具，不伪造人工标注。

## 论文与差异

参考 [Rubrics as Rewards](https://arxiv.org/abs/2507.17746)。按项目接口独立编写的 MAS 适配，
未引入原作者代码或提示词，不声称复现论文数值。响应级奖励改为动作级，正权重/正向细则，
不做 GRPO。范围与验收见 docs/plans/rar-reproduction-plan.md。

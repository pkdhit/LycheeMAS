# RaR 步骤奖励：相对最原始项目的合并改动说明

> 用途：向协作者交接、审查代码、准备 PR，区分两轮贡献，避免把协作者已有成果重复算作后续新增。
>
> 仓库：D:/LycheeMAS-collab；分支：postrun_stepreward。
> 原始基线：9189cc2（本次 RaR 开发开始前的 LycheeMASv0.3 主线）。
> 协作者版本：8e796d0，作者 pkdhit，提交说明“新增 step reward 的 rubric 评分实现”。
> 我的版本：在 8e796d0 上继续修改的当前工作区，由本人提出需求并借助 Codex 完成；编写本文时尚未 commit/push。
>
> 基线核验：与 D:/LycheeMAS-LycheeMASv0.3 原项目目录比对的188个文件，在统一换行符后内容一致。
> 本文统计 collab 仓库中该功能分支的累计改动；旧项目目录中的独立草稿不属于本分支改动。
> 文中所有代码路径均相对 D:/LycheeMAS-collab，标注“新增/修改”时以各部分指定的起点为准。

## 第一部分：协作者修改的部分

### 1. 对比范围与原始项目状态

本部分对应 `9189cc2 → 8e796d0`。协作者共改动6个文件：新增3个、修改3个。
Git 记录为新增778行、删除10行；行数仅表示变更量，不代表完成度。

原始项目已经提供：

- core.Message、core.Trajectory 等公共数据结构。
- REGISTRY 注册表和方法分发机制。
- analyze_run() 运行后分析入口。
- Attribution、FailureAttributor、CreditAssigner 协议。
- TraceStore 消息/决策保存能力。
- all_at_once、step_by_step、binary_search 和 attribution_guided 等占位组件。

这些原有基础能力不属于本次两人的新增贡献。原始项目没有本分支的 RaR 细则生成与步骤奖励实现。

### 2. 协作者逐文件改动

| 文件 | 相对原始基线 | 协作者完成的内容 |
|---|---|---|
| src/lychee_mas/methods/postrun/rubric_step_reward.py | 新增 | 554行的步骤奖励初版，包含数据结构、生成/评审提示、回调、奖励计算和结果明细 |
| configs/postrun/rubric_step_reward.yaml | 新增 | 方法名、评审方式、长度限制、阈值及汇总方式等配置模板 |
| 复现代码说明.txt | 新增 | 初版实现范围、字段说明、调用示例与未完成内容 |
| src/lychee_mas/methods/postrun/__init__.py | 修改 | 导入新模块，触发注册并导出新类 |
| src/lychee_mas/methods/postrun/README.md | 修改 | 增加方法功能、结果字段、最小使用示例 |
| docs/DESIGN.md | 修改 | 更新 postrun 能力说明与组件注册表 |

### 3. 协作者新增的核心能力

| 类／函数／字段 | 原始贡献 |
|---|---|
| StepRubric | 保存细则编号、描述、权重、制定理由 |
| RubricJudgement | 保存满足/不满足及评分依据 |
| StepRewardRecord | 保存步骤、智能体、角色、实际输出、奖励和评审明细 |
| RubricStepRewardAttributor.attribute() | 遍历轨迹，串联生成、评审、计算和结果写回 |
| _generate_rubrics()、_rubric_prompt() | rubric_llm 生成细则；未注入时使用内置通用细则 |
| _judge()、_judge_prompt() | judge_llm 逐条评审；另提供显式启用的启发式预览 |
| _normalize_reward() | 按满足条件的权重占总权重计算步骤奖励 |
| StepRewardCreditAssigner.credits() | 将步骤奖励按智能体平均或求和，类本身提供外部奖励缩放选项 |
| trajectory.meta["rubric_step_reward"] | 写入 steps、criteria_rows、agent_rewards |

他完成了两项注册：

- attributor/rubric_step_reward。
- credit_assigner/step_reward。

这使原项目从只有相关接口和占位组件，发展到拥有可调用的步骤评分初版。
方法实现位于 methods，模型经回调注入，保持零重依赖注册。这些设计在后续版本中延续。

### 4. 初版的边界与后续发现的问题

以下是对初版状态的说明，不是当前版本仍然存在的问题。

- 细则生成器接收本步实际输出，存在根据答案反向制定标准的问题。
- satisfied 使用 bool() 转换，字符串 false 会被误认为满足。
- 细则和评审用 zip() 按位置配对，评审乱序时可能配错权重。
- 每条 Message 被视为一步，role=tool 的工具返回可能被误识别为调用。
- 任务描述缺失时可回退到 task_id；角色可能回退到 assistant；历史使用全部此前消息。
- 长文本被截断，部分评分依据可能丢失而调用者不知情。
- analyze_run 不向 assigner 透传构造参数，因此配置中的 aggregation 等不一定生效。
- 将 reward 写入 confidence，混淆了输出质量与评审置信度。
- 有内存结果表，但没有本次新增的完整离线入口、文件导出和人工核查流程。
- 该提交没有新增专项测试；当时的实现说明明确记录未运行测试。

## 第二部分：我修改的部分

### 1. 对比范围与工作目标

本部分对应 `8e796d0 → 当前工作区`，是在协作者初版上的修复和补齐，不是从零重新实现全部功能。

目标是完成用户要求的“自动生成细则 → MAS步骤评审 → 加权奖励 → 结果表 → 人工核查工具”，
保持原项目接口、注册机制和模块分层。由于没有真实模型端点，本轮仅进行离线验证。
不做 GRPO，不把预设回放当成真实模型成果，不编造人工标注结果。

### 2. 我修改的已有文件

| 文件 | 改动及原因 |
|---|---|
| src/lychee_mas/methods/postrun/rubric_step_reward.py | 修复评分正确性、严格校验、历史/输出隔离、汇总参数与置信度语义；保留原注册名和主类 |
| configs/postrun/rubric_step_reward.yaml | 更新严格输入相关参数，增加 history_mode，移除失效或不再支持的兜底选项 |
| src/lychee_mas/methods/postrun/README.md | 重写当前输入契约、模型回调格式、离线使用与人工标注说明 |
| docs/DESIGN.md | 增加方法的实际接入约束、局部奖励语义和离线边界 |
| tests/test_eval_benchmark_extensions.py | 仅将4处路径比较统一为 as_posix()，修复 Windows 下已有测试失败；没有改 benchmark 业务代码 |
| 复现代码说明.txt | 更新旧说明，移除不再正确的调用方式，链接当前文档 |

### 3. 我新增的文件

| 文件 | 职责／关键入口 |
|---|---|
| src/lychee_mas/methods/postrun/rar_trace_adapter.py | load_trajectory() 加载规范化轨迹；prepare_steps() 划分并校验步骤；strict_json() 严格解析 |
| src/lychee_mas/methods/postrun/rar_replay.py | ReplayCallbacks.rubric()/judge() 回放回复；require_complete() 校验回复全部按预期消费 |
| src/lychee_mas/methods/postrun/rar_export.py | export_step_rewards() 导出完整结果和盲标模板；evaluate_annotations() 计算人工核查指标 |
| scripts/run_rar_step_reward.py | 离线实验 main()，读取轨迹/配置/回放，经 analyze_run 评分并导出 |
| scripts/evaluate_rar_annotations.py | 人工标注分析 main()，读取真实填写的CSV并生成对照报告 |
| examples/data/rar_trajectory.json | 两名agent、三步、一次工具调用和不可见私有消息的最小轨迹样例 |
| examples/data/rar_replay.json | 对应的预设细则和乱序评审回复，明确标记 offline_replay |
| docs/plans/rar-reproduction-plan.md | 复现范围、论文改编声明、文件职责、验收和后续真实实验 |
| docs/experiments/rar-step-reward.md | 已执行验证、运行命令、实际 origin/upstream 仓库及PR步骤 |
| docs/rar-changes-from-original.md | 本文：两轮贡献与相对原始基线的合并改动说明 |

本部分共修改6个已有文件、新增10个文件（含本文）。没有新增专项测试文件。
两个 scripts 文件是正式运行/分析入口，不是专项测试脚本。

### 4. 核心修复前后对比

| 项目 | 协作者初版行为 | 当前行为 |
|---|---|---|
| 生成细则的信息 | 包含本步实际输出 | 只包含目标、角色、动作类型、此前可见历史；实际输出只交给评审器 |
| 满足状态 | bool(value) | 必须是真正的JSON布尔值；字符串false、数字0/1和null均报错 |
| 细则与评审匹配 | 按列表位置 | 按criterion_id，允许乱序；未知、重复、缺失ID均报错 |
| 权重 | 允许0，缺少有限性检查 | 必须为有限正数，总权重也必须有限；拒绝NaN/Infinity/负数/字符串/布尔值 |
| 模型回复格式 | 部分宽松提取与回退 | 严格JSON和必要字段，拒绝重复JSON键、空理由、空制定依据 |
| 消息与步骤 | 每条消息视为一步 | 仅assistant发言/调用评分；user/system仅作上下文；tool返回关联但不计步 |
| 工具归属 | 依赖消息sender和简单识别 | 调用sender为发起agent，要求工具名、参数、调用ID，返回对应此前唯一调用 |
| 工作角色 | 可回退到assistant | 由agent_role或agent_roles映射显式提供 |
| 任务目标 | 可回退到task_id | 必须提供真实目标文本，任务编号不能代替目标 |
| 历史 | 全部此前消息 | 默认按visible_message_ids；只有明确全广播场景才能用all_previous |
| 超长输入 | 静默截断 | 正数预算超限即报错；0表示不限，不自动丢弃依据 |
| 默认细则 | 不传生成回调时自动用固定四条 | 默认必须注入生成回调；固定细则仅可显式作为对照，并标记fixed_baseline |
| 缺评审模型 | 可启用启发式评审 | 必须显式注入回调，离线使用可审计回放；不使用启发式伪评分 |
| 汇总参数 | 经统一入口可能失效 | attributor把aggregation写入Attribution.meta，assigner读取，公共入口不改 |
| 外部任务分数 | 可作为缩放选项 | 本方法不乘外部任务分数，仅输出本地步骤细则满足度 |
| 置信度 | confidence=reward | confidence=0为协议占位，meta.confidence_available=false |
| 错误归因标签 | 容易被误读为因果归因 | 明确is_fault只是低于局部阈值，不能据此推断因果责任 |

### 5. 数据结构、输出和人工核查新增能力

保留 StepRubric、RubricJudgement、StepRewardRecord 主体；StepRewardRecord 增加稳定的 message_id、
tool_call_id、visible_message_ids，以便结果追溯。

结果表新增/保留关键字段：trajectory_id、task_id、step、message_id、agent、role、action_type、
criterion_id、评分细则、rationale、weight、normalized_weight、satisfied、reward_value、step_reward、评分依据。

- reward_value：单条细则的归一化得分贡献。
- step_reward：该步所有细则贡献之和。它在每行重复显示，不应再逐行累加。
- agent_rewards：按配置对同一agent的步骤奖励取平均或求和。

运行输出：

| 文件 | 内容 |
|---|---|
| results.json | 完整步骤结果，实际配置、seed、Git SHA/dirty、源码和输入哈希、调用/token/耗时 |
| criteria.csv | 可阅读的“步骤—智能体—评分细则—奖励—依据”结果表 |
| model_calls.json | 回放请求/回复，供检查生成器是否看到不应看到的信息 |
| human_annotations.csv | 含目标、角色、可见历史、输出和细则的盲标空表，不包含模型判断/理由/奖励 |

输出拒绝覆盖已有目录/文件，避免损坏已有结果或人工标注。
CSV展示转义保留可追溯的ID对应关系，JSON保存原始内容。
人工核查计算覆盖率、细则一致率、完整标注步骤的奖励MAE、细则质量和分歧案例；
空标注、重复/未知ID或非法布尔值显式报错。部分标注不冒充完整结果。

### 6. 协作接入需要同步的变化

公共协议没变，但方法私有输入要求变严格，旧调用方必须检查以下内容：

1. 默认同时提供 rubric_llm 与 judge_llm。固定细则对照使用 default_rubrics，不能与 rubric_llm 同时提供。
2. 以下旧参数已删除：heuristic_judge、min_content_chars、use_external_reward，继续传入会报错。
3. trajectory.meta 提供真实task_goal/goal/question；工作角色来自meta.agent_roles或消息meta.agent_role。
4. 默认每个agent动作都提供meta.visible_message_ids；多工具调用先拆成独立Message。
5. 调用消息提供tool_name、tool_call_id、tool_arguments，工具返回提供关联的tool_call_id。
6. 模型细则必须有id、description、weight、rationale；评审必须有criterion_id、satisfied、reason。
7. 两类回调接收JSON结构的提示字符串，依赖旧提示格式的回调需要适配。
8. 同步入口不能在已有事件循环中直接等待异步回调；异步应用可用asyncio.to_thread调用analyze_run。
9. 文件加载器接受规范化的单条轨迹JSON，不会自动识别任意框架的原始日志；调用方负责按契约适配。

### 7. 原项目接口与功能保持情况

本次两轮改动没有修改以下基础接口/实现：

- plugins/postrun/base.py 中 analyze_run、optimize_postrun、train_from_runs。
- core/types.py 中 Message、Trajectory 等公共类型。
- methods/postrun/base.py 中 Attribution、FailureAttributor、CreditAssigner 协议。
- core/registry.py 注册表实现。
- 已有模型后端、其他论文算法和训练流程。

协作者新增的注册名称不变；methods/postrun/__init__.py 的注册导入由协作者完成，本轮未再次修改。
算法仍在methods层，模型通过回调注入，导入框架不会加载重依赖。

### 8. 验证、环境与当前限制

已执行的结果（历史验证记录，不表示本文生成时又运行了一遍）：

| 检查 | 结果 |
|---|---|
| 现有pytest | 184 passed、3 skipped |
| 依赖跳过 | 两个AgentInit模块缺numpy；C2C模块缺torch |
| 临时离线断言 | 共72项通过，未保存为专项测试脚本 |
| ruff：src和两个新增入口 | 通过 |
| 零重依赖检查 | HEAVY LOADED: NONE |
| 原有五接缝demo | 通过，最终答案4 |
| 离线步骤奖励样例 | 3步、6条细则，奖励1.0/1.0/0.7；planner均值1.0，solver均值0.85 |

本地另创建了.venv，安装pytest、ruff、langgraph、pyyaml及依赖；实验输出位于runs/rar/。
这些目录已被原项目.gitignore忽略，本次没有修改.gitignore、pyproject.toml或VS Code自动激活设置。
虚拟环境、实验产物和临时合成标注不应提交；用于计算校验的临时标注已删除。

限制：没有进行真实LLM测试或真实人工标注研究，没有GRPO训练。
离线回放证明输入契约、流程和算术正确，不证明自动生成质量、真实评分合理性或论文数值复现。

### 9. 两部分合并后的总览与建议审查顺序

相对9189cc2，合并两轮改动并包含本文后，共涉及17个不同文件：新增13个、修改4个。
两轮都修改过的文件只计一次，不能把两个部分的文件数直接相加。

原始已有文件的4处修改是：

- docs/DESIGN.md。
- src/lychee_mas/methods/postrun/README.md。
- src/lychee_mas/methods/postrun/__init__.py（协作者修改）。
- tests/test_eval_benchmark_extensions.py（本轮跨平台修正）。

其余13个文件均相对原始基线新增，其中包括协作者最初新增、后续被我继续修改的评分器、配置和说明。
这意味着：评分器不是我从原项目中独立新建的，而是协作者先完成初版，我再修复和扩展。

建议按以下顺序review：

1. 两人先确认README里的任务目标、角色、步骤和可见历史契约。
2. 协作者审查rubric_step_reward.py的核心修复和旧参数迁移。
3. 负责轨迹采集的人审查rar_trace_adapter.py，确保真实日志能提供所需信息。
4. 负责实验的人运行离线入口，检查结果表和人工盲标模板。
5. 按docs/experiments/rar-step-reward.md提交到自己的fork，再向协作者postrun_stepreward分支创建PR。

本文创建时未自动commit、push或创建PR；原提交8e796d0保留，不改写协作者历史。

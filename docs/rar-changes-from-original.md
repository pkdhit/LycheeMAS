# RaR 步骤奖励：相对最原始项目的合并改动说明

> 用途：向协作者交接、审查代码、准备 PR，区分两轮贡献，避免把协作者已有成果重复算作后续新增。
>
> 仓库：D:/LycheeMAS-collab；分支：postrun_stepreward。
> 原始基线：9189cc2（本次 RaR 开发开始前的 LycheeMASv0.3 主线）。
> 协作者版本：8e796d0，作者 pkdhit，提交说明“新增 step reward 的 rubric 评分实现”。
> 我的版本：在 8e796d0 上继续修改的当前工作区，由本人提出需求并借助 Codex 完成；初轮成果已提交为15963d1；后续API接入与AFTraj适配尚未提交，见第二部分第10节起。
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
保持原项目接口、注册机制和模块分层。初轮因没有真实模型端点，仅进行离线验证；后续接入与验证边界见第10节起。
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

第2—9节为初轮历史记录：修改6个已有文件、新增10个文件（含本文），不含后续增量。没有新增专项测试文件。
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

截至初轮离线实现，以下基础接口/实现未修改；后续模型后端变化见第10节：

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

截至初轮离线实现，相对9189cc2共涉及17个不同文件：新增13个、修改4个。
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

本文初次创建时未自动commit、push或创建PR，之后用户已提交15963d1；原提交8e796d0保留，不改写协作者历史。

### 10. 后续更新：真实API接入与AFTraj轨迹适配（2026-10-13）

本节继续归入“我修改的部分”，不改变协作者初版的贡献归属。
对比起点为15963d1，覆盖此前API排错和本次AFTraj适配；此前API工作不重复算作本次新写。
第2—9节的文件数量和“没有真实LLM测试”等描述是初轮历史状态。
用户此前已报告人工样例上真实模型调用成功；本次AFTraj适配仅做离线验证，未发起付费请求。

#### 10.1 完整文件清单

| 文件 | 相对15963d1 | 内容 |
|---|---|---|
| src/lychee_mas/backends/openai_api_backend.py | 修改 | 前序API排错增加APIGenResult.finish_reason，默认None；generate_chat透传服务商结束原因 |
| scripts/smoke_rar_api.py | 新增 | 前序增加API运行与逐调用诊断；本次增加--input及输入哈希、来源和假设记录 |
| src/lychee_mas/methods/postrun/rar_aftraj_adapter.py | 新增 | convert_aftraj()将原始math记录转换成规范轨迹和独立源标注 |
| scripts/prepare_rar_aftraj.py | 新增 | 本地Parquet读取、选样、校验、转换与来源清单输出 |
| configs/postrun/rar_aftraj.json | 新增 | MathSolver与Verifier的实验职责配置 |
| docs/experiments/rar-aftraj.md | 新增 | 数据引用、假设、运行命令、已知问题与边界 |
| src/lychee_mas/methods/postrun/README.md | 修改 | 累计增加API说明、诊断方法及AFTraj文档入口 |
| docs/rar-changes-from-original.md | 修改 | 本文续写贡献、验证记录和PR步骤 |

本轮交付8个文件，新增5个、修改3个（含本文）。相对最原始9189cc2累计23个不同文件，
新增18个、修改5个；两轮都涉及的文件不重复计数。
评分公式、注册名、plugins接口、core公共类型和其他论文算法没有修改。
适配器是方法私有工具，不需要新注册算法。pyarrow在准备脚本main()内惰性导入。
本轮没有改依赖声明，没有新增持久化专项测试文件。

#### 10.2 API运行和诊断

- 通过现有OpenAICompatibleBackend调用模型，methods层仍通过回调注入模型。
- 使用RAR_API_MODEL、RAR_API_BASE_URL、RAR_API_KEY，不把密钥写入代码或运行配置。
- 每步rubric→judge，max_rubrics=4、aggregation=mean、history_mode=explicit。
- --input指定单条规范化轨迹；不传时继续使用examples/data/rar_trajectory.json。
- --max-output-tokens默认4096且必须为正；--output指定新目录，拒绝覆盖。
- 每次调用保存.request.json、.response.txt、.metadata.json，记录结束原因、token和延迟。
- finish_reason=length明确报截断；其他非stop/None原因也报错；None只是未提供结束原因，仍须校验JSON。
- 非法JSON不自动补全、不伪造评分、不自动重试。失败时保留诊断，完整成功后导出结果表。
- config.json保存模型、Git SHA/dirty、输入路径/SHA256、配置、数据来源与假设。
  完成后的结果元数据还记录调用数、token和延迟汇总。
- 入口读取trajectory.json，不直接读取Parquet或source_annotations.json。

#### 10.3 转换函数、步骤与映射

convert_aftraj(record, *, agent_roles, history_policy)返回两个字典：
规范化轨迹（供load_trajectory加载）和独立源标注。内部使用prepare_steps进行契约校验。

| 原始数据 | 处理 |
|---|---|
| task | 写入task_goal，不用gold_answer构造目标 |
| user/system | 上下文，不评分 |
| agent content | 一条公开发言步骤 |
| agent action列表 | 每个调用独立计步；解析嵌套JSON参数，要求参数是对象 |
| 同轮content和action | 分开发言/调用；均只见此前原始轮次 |
| environment结果列表 | 逐个转tool消息，关联此前唯一调用并校验工具名，不计步 |
| thought | 不进入评分，只统计省略数量 |
| 无公开发言/调用轮次 | 不生成空步骤，omitted_turns记录原轮次及原因 |
| 重复调用、孤立/重复返回、未知角色、非法参数 | 明确报错 |
| 尚无返回的调用 | 记录unmatched_call_ids，不伪造结果 |
| 未知时间戳 | ts=0并明确注明，不伪造真实耗时 |

轨迹/消息ID使用conv_id哈希和序号，task_id使用任务文本哈希；调用ID换成中性顺序ID。
旁路mapping保存message_id、source_turn_index、source_item_index、original_call_id、step。
原轮次从0编号、评分步骤从1编号，不能混用。
源mistake_step按零基解释只是待核实假设；一轮拆多步时输出candidate_mistake_steps，
不擅自认定哪个调用是真正错误，也不将源标签当逐细则人工标注。

#### 10.4 历史、角色与标签隔离

必须显式选择shared-prefix：每步看到此前原始轮次，同轮兄弟步骤互不可见，
未来工具结果不进入当前细则生成上下文。数据未提供真实可见性，这是实验假设。
MathSolver/Verifier职责来自配置，不是恢复的原始system prompt。
这些假设保存在轨迹元数据和运行记录中。

gold_answer、原conv_id、safe/unsafe、mistake_*、unsafe_source只保存在旁路标注或清单，
不附加到模型提示；轨迹内容中原本出现的答案/错误仍原样保留。
标签隔离不等于模型无法根据实际行为判断错误。

#### 10.5 准备入口与输出

在D:/LycheeMAS-collab执行（输出目录必须不存在）：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_rar_aftraj.py --history-policy shared-prefix --output runs/rar/aftraj-new
```

| 参数 | 含义 |
|---|---|
| --source-dir | 默认data/benchmarks/prepared/aftraj，读取所选分组的aftraj_safe.parquet/aftraj_unsafe.parquet |
| --roles | 默认configs/postrun/rar_aftraj.json |
| --history-policy | 必填，目前仅shared-prefix |
| --split | 默认both，可选safe/unsafe |
| --limit | 默认2，正数 |
| --conv-id | 可重复指定，优先于limit；缺失/重复记录报错 |
| --output | 必填的新目录 |

脚本只读本地数据，不下载、不调用模型。需要环境已安装pyarrow。
默认按源顺序交替选择safe/unsafe，不是随机抽样，不保证一般情况下配对。
没有自动应用splits_test.json；正式使用官方划分时应先确认记录并显式选择。
所选记录全部校验成功才开始创建输出；坏记录会导致失败，不自动跳过。

每条输出为aftraj-<摘要>/trajectory.json及source_annotations.json；根目录manifest.json
记录源文件路径/哈希、角色配置、选择策略、历史假设、Git状态、引用许可、步数和预计调用数。

已准备的样例在runs/rar/aftraj-adapted：

| 原记录 | 子目录 | 步数/预计API调用 |
|---|---|---|
| math_0000_safe | aftraj-d1b4a62a8e09a971b470 | 11/22 |
| math_0000_unsafe_injected_static | aftraj-6856aeaddefe019d688b | 9/18 |

设置原有API环境变量后可运行：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_rar_api.py --input runs/rar/aftraj-adapted/aftraj-d1b4a62a8e09a971b470/trajectory.json
```

此命令实际调用模型；每次处理一条轨迹，不是批量调度器。
长历史仍受原评分器预算约束，超限明确报错，不静默截断。
输出沿用results.json、criteria.csv、model_calls.json、human_annotations.csv及原人工核查入口。

### 11. 本次验证与尚未完成的实验

| 检查 | 适配实施时的结果 |
|---|---|
| 全量math转换校验 | safe395/396、unsafe397/397，共792/793 |
| 唯一失败 | math_0153_safe参数重复expression键，严格拒绝，不选择其一覆盖 |
| 两条样例评分导出 | 11/9步通过；40次合成回调，不是40次真实API请求 |
| 历史/标签检查 | 样例不见同轮及未来消息，不附加标准答案字段/错误原因/原unsafe标识 |
| 稳定性/异常 | 确定性ID通过；孤立返回、重复调用、非法参数、未知角色、未声明历史策略均拒绝 |
| 现有pytest | 184 passed、3 skipped |
| ruff | src及API/准备入口通过 |
| 零重依赖 | HEAVY LOADED: NONE，含pyarrow检查 |
| 差异格式 | git diff --check通过 |

本次续写文档未重新运行真实模型。临时断言未保存为专项测试脚本。
全量转换检查成功不等于逐条数学内容正确，也不是793个独立持久化测试。
直接选择全部793条会遇到坏记录而失败，脚本不会自动产出过滤后的792条。
合成回调满分仅验证流程与计算，不能当模型效果或人工标注结果。

目前仅支持AFTraj math事后评分，不是MATH-50重新运行MAS，不做GRPO。
真实AFTraj评分质量、少量人工盲标及一致性分析仍待完成；建议先跑一对，再扩展样本。
unsafe含人工注入错误，不能全部称为自然失败。

数据：https://huggingface.co/datasets/ZBox008003/AFTraj （CC BY 4.0）。
配套项目：https://github.com/ZBox1005/AgentForesight 。
论文：https://arxiv.org/abs/2605.08715 。报告中保留引用与许可，并说明角色和可见性假设。

### 12. 提交与PR操作（本次仅提供方法，未代执行）

已核对：当前分支postrun_stepreward；origin是R1kka794/LycheeMAS，
upstream是pkdhit/LycheeMAS。本地有upstream/postrun_stepreward跟踪记录，最新远程状态需fetch核实。

#### 12.1 查看并按文件暂存

```powershell
Set-Location D:\LycheeMAS-collab
git branch --show-current
git status
git diff --stat
```

应在postrun_stepreward；若不一致，先检查工作区，不盲目带改动切分支。
git diff不包含未跟踪文件，需结合status查看。

```powershell
git add src/lychee_mas/backends/openai_api_backend.py src/lychee_mas/methods/postrun/README.md
git add src/lychee_mas/methods/postrun/rar_aftraj_adapter.py
git add scripts/smoke_rar_api.py scripts/prepare_rar_aftraj.py
git add configs/postrun/rar_aftraj.json docs/experiments/rar-aftraj.md docs/rar-changes-from-original.md
git diff --cached --stat
git diff --cached --check
git diff --cached
```

本轮预期8个文件；之前已暂存其他内容时须逐项核对。不要直接git add .混入未知文件。
不要提交密钥、.env、.venv、Parquet、runs产物或用途未确认的tream。
本次交付同时含此前API入口和本次适配，避免只提交适配器漏掉入口。

#### 12.2 Commit并推送自己的fork

```powershell
git commit -m "feat(postrun): 接入真实API并适配AFTraj步骤评分"
git fetch origin
git push origin postrun_stepreward
git status
git log -1 --oneline
```

推送成功且与origin分支同步表示自己的fork已更新，不表示已合入upstream。
仅剩tream未跟踪不代表提交失败。
如提示缺作者身份，用本人信息设置仓库级配置，再重试commit（不要照抄占位值）：

```powershell
git config user.name "你的GitHub用户名"
git config user.email "你的已验证邮箱或GitHub提供的noreply邮箱"
```

如push因远程新提交被拒绝，先fetch并检查再合并，不force push：

```powershell
git fetch origin
git log --oneline --left-right HEAD...origin/postrun_stepreward
git merge origin/postrun_stepreward
```

冲突需逐处解决、暂存并完成merge commit；运行相关检查后再push。

#### 12.3 在GitHub创建或更新PR

1. 打开https://github.com/pkdhit/LycheeMAS ，进入Pull requests → New pull request。
2. 点击compare across forks。
3. base repository选pkdhit/LycheeMAS，base选postrun_stepreward。
4. head repository选R1kka794/LycheeMAS，compare选postrun_stepreward。
5. 检查Files changed，确认没有数据、密钥和运行产物。
6. 填写下面标题/正文，Create pull request，请协作者review。

直接比较链接：
https://github.com/pkdhit/LycheeMAS/compare/postrun_stepreward...R1kka794:postrun_stepreward?expand=1

这里延续合入协作者功能分支的方案；双方确认回主线时才把base改成LycheeMASv0.3。
如果相同head/base已有开放PR，本次push会自动更新它，修改现有PR说明即可。
若upstream还没有合入15963d1，PR会包含此前离线改动，整个PR不一定只有本轮8个文件。

#### 12.4 可复制的PR标题和正文

标题：

```text
feat(postrun): 接入真实API并支持AFTraj数学轨迹步骤评分
```

正文（第一段仅在PR包含15963d1时保留）：

```markdown
## 改动

包含此前RaR严格输入校验、离线回放、结果导出与人工核查入口。

增加真实API调用及诊断入口；将AFTraj math原始记录适配为现有步骤评分输入。
批量工具调用拆成独立步骤，工具结果关联但不计步，源标签与标准答案分开保存。
API入口新增--input，记录原始回复、结束原因、输入哈希及实验假设。

公共plugins/core接口和评分注册名保持不变，后端增加可选finish_reason返回字段。
完整贡献分工见docs/rar-changes-from-original.md。

## 验证

- 现有测试184通过、3跳过；ruff与零重依赖检查通过。
- 793条math记录中792条转换通过，一条重复JSON键记录明确拒绝。
- 两条样例11/9步，离线合成回调评分和导出通过。
- 检查稳定ID、历史隔离与多类非法输入拒绝行为。

## 限制

共享历史与角色职责为显式实验假设；mistake_step零基解释待核实。
本次AFTraj适配未调用真实模型，尚未做真实人工标注，不做GRPO。
数据、密钥及运行产物不随PR提交。
```

review后继续在同一分支修改、commit、push，PR会自动更新。
PR正文应描述当前实际未合入的差异，本文则保留完整累计贡献。

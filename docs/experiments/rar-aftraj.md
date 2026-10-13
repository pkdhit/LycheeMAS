# AFTraj 数学轨迹接入 RaR

本适配读取本地 AFTraj 原始 Parquet，转换为现有 RaR Trajectory JSON，再由原有
rubric_step_reward / step_reward 评分。没有修改 plugins 接口，没有训练或下载行为。
目前仅支持 math 域；这不是 MATH-50 的新 MAS 运行，而是已有 MAS 轨迹的事后评分。

## 数据与实验假设

- 来源：https://huggingface.co/datasets/ZBox008003/AFTraj
- 配套项目：https://github.com/ZBox1005/AgentForesight
- 论文：AgentForesight，https://arxiv.org/abs/2605.08715
- 数据许可：CC BY 4.0。发表实验时保留数据集与论文引用。
- 必须显式选择 shared-prefix：每步可见此前原始轮次，同一轮拆分出的兄弟步骤彼此不可见。
  数据未提供真实可见性，这是一项实验假设。
- configs/postrun/rar_aftraj.json 中职责是实验配置，不是原始 system prompt。
- 一次公开发言或工具调用算一步；批量调用逐项拆分；工具结果只作历史证据。
  content 与 action 同时存在时各自拆分，均只看此前轮次。
- thought 不进入评分；没有公开动作的轮次在旁路记录中说明省略原因。
- 保留工具参数和结果；原调用 ID 换成中性 ID，原 ID 保存在旁路映射。
- gold_answer、safe/unsafe、mistake_*、原 conv_id 只在 source_annotations.json/manifest.json，
  不附加到模型输入。实际轨迹中出现的答案原样保留。
- mistake_step 的零基索引只是待核实假设；一轮多调用映射到多个候选步骤，
  不能直接视为多个真实错误标签。
- unsafe 包含人工注入错误，不可将全部 unsafe 描述成自然发生的失败。
- 未知时间戳填 0 并注明；没有伪造耗时或源模型 token 数。
- 格式歧义明确报错。目前 793 条 math 记录中 792 条通过转换；
  math_0153_safe 的工具参数存在重复 expression 键，严格拒绝，不自动选择其一。

## 操作（仓库根目录 PowerShell）

先准备默认两条记录（输出目录必须不存在）：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_rar_aftraj.py --history-policy shared-prefix --output runs/rar/aftraj-new
```

默认按文件原顺序交替选择 safe、unsafe，limit=2；不是随机抽样，也不保证一般情况下配对。
可用 --conv-id 重复指定记录，此时优先于 limit；可用 --split safe 或 unsafe。
--source-dir 指向本地含 aftraj_safe.parquet / aftraj_unsafe.parquet 的目录。
Parquet 读取需要 pyarrow，已安装项目 benchmark 数据依赖的环境通常具备。

manifest.json 给出每份 trajectory.json 路径、步数、预计模型调用数、源文件 SHA256、
角色配置和 Git 状态。source_annotations.json 只供后续人工检查/评测，不传给评分脚本。

已在本仓库生成 runs/rar/aftraj-adapted：
- aftraj-d1b4a62a8e09a971b470/trajectory.json：math_0000_safe，11 步，22 次评审调用。
- aftraj-6856aeaddefe019d688b/trajectory.json：math_0000_unsafe_injected_static，9 步，18 次调用。

设置好原有 RAR_API_MODEL、RAR_API_BASE_URL、RAR_API_KEY 后，先评分一条：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_rar_api.py --input runs/rar/aftraj-adapted/aftraj-d1b4a62a8e09a971b470/trajectory.json
```

该命令会产生付费请求。适配验证过程中没有执行真实 API。
评分器默认历史长度预算仍生效，长轨迹超限会明确报错，不会静默截断。

输出仍是运行目录中的 results/results.json、criteria.csv、human_annotations.csv 等。
先查看各步骤的细则与理由，再对少量步骤盲标；不要把源失败标签当作逐细则人工标注。
步骤奖励是局部满足度，不等于任务正确率或因果归因。

## 文件职责

- methods/postrun/rar_aftraj_adapter.py：纯转换、调用关联、历史构造、标签隔离。
- scripts/prepare_rar_aftraj.py：本地读数据、选样、输出、来源追踪。
- configs/postrun/rar_aftraj.json：角色职责。
- scripts/smoke_rar_api.py：新增 --input，保留原样例默认入口，记录输入哈希和假设。

离线检查只能验证契约与流程；真实评审质量需要模型实验与人工标注验证。

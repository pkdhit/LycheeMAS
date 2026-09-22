# RaR → MAS 步骤级奖励：范围与验收

## 目标

按用户最终要求实现自动细则生成与步骤奖励，不进行 GRPO、训练或图更新。
本轮没有服务器模型接口，只验证离线回调、输入契约、计算和结果处理。
按用户要求不新增专项测试脚本，使用现有测试和临时内存断言。
真实评分合理性与独立人工标注结论仍需后续实验，不能用回放代替。

## 论文依据与差异

论文：[Rubrics as Rewards: Reinforcement Learning Beyond Verifiable Domains](https://arxiv.org/abs/2507.17746)。
按论文思想独立编写，未复制外部仓库代码/提示词，不新增外部许可证资产。
保留协作者提交 8e796d0，以后续修改修复，不改写原历史。

原论文完整响应奖励改为 MAS 发言/工具调用级奖励，每步细则仅根据目标、角色、
此前可见历史制定。采用有限正权重和正向满足语义，不搬用负权重陷阱条目。
没有固定论文数据集、真实评审模型或 GRPO，不声称达到原论文指标。

## 接入

1. core.Trajectory / Message 输入，私有适配器验证稳定 ID、目标、工作角色、调用和历史。
2. 注入 rubric_llm；生成提示不含本步实际输出或未来信息；缺失回调默认报错。
3. 注入 judge_llm；要求完整唯一的 criterion_id、严格 bool、非空评分理由。
4. sum(weight * satisfied) / sum(weight)，按 ID 对齐，非法权重/JSON 显式报错。
5. 输出 Attribution.meta、trajectory.meta、CSV、完整 JSON、请求回复审计记录。
6. 人工核查：盲标 CSV、覆盖率、逐条一致率、完整步骤奖励 MAE、细则质量和分歧案例。
7. 可复现：配置、seed、Git SHA/dirty 状态、源码与输入哈希、调用/token/耗时。

算法仅位于 methods，实现保持公共 core/plugins 协议不变，模型 SDK 不进入算法层。
默认 mean，sum 经 Attribution.meta 传给 assigner；不乘外部任务得分。
is_fault 仅为局部阈值标签，confidence 协议字段不冒充真实评审置信度。

## 交付文件

- methods/postrun/rubric_step_reward.py：修正评分本体。
- methods/postrun/rar_trace_adapter.py：输入适配与校验。
- methods/postrun/rar_replay.py：严格回放回调。
- methods/postrun/rar_export.py：导出和人工抽查计算。
- scripts/run_rar_step_reward.py：离线实验入口。
- scripts/evaluate_rar_annotations.py：人工抽查分析入口。
- examples/data/rar_trajectory.json、rar_replay.json：小型人工构造样例，不是模型成果。
- configs/postrun/rubric_step_reward.yaml：参数。
- methods/postrun/README.md、docs/DESIGN.md：契约与组件状态。

以上 methods 路径均相对 src/lychee_mas；其他路径相对仓库根目录。

## 验收

- 生成器不见当前输出；不可见或未来消息不会自动进入历史。
- 工具调用归于发起 agent，返回不额外计步；多调用先拆分。
- 缺目标/角色、非法历史、重复 ID、非法布尔/权重、超限和模型格式错误显式失败。
- 乱序评审正确计分；mean/sum 经 analyze_run 生效；不依赖默认固定细则或启发式 judge。
- 回放产生三步 [1,1,0.7]、六条细则，真实模型调用为0；盲标模板不泄漏模型标签。
- 空标注不生成评分；部分标注报告覆盖率，只计算完整步骤的奖励差异。
- 执行现有 pytest、ruff、零重依赖检查和五接缝 demo，结果见实验文档。

## 后续实验

真实模型可用后，经 backends 注入回调，记录真实 token/延迟/模型版本。
收集真实 MAS 轨迹，核实可见历史来源，再进行20～30步起步的独立人工抽查。
按样本规模报告限制，不能以回放通过推导真实评分质量。

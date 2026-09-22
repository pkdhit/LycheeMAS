# RaR 步骤奖励离线验证与提交指南

## 本轮结果（2026-09-22）

分支 postrun_stepreward，基于协作者原提交 8e796d0；保留原贡献历史。
没有真实模型端点，没有进行真实 LLM 调用或 GRPO，也没有伪造人工标注结果。
本轮按用户要求不新增专项测试脚本，验证用现有测试、临时内存断言和正式离线实验入口。

| 检查 | 结果 |
|---|---|
| 现有 pytest | 184 passed，3 skipped |
| 跳过原因 | AgentInit 两个测试模块缺 numpy（及其可选生态）；C2C 模块缺 torch |
| ruff check src + 两个新增入口 | 通过 |
| 零重依赖自检 | HEAVY LOADED: NONE |
| 原有五接缝 demo | 通过，最终答案4 |
| 临时步骤奖励断言 | 72 项通过，不保存为专项测试脚本 |
| git diff --check | 通过 |
| 离线回放 | 3 步、6 条细则，奖励1.0/1.0/0.7，planner均值1.0、solver均值0.85 |

临时断言覆盖：答案隔离、私有/未来历史隔离、工具归属、乱序ID、非法bool/权重/JSON、
目标/角色缺失、超限报错、mean/sum透传、异步回调、固定细则标签、导出不覆盖、
盲标模板、空标注报错、部分覆盖率、完整步骤MAE和分歧计算；另验证CSV标识转义往返与人工核查CLI。
人工核查计算使用的临时合成标签仅用于算术校验，已删除，不作为人工实验成果。

最初现有测试在 Windows 上把反斜杠路径直接与 POSIX 字符串比较导致失败。
仅将该测试的4处路径比较改为 as_posix()，没有修改 benchmark 业务实现或新增测试文件。

本地 .venv 安装 pytest、ruff、langgraph、pyyaml，仅用于离线开发和检查；被 gitignore 忽略。
不安装或调用大型模型。模型SDK不进入方法层，公共 plugins/core 接口未改。

## 重新运行（PowerShell，在仓库根目录）

已有 .venv 可直接执行；其他机器可创建隔离环境并安装相同开发依赖。

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider
.\.venv\Scripts\ruff.exe check src scripts/run_rar_step_reward.py scripts/evaluate_rar_annotations.py --no-cache
.\.venv\Scripts\python.exe examples/01_five_seams_demo.py
.\.venv\Scripts\python.exe -c "import sys,lychee_mas; print('HEAVY LOADED:', [m for m in ('torch','transformers','autogen_core','autogen_agentchat','langgraph','langchain_core','numpy','yaml','sympy','datasets') if m in sys.modules] or 'NONE')"
.\.venv\Scripts\python.exe scripts/run_rar_step_reward.py `
  --input examples/data/rar_trajectory.json `
  --replay examples/data/rar_replay.json `
  --config configs/postrun/rubric_step_reward.yaml `
  --output runs/rar/my-offline-run
```

输出目录必须不存在，重跑请更换目录名。
结果与配置、Git SHA/dirty、源码/输入哈希均在 results.json；CSV为结果表；
human_annotations.csv 为盲标空表；model_calls.json 为回放审计记录。
完整契约和人工核查命令见 src/lychee_mas/methods/postrun/README.md。
回放只证明流程和计算符合输入契约，不证明真实细则质量或真实评分正确性。

## 本仓库的 remote 与 PR 目标

当前 origin 为 https://github.com/R1kka794/LycheeMAS.git 。
当前 upstream 为 https://github.com/pkdhit/LycheeMAS.git （协作者仓库）。
本次不自动 commit、push、建PR或合并，由用户检查后执行。

先检查只包含预期改动，别加入 .venv、runs 或密钥：

```powershell
git branch --show-current
git status --short
git diff --check
git diff
```

应为 postrun_stepreward。将 Windows 测试兼容修正与评分功能分成两个提交，便于 review：

```powershell
git add tests/test_eval_benchmark_extensions.py
git commit -m "test(eval): 修正 Windows 路径断言的跨平台比较"
git add src/lychee_mas/methods/postrun/rubric_step_reward.py src/lychee_mas/methods/postrun/rar_trace_adapter.py src/lychee_mas/methods/postrun/rar_replay.py src/lychee_mas/methods/postrun/rar_export.py src/lychee_mas/methods/postrun/README.md
git add scripts/run_rar_step_reward.py scripts/evaluate_rar_annotations.py configs/postrun/rubric_step_reward.yaml examples/data/rar_trajectory.json examples/data/rar_replay.json
git add docs/DESIGN.md docs/plans/rar-reproduction-plan.md docs/experiments/rar-step-reward.md "复现代码说明.txt"
git diff --cached --check
git diff --cached --stat
git commit -m "fix(postrun): 完善 RaR 步骤奖励及离线验证流程"
```

提交后同步协作者分支与主线。共享分支使用 merge 保留已有历史，不使用 force-push：

```powershell
git fetch upstream
git fetch origin
git merge --no-edit upstream/LycheeMASv0.3
git merge --no-edit upstream/postrun_stepreward
git merge --no-edit origin/postrun_stepreward
```

任一命令有冲突立即停下，解决冲突并完成 merge 后再执行后续命令。
同步改变代码后重新运行上述离线检查。确认通过后：

```powershell
git push origin postrun_stepreward
```

在 GitHub 创建 PR：

- base repository：pkdhit/LycheeMAS（upstream）
- base branch：postrun_stepreward（先合入协作者功能分支）
- head repository：R1kka794/LycheeMAS（origin）
- compare branch：postrun_stepreward

PR 写明本次缺真实模型端点，仅离线验证；附184通过/3依赖跳过、72临时断言、lint/selfcheck/demo结果，
并说明未新增专项测试脚本是用户要求、Windows现有测试修正、论文改编边界。
协作者审查合入后，再由负责人决定功能分支何时进入 LycheeMASv0.3 主线。
不要直接推送主线，不对共享分支强制推送。

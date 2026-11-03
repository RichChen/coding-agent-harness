# TP01–TP04 首批实现记录

日期：2026-09-19。授权：用户“你直接实施”。本批完成四包的离线策略/机制实现及组合验证；不是整个改造路线完成，也不是 R1 的真实模型公开 issue 修复演示完成。费用、跨仓真实运行和执行安全能力仍单列。

## 已实现

| 包 | 实现与证据 | 当前限制 |
|---|---|---|
| TP01 | `protocol/public.py` 严格公共 DTO/版本投影；独立 `protocol/evaluation.py`；`eval/public_export.py` 审查过的 allowlist/hash 导出；sentinel、私有目录/路径别名/不干扰测试 | 公共文件的语义公开性需可信数据准备器审查；不提供 OS 沙箱；实际 symlink 创建测试本机跳过 |
| TP02 | `evidence/models.py, locator.py, context.py, strategy.py`；无 tags/expected_files 的公开词法定位、read/search proposals、序列化证据状态、公开测试可读不可写 | 仅关键词/公开观测 baseline；不自动执行 shell/search/模型；宿主提供真实 observations |
| TP03 | `memory/records.py, record_store.py, source_manifest.py, migration.py, builder.py`；版本与来源、事务提交/故障回滚、来源 split/time/hash gate、legacy 只读迁移、公开轨迹规则抽取 | 无历史来源的数据保留 candidate/unknown 或隔离；不会补造 provenance；Secret 筛查仅基础模式，非完整 DLP |
| TP04 | `memory/validity.py, dependency_index.py, gate.py`；valid/invalid/unknown、版本漂移/冲突/重复/配额拒绝、实际注入收据校验和采用 unknown | RepoFacts/API 签名由可信宿主提取器提供；范围规则保守；无真实 harness receipt/async plugin 联调 |

旧 TaskSpec/GraphRunner/MemoryHint/Skill 检索与真实模型调用路径继续保留。只给新产生的旧实验 trace/metrics/summary/report 加 `diagnostic_oracle` 标记和代理指标说明，没有覆盖历史报告，也没有把旧成绩重新包装成新成绩。新增公共路径不调用旧标签 router、scripted_fixes 或 direct ModelClient。

额外装配工作仅为让四包可直接演示：`plan_public_step` 把公开 evidence 与可选 memory context 组合；`skills/public_router.py` 提供不读 tags/expected_files 的 realistic Skill 路由；`PublicStrategyState` 提供版本化纯 checkpoint；`eval/public_runner.py` 用 fixture observations 走多轮离线 walkthrough；`upgrade-demo` CLI 和独立脚本提供离线入口；规则 builder 处理明确允许的结构化轨迹。没有因此新增 runtime/容器/通用预算系统。

## 运行入口

在仓库根目录，使用已有环境，无需 API key。output 必须是新的不存在目录（拒绝覆盖）。下面命令里的解释器路径是当时的本机环境，换成任意 Python 3.11+ 环境即可；`reports/` 下的产物不入库，可以重新生成。

```powershell
& 'C:\Users\Administrator\.conda\envs\coding-harness\python.exe' -m reposkill_agent upgrade-demo --output reports/upgrade/my-new-demo
# 或
& 'C:\Users\Administrator\.conda\envs\coding-harness\python.exe' scripts/demo_memory_validity.py --output reports/upgrade/my-new-demo-script
```

两条是已存在的等价入口。实际运行记录用 CLI 写入 `reports/upgrade/offline-20260919-v2/`；独立脚本的 `--help` 已执行。demo 输入是人工构造的公开 issue/小仓库与历史公共轨迹，不是新收集的真实 GitHub issue。private-preparation 目录只用于模拟数据准备；不得把整个输出目录作为 agent workspace 挂载，应只暴露 public-workspace 和获准的 artifact 视图。

已生成可打开产物：

- 离线报告（本地生成，未入库：`reports/upgrade/offline-20260919-v2/report.md`）
- 完整结果（本地生成，未入库：`reports/upgrade/offline-20260919-v2/result.json`）：locator/context、记录 snapshot、实际 artifact refs、fixture receipt 与用途记录
- 运行 manifest（本地生成，未入库：`reports/upgrade/offline-20260919-v2/manifest.json`） 与 来源 policy（本地生成，未入库：`reports/upgrade/offline-20260919-v2/source-policy.json`）
- `artifacts/trajectory.txt`、`artifacts/verification.txt`、`artifacts/read-parser.txt`：源码测试已检查引用 hash 对应实际字节
- `memory.db`：candidate→validated 的版本记录、transition、fixture use 记录

## 漂移结果：只作为机制验收

| 情景 | 依赖检查 | 是否选择 | 整库清空 baseline 的差别 |
|---|---|---|---|
| 未改变 | valid | 是 | 同样保留 |
| API 签名改变 | invalid | 否 | 清空全部 |
| commit 相同但 package 不兼容 | invalid | 否 | 检测环境变化后清空 |
| 无关文件/commit 改变，所需依赖未变 | valid | 是 | 整库清空丢弃有效经验 |
| 缺少必要依赖证据 | unknown | 否 | 不将“没有观测”伪装成 valid |

四策略×五情景共 20 行。无模型调用，repair_success=null；fixture_host_injected 仅模拟宿主执行选择，actual_model_injection=false。静态/清空组与依赖组使用同一条初始记忆和变更集。率的分母分别为可评价 stale/valid 候选，零分母为 null；unknown 不算 stale 标签。真实模型是否受益尚未实验。

## 测试与已核实命令

解释器 Python 3.11.15，既有 pytest 8.4.2 / Ruff 0.15.20 环境；没有新建/改装该环境。新增 PEP 440 检查所需 `packaging>=23.0` 已声明为项目依赖，当前环境已有可用版本。

所有测试执行前设置：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PATH='C:\Users\Administrator\.conda\envs\coding-harness;C:\Users\Administrator\.conda\envs\coding-harness\Scripts;' + $env:PATH
```

扩大回归命令（实测 133 passed, 1 skipped，77.44 秒）：

```powershell
& 'C:\Users\Administrator\.conda\envs\coding-harness\python.exe' -m pytest tests --ignore=tests/test_cli.py --ignore=tests/test_llm_ablation.py --ignore=tests/test_external_baselines.py --ignore=tests/test_phase7_benchmark.py -q -o addopts='' -p no:cacheprovider --basetemp=traces/upgrade_20260919_regression_01 --junitxml=reports/upgrade/verification-20260919/regression.xml
```

最后的小幅边界补充后，运行新功能+相关 legacy 定向组（71 passed, 1 skipped，40.42 秒）：

```powershell
& 'C:\Users\Administrator\.conda\envs\coding-harness\python.exe' -m pytest tests/test_public_boundary.py tests/test_evidence_locator.py tests/test_memory_records.py tests/test_memory_record_store.py tests/test_memory_sources.py tests/test_memory_validity.py tests/test_memory_gate.py tests/test_upgrade_integration.py tests/test_ablation.py tests/test_llm_ablation.py::test_llm_ablation_mock_runs tests/test_cli.py::test_cli_help tests/test_cli.py::test_list_tasks -q -o addopts='' -p no:cacheprovider --basetemp=traces/upgrade_20260919_final_01 --junitxml=reports/upgrade/verification-20260919/final.xml
```

补齐 demo artifact 落盘后再测该集成模块（8 passed，3.53 秒）：

```powershell
& 'C:\Users\Administrator\.conda\envs\coding-harness\python.exe' -m pytest tests/test_upgrade_integration.py -q -o addopts='' -p no:cacheprovider --basetemp=traces/upgrade_20260919_artifacts_01 --junitxml=reports/upgrade/verification-20260919/artifacts.xml
& 'C:\Users\Administrator\.conda\envs\coding-harness\python.exe' -m ruff check reposkill_agent tests scripts --no-cache
```

Ruff 最终通过。上述是实际命令记录，重复运行时请换新的 basetemp/XML 位置，pytest 会清空它自己指定的 basetemp；不要复用本次证据目录。三个测试集有重叠，不能相加宣称独立总测试数量。

未运行不加筛选的完整 suite，避免旧 CLI 测试写默认历史 summary 和外部 baseline CLI 潜在调用；旧 CLI help/list 和 LLM mock runner 已单独验证。跳过的 1 项是真实 symlink 创建受 Windows 权限限制，另有无需该权限的 ancestor-link guard 测试通过；不把模拟测试当 OS 隔离验证。未跑付费实验/真实 OpenHands/容器/coding-harness 联调。

本轮新增 pre-harness 合同测试：`tests/test_public_pre_harness.py`，11 项通过；覆盖无标签路由的 sentinel 非干扰、零技能、checkpoint 绑定/恢复、fixture 多轮 runner、轨迹失败信息保留、source split/time/duplicate/diagnostic 拒绝、oracle 字段拒绝和 realistic 模块 legacy import audit。此前未设置环境 PATH 的一次全量尝试中，旧 diagnostic 测试有 9 项因外部 `python` Windows 启动别名失败；按项目记录补齐 PATH 后的回归已通过，未改旧 runner 迎合环境问题。

本轮 pre-harness 收尾回归（按项目记录设置 Conda 环境 PATH）为 **147 passed, 1 skipped，85.05 秒**，JUnit：pre-harness regression（本地生成，未入库：`reports/upgrade/verification-20260919/pre_harness_regression.xml`）；新增合同测试单独为 **11 passed，0.40 秒**，JUnit：pre-harness contract（本地生成，未入库：`reports/upgrade/verification-20260919/pre_harness_contract.xml`）。

JUnit 原始证据：回归（本地生成，未入库：`reports/upgrade/verification-20260919/regression.xml`）、定向（本地生成，未入库：`reports/upgrade/verification-20260919/final.xml`）、artifacts（本地生成，未入库：`reports/upgrade/verification-20260919/artifacts.xml`）。

## 版本、快照与 review

Git 仍不可用，没有 init/commit/push。实施前重新检查旧 manifest 102 文件，全部未变；建立限定范围可恢复 source.zip（本地生成，未入库：`baseline/upgrade-20260919-tp01-04/source.zip`），SHA256：`31e7c6fcbd7ed5ac102478e2a49ca56db7157d2652d264cb2d178b3a809be9a7`。排除了 `.env`、`.git`、全部报告/数据/PDF/工作区；只存原 allowlist 文件。`scripts/upgrade_snapshot.py` 提供 capture/compare，capture 检查前置 hash，不会静默捕获改过的基线。

统一 diff（本地生成，未入库：`reports/upgrade/review-20260919/changes.diff`） 与 变更 hash manifest（本地生成，未入库：`reports/upgrade/review-20260919/manifest.json`） 覆盖 38 个源码/测试/脚本/配置/README 等变更文件（新增文件也计入），文档新增另见本目录。新报告与 baseline 在 gitignore 中忽略，未来提交应选取脱敏的小型证据，不全量收录运行目录。

[API 说明](PUBLIC-API.md) 与 [JSON schemas](schemas/PublicTask.schema.json) 已落盘；共导出 PublicTask/EvaluationSpec/MemoryRecord/SourcePolicy/EvidenceState/GateDecision/InjectionReceipt 七种 schema。它们来自实际 Pydantic 类，尚未作为两仓协商后的公共版本发布。

## 后续阶段

下一工程阶段仍是 A4 的 harness 接入：由 coding-harness 执行 proposals、模型/工具、预算、事件、receipt、取消和恢复；本仓已提供可消费的 public router、纯 state DTO 和 fixture runner，但没有实现跨仓 plugin 或第二个 runtime。接入完成后才做一次受限预算真实修复，未向其他任务发消息或改对方仓库。

A5 付费 pilot 前仍须固定真实数据来源/split、模型价目和总费用上限。当前用户授权允许继续常规实现，但不等于授权无限模型消费。

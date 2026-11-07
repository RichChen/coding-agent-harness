> 这个目录是 monorepo 的一部分，原来是独立的 `coding-agent` 仓库，历史保留。
> 总览、结果和阅读入口见[根目录 README](../README.zh-CN.md)。

# RepoSkillAgent

公开证据驱动的 Coding Agent 策略与可追溯记忆项目。新路径提供公开任务隔离、证据定位、版本化记忆与依赖有效性检查，已接入 coding-harness 并有一个合成任务的真实开／关联调证据。真实多任务实验（A5-main、A6-R）由 harness 执行，记忆卡、预注册和分析脚本在本仓库；两次都没有检出记忆或上次尝试记录带来的收益，见下文。

- 新路径：PublicTask → EvidenceState → MemoryRecord/validity → context proposals。harness 负责唯一执行 runtime 和模型预算；单任务联调不能证明记忆收益或未知 issue 泛化。
- 旧路径：保留 PlanAct、角色分工、Skill/Memory 与 L0/L2/L5。旧实验使用 fake 本地执行 adapter + 真实 DeepSeek，不是纯 mock，也不是 OpenHands SDK 实际运行证据。
- OpenHands：提供可选 adapter，真实集成状态未验证。旧报告完整保留，新输出标为 `diagnostic_oracle`，不与新 realistic 数据混算。

## 新离线演示（无需 API key）

```powershell
# 使用项目 Python 3.11+ 环境；output 必须为不存在的新目录
python -m reposkill_agent upgrade-demo --output reports/upgrade/my-first-demo
# 等价独立脚本
python scripts/demo_memory_validity.py --output reports/upgrade/my-second-demo
```

演示生成公开 workspace、来源/状态可查的 memory.db、manifest、result.json 和 report.md。API/依赖改变时拒绝旧经验，无关文件改变仍保留有效条目，缺证据返回 unknown。所有“注入”收据来自 fixture host；没有真实模型调用、修复成功率或 OS 隔离声明。

解释器用任意 Python 3.11+ 环境（上面命令里的 `python`）。详见 [首批实现与运行说明](docs/upgrade/IMPLEMENTED-TP01-04.md) 与 [公开接口](docs/upgrade/PUBLIC-API.md)。当时的实施计划和任务包在 [docs/archive/](docs/INDEX.md)。

H4/H5：`RepoSkillH4Plugin` 提交 async JSON proposals 和可序列化状态，不直连模型。公私分离的单任务交接包已由 harness `real-04` 完成 off/on 联调，各 4 次模型调用、独立 grader 各 4/4；这是另一仓保存的既有证据，本轮没有重跑。字段映射与历史边界见 [H4 适配说明](docs/upgrade/H4-ADAPTER.md)，最新证据区分见 [A5 验收记录](docs/upgrade/A5-VERIFICATION.md)。

## A5-main（2026-09）

在 56 道 SWE-bench Verified 题上比较 B0（不注入记忆）和 B2（注入同仓库、更早、已验证修复的卡片）。B0 解出率 68.5%，B2 相对 B0 的平均配对差是 −1.8 个百分点（95% 区间 −8.3 到 4.8，p = 0.72），没有检出正效应。

- 预注册结果：[A5-MAIN-RESULTS.zh-CN.md](docs/sprint/A5-MAIN-RESULTS.zh-CN.md)
- 最终报告：[A5-FINAL-REPORT.zh-CN.md](docs/A5-FINAL-REPORT.zh-CN.md)
- 历程指针：[PROJECT-HISTORY.zh-CN.md](docs/PROJECT-HISTORY.zh-CN.md)

## A6-R（2026-10）

对 A5 B0 里失败的 53 次运行（22 道题）各做第二次尝试：R0 原样重跑 A5 B0 的 prompt，R1 在 prompt 后附上冻结的上次尝试记录，共 318 个槽位。R1 − R0 的点估计是 −6.6 个百分点（95% 区间 −17.7 到 3.8，p = 0.29），没有检出正效应。

- 预注册：[A6-R-PREREG.zh-CN.md](docs/sprint/A6-R-PREREG.zh-CN.md)
- 结果：[A6-R-RESULTS.zh-CN.md](docs/sprint/A6-R-RESULTS.zh-CN.md)
- 停止原因描述（非预注册）：[A6-R-STOP-DESCRIPTION.zh-CN.md](docs/sprint/A6-R-STOP-DESCRIPTION.zh-CN.md)

下面的 240 条矩阵和 pilot 是未执行的计划。更早的 L5 比 L2 高 9.4 个百分点来自自建题集 medium_48，不是这次实验。

## A5 四变体离线合同（无需 API key）

```powershell
python scripts/run_a5_offline.py --output reports/upgrade/my-a5-smoke
```

B0 无增强；B1 从同一历史池做 lexical set overlap 检索（不是 BM25）；B2 使用结构化 procedure；B3 在 B2 上加当前依赖有效性检查。候选窗口、注入数量/字节和宿主运行 profile 固定。此脚本没有真实 API 开关。

本地保存过 4 个 dev smoke 报告（`reports/upgrade/` 不进仓库，可用上面的命令重新生成）：16 条决策、0 模型调用、主分母为 0，修复成功率为 null。`configs/a5/pilot-12.json` 和 `pilot-20.json` 是 3 repeats 的未启用模板；真实 heldout 清单、模型/价格和货币上限尚未绑定。合同、来源冻结、host 注入收据、失败/缺行/重跑及全模型成本审计详见 [A5 计划与交付](docs/upgrade/A5-PLAN.md)。旧 L0/L2/L5、D0–D3 保留为独立证据，不混入 E-A1。

2026-09-21 后续：已按 20 题目标准备 [SWE-bench Lite 候选包与预算草案](docs/upgrade/A5-PILOT20-STATUS.md)，5 个 repo 各 4 题、240 条 planned assignments。公开输入与私有评测分离，尚未执行 pilot；真实历史池、整仓 grader/环境预检及 A5 预算批准仍待完成。这是候选准备，不能当作已跑通 20 题。

用户已进一步授权约 50 CNY，并支持必要的历史建池。当前分账为 pilot 46.080 CNY、build 3.840 CNY；[首批四个建池工作项](docs/upgrade/A5-FUNDED-BUILD-v1.md)已经准备，但仍等 harness 整仓 grader 和唯一预算账本预检，不会直接调用旧模型客户端。

## 历史主结果（Batch 5.10，legacy diagnostic）

`phase7_medium_48` · 4 个 synthetic medium repos · seed=42 · repeats=2 · real DeepSeek

以下保留原受控实验结果，不代表未知仓库泛化、SWE-bench 分数、统计显著性或多 Agent 单独的因果收益；当前代码版本也不等同历史运行版本。

| Config | Mean | Std | Repeat 0 | Repeat 1 |
| --- | ---: | ---: | ---: | ---: |
| L0（single LLM） | 45.8% | ±2.1pp | 47.9% | 43.8% |
| L2（PlanAct + multi-agent） | 70.8% | ±2.1pp | 72.9% | 68.8% |
| **L5（+ Skill + Memory + policy）** | **80.2%** | **±5.2pp** | 75.0% | 85.4% |

- **L5 − L2 = +9.4pp**（两次 repeat 均 L5 ≥ L2）
- **L5 − L0 = +34.4pp**
- parse_err：L5 total 10，不高于 L2（10）
- 类别增益集中在 **skill_heavy (+15pp)**、**memory_heavy (+12.5pp)**、**multi_file_api (+12.5pp)**

> On 48 medium repo tasks with 2 real DeepSeek repeats, L5 achieves **80.2% ± 5.2pp** vs **70.8% ± 2.1pp** for L2, with both repeats satisfying L5 ≥ L2.

详见 `reports/phase7-variance-summary.md`、`reports/phase7-positive-gain-summary.md`。

## 机制概览

```
TaskGraphPlanner (PlanAct: inspect → locate → patch → test → summarize)
    └── Role Agents: Manager / Locator / Developer / QA
            └── LLMDeveloperAgent (DeepSeek patch 生成)
    └── SkillCard v2 + domain/evidence gating
    └── MemoryHint v2 (episodic / procedural / repo profile)
    └── Augmentation policy router（按 task category 路由 skill/memory）
    └── Context expansion + JSON repair retry
```

| Config | 含义 |
| --- | --- |
| `L0_llm_single` | 单次 inspect + LLM patch + test |
| `L2_llm_multi` | PlanAct + 角色分工 + LLM developer |
| `L5_llm_full` | L2 + Skill + Memory + policy gating |

## 快速开始

```bash
pip install -e ".[dev]"

# 单元测试（无需 API key）
python -m pytest -q --deselect tests/test_a5_funding.py::test_funded_build_bundle_is_prepared_only_and_private_material_stays_outside_public \
  --deselect tests/test_a5_funding.py::test_funded_build_artifacts_are_hashed \
  --deselect tests/test_memory_cards_v5.py::test_generic_builder_matches_v5_bytes
# 356 passed, 10 skipped, 3 deselected
# 跳过的 10 项里，3 项（test_a5_preparation.py）需要 pyarrow，agent 的依赖里没有它；
# 同一环境装过 harness（会带上 pyarrow）时为 359 passed, 7 skipped。
# 排除的 3 项读取未提交的私有证据（A5 资金准备产物 2 个、django-10914 的
# handoff 字段），不是代码回归；有完整本地证据时去掉 --deselect 即可。见 docs/INDEX.md

# Phase 6 deterministic ablation（无 LLM，验证机制链路）
python scripts/run_phase6_ablation.py --tasks benchmarks/tasks/phase6_hard_12.yaml

# Phase 7 主 eval（需 .env 中 DeepSeek 配置，禁止 --mock-llm 用于主结论）
python scripts/run_llm_ablation.py \
  --tasks benchmarks/tasks/phase7_medium_48.yaml \
  --configs L0_llm_single,L2_llm_multi,L5_llm_full \
  --repeats 2 --seed 42

# 离线 mock（仅 CI / plumbing，不作主证据）
python scripts/run_llm_ablation.py --tasks benchmarks/tasks/phase6_hard_12.yaml --limit 3 --mock-llm
```

## DeepSeek 配置

在本地 shell 或根目录 `.env` 设置（**不要把 key 写入仓库**）：

```powershell
$env:LLM_PROVIDER = "deepseek"
$env:LLM_MODEL = "deepseek-v4-pro"
$env:LLM_BASE_URL = "https://api.deepseek.com"
$env:LLM_API_KEY = "<your key>"
```

## 评测分层

| 层级 | Benchmark | 用途 |
| --- | --- | --- |
| Plumbing | `phase6_tiny_24` | runner/trace/report 稳定 |
| Deterministic mechanism | `phase6_hard_12` + A0–A5 | skill/memory 机制链路（无 LLM） |
| **Real LLM（主结论）** | **`phase7_medium_48`** + L0/L2/L5 | 真实 DeepSeek 正向收益 |

旧 tiny/hard eval 为开发历史，**不是**最终主结论。见 `docs/current-evidence.md`、`docs/limitations.md`。

## 目录结构

```
reposkill_agent/       # 主包，入口 python -m reposkill_agent（cli.py）
  protocol/            # 新路径：公开任务 / 私有评测的合同
  evidence/            # 新路径：公开证据定位，不依赖模型和 shell
  memory/              # MemoryRecord、来源与依赖有效性检查、记忆卡 cards*.py、
                       # card_plugin.py（harness B2 加载的插件）；旧 episodic/procedural/repo profile
  integrations/        # harness_h4.py：H4 公开接口适配
  experiments/         # A5 离线合同、变体、审计、资金账本，不含模型客户端
  core/                # 旧路径：TaskGraph、PlanAct planner、上下文扩展、trace
  agents/              # 旧路径：Manager / Locator / Developer / QA + LLMDeveloperAgent
  skills/              # SkillCard、domain gating、validator、公开路由
  llm/                 # ModelClient、prompts、PatchPlan schema
  adapters/            # WorkspaceAdapter（fake / openhands）
  baselines/           # 可选外部基线（vanilla DeepSeek、OpenHands、SWE-agent、Aider）
  eval/                # 旧 ablation runner、注入策略、报告；upgrade_demo、h4_fixture
benchmarks/
  tasks/               # phase6 / phase7 任务 YAML
  repos/               # 4 个 medium + 7 个 tiny 合成 Python 仓库
  a5/                  # A5 各版本记忆卡（v2-v6）及 manifest；pilot20 来源与许可证
configs/               # default.yaml；a5/ 下为交接、资金、pilot 模板和来源锁
scripts/               # 见下表
docs/
  A5-FINAL-REPORT.zh-CN.md, MEMORY-FINAL-REPORT.zh-CN.md, PROJECT-HISTORY.zh-CN.md
  INDEX.md             # 文档分类和阅读顺序
  sprint/              # A5-main / A6-R 预注册、结果、checkpoint 和记忆协议（只追加）
  upgrade/             # 新路径 TP01-04、H4 适配、A5 计划与验收
  memory-v2/           # 记忆诊断与文献
  archive/             # 早期总计划、Phase 7.1 计划、TP01-04 实施计划与任务包
  current-evidence.md, limitations.md, case-study-*.md, code-reading-guide.zh-CN.md
reports/               # phase6 / phase7 汇总；llm/ 下是历次 L0-L5 汇总，archive/ 为 Batch 5.7
tests/                 # 51 个测试模块，无需 API key
```

| 用途 | 脚本 |
| --- | --- |
| 旧路径实验 | `run_baseline.py`、`run_graph.py`、`run_memory_ablation.py`、`run_phase6_ablation.py`、`run_llm_ablation.py`、`run_mechanism_smoke.py`、`run_external_baselines.py` |
| 新路径演示与 H4 | `demo_memory_validity.py`、`prepare_h4_fixture.py`、`upgrade_snapshot.py` |
| A5 准备 | `run_a5_offline.py`、`prepare_a5_pilot20.py`、`prepare_a5_funded_build.py`、`build_memory_cards.py`、`dev_time_selection.py`、`handoff_sha.py` |
| A5-main / A6-R 分析 | `analyze_a5_main.py`、`analyze_a6_retry.py`、`make_a6_synthetic.py`、`a6_diff.py`、`describe_a6_stops.py`、`posthoc_a6_anchor.py`（后两个非预注册） |

原始运行输出（`reports/llm/*/` 下的逐题结果、`reports/phase6/`、`traces/` 下的轨迹）只保存在本地，仓库里只保留汇总。

## Case Studies

| 主题 | 文档 |
| --- | --- |
| Skill domain gating | `docs/case-study-skill-gating.md` |
| Memory reuse（holdout） | `docs/case-study-memory-reuse.md` |
| Context expansion / JSON repair | `docs/case-study-context-repair.md` |

## 不做什么

- 不复刻 coding-harness 的完整 harness/eval/policy
- 不做 UI、Docker 平台、分布式 worker
- 不做完整 SWE-bench pipeline
- 不在主结论中使用 `--mock-llm`

## 参考

- `docs/archive/PHASE7_1-POSITIVE-GAIN-HARDENING-PLAN.zh-CN.md` — Phase 7.1 执行文档
- `docs/INDEX.md` — 文档分类和阅读顺序
- `docs/code-reading-guide.zh-CN.md` — 代码阅读顺序与问题清单模板

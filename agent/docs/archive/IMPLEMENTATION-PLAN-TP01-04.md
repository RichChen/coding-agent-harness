# coding-agent 实施计划 v0.1

> 历史实施计划（2026-09-18 至 09-19）。TP01–TP04 已按此计划实现并离线验证；当前接口见 [PUBLIC-API](../upgrade/PUBLIC-API.md)，实现与限制见 [实现记录](../upgrade/IMPLEMENTED-TP01-04.md)。原文保留，用于追溯当时的范围和验收标准。

2026-09-21 A5 更新：H4/H5 的单任务真实联调已有 harness `real-04` 证据，本轮未重跑。A5 的 E-A1 B0–B3 合同、同池选择器、注入/用量/分母审计和 4 个离线 dev smoke 已完成，见 [A5 计划](../upgrade/A5-PLAN.md)与[验收记录](../upgrade/A5-VERIFICATION.md)。真实 12/20×3 pilot 仍仅为未启用模板，缺真实 heldout 审查、模型价格与货币上限；不把离线完成称作 R3 研究证据完成。下文为各阶段历史快照。

2026-09-21 H4 更新：A4 的 async JSON adapter 与一个 PublicTask/私有 EvaluationSpec 交接包已实现，见 [H4 适配说明](../upgrade/H4-ADAPTER.md)。本轮不启动真实 API；在 harness 自身真实运行完成、受控外部插件持久注册可用后，才执行插件开/关各一次的跨仓烟测。

执行更新（2026-09-19）：用户已选择本任务直接实施。TP01–TP04 的离线实现与 pre-harness strategy contract 已完成，见 [实施记录](../upgrade/IMPLEMENTED-TP01-04.md)；下文保留 2026-09-18 的设计基线和当时状态，不能据其中“待授权”判断当前授权已失效。

日期：2026-09-18。状态：A0 准备交付，未授权开始产品实现。工作目录：`D:\crq\src\coding-agent`。本计划是核对当前源码后的设计，不表示后述 API、命令或实验已经实现。

依据：agent 目标 v1.0（未公开）、共享契约 v0.1（未公开）、harness 目标 v1.0（未公开）。三份文件原名、版本和内容完整保留，原件与副本 SHA256 相同，见 [核查记录](../upgrade/VERIFICATION.md)。research-workflow 的研究合同约束落实在第 7 节：先冻结数据、指标、成本、失败信号，再实验，不追求预设正分数。

## 1. 最终形态和职责

本项目成为“公开未知 issue → 可追溯证据发现 → 可选 Skill/Memory → 有效性拒绝/重验 → 修复策略 → 独立评测”的策略与研究层。核心问题是经验在什么前提下可复用，代码/依赖变化后何时不应使用旧经验。缺少证据和负结果都是合法输出。

coding-harness 负责执行循环、模型网关、全部在线预算、工具权限和执行、取消、进程/容器生命周期、上下文窗口、事件序号与持久化、运行恢复及独立 grader 的执行设施。本项目只提交 action/context/event proposals 和可序列化策略状态，负责任务/记忆数据协议和修复/记忆指标。不实现第二个 runtime、Docker 平台、HTTP 重试系统、通用事件数据库或跨服务恢复系统。

旧 GraphRunner、fake/OpenHands adapters、LLM client 留作 legacy compatibility。新主路径不借旧 client 绕开宿主；单仓测试用 fixture，另一仓未完成不阻塞策略开发。首轮不改 README 等现有产品说明、不改源码、不运行真实 API、不下载数据、不提交或推送，也没有向其他任务发消息或修改对方仓库。

## 2. 当前基线和现状证据

### 2.1 工作区与环境

- `.git` 与 `.agents` 是存在但为空的普通目录；`git status --short` 返回 not a git repository。没有可引用的 HEAD，也不能报告“工作树干净”。本目录、源码/测试/脚本/docs/configs/benchmarks 范围及已检查祖先路径未发现 AGENTS.md；全目录搜索遇到旧 `.tmp/pytest_basetemp` 访问限制后改用上述限定范围，未读取或修改受限缓存。
- 当前默认 `python` 是不可启动的 WindowsApps 别名；旧文档的 `conda activate coding-agent` 环境在已检查的用户 Conda 目录中不存在。
- 可借用既有解释器 `C:\Users\Administrator\.conda\envs\coding-harness\python.exe`，Python 3.11.15，Pydantic 2.13.4 / PyYAML 6.0 / Typer 0.25.1 / Rich 15.0.0 / pytest 8.4.2 / Ruff 0.15.20。只是调用现有环境，没有修改环境或对方仓库，也不代表已安装/集成 harness。
- 44 项定向离线测试通过（21.05s）；`ruff check reposkill_agent tests scripts --no-cache` 通过；CLI help 通过。未运行全套 suite、真实 OpenHands、容器或付费实验。准确命令和覆盖范围见 [VERIFICATION.md](../upgrade/VERIFICATION.md)。
- Windows 本机支持上述离线 Python 检查；Linux/container/network isolation/cancel 等能力本轮未验证，未来以宿主 capability profile 为准。无 GPU 需求，不需要安装向量库或新依赖才能开始首批工作。

### 2.2 实施时的基线/快照方案

已生成 [planning-source-manifest.json](../upgrade/planning-source-manifest.json)，记录 102 个源码、测试、脚本、配置及入口文件的大小与 SHA256；它只是本轮核查清单，不是可恢复源码快照，也不声称可重现历史实验运行时的代码。

用户选定首包并授权实现后，先重新核对这些 hash 和用户新增指令，保留所有差异；建立 `baseline_id`、UTC 时间、源文件清单和可恢复快照。只纳入明确 allowlist：`reposkill_agent/`、`tests/`、`scripts/`、`configs/`、`pyproject.toml`、`.gitignore`、README 与必要文档；benchmark 仅逐个纳入已审阅的小 fixture，历史结果仅记录指定文件 hash/引用。排除 `.env`、私钥/凭据、`.git`、缓存、traces/workspaces、全量 reports、PDF、大数据及外链/reparse points。先审查清单和敏感内容，再在专用 `baseline/<id>/` 保存；不得对父目录建仓或全目录 `git add .`。

若用户提供真正的已有仓库，则用其 HEAD + 本地 diff/hash；否则优先限定快照，不修复空 `.git` 或擅自初始化父仓库。每包提交交付清单、before/after hash 和统一 diff，哪怕没有 Git 也可供 review/回滚。检测到同文件用户改动时先合并理解，不能覆盖。后续若建本目录 Git，须明确范围；现有 `.gitignore` 的全局 `*.jsonl` 会忽略未来契约 fixture，届时只对白名单 fixture 做例外，运行 trace 仍不入库。

### 2.3 历史结果保留

核对 `reports/llm/20260704_092308/llm-ablation-summary.json` 与 Markdown：48 tasks、2 repeats、seed=42、mock_llm=false、seed_memory=false、每 task 最大调用数 3；L0/L2/L5 各 96 行，成功 44/68/77（45.8%/70.8%/80.2%），调用总数 99/153/147。原报告、配置、失败案例不重写。

当前 `LLMAblationRunner._run_once` 明确 `adapter="fake"` + `developer_mode="llm"`；FakeAdapter 在本地真实读写并启动测试子进程，真实 ModelClient 可以同时使用。故旧主实验不是纯 mock，也不是 OpenHands SDK 实际执行证据。OpenHandsAdapter 普通文件/命令方法同样是本地操作，只有 `run_agent_task` 使用 Conversation；adapter 名称不能替代 backend 证明。

这些是 4 个手工 synthetic medium repo 的 legacy/oracle-assisted 证据，不是 SWE-bench、未知 repo 泛化、统计显著性或“多 Agent 因果收益”。当前代码还晚于原实验：例如 auth domain 与测试写入保护已更新，不能拿当前源码 hash 伪装成历史版本。旧 memory 在每个 repeat 内在线积累、repeat 间分离，带标签 probe 的 warmup 不一定计入任务分母；新协议不继承这套隐含规则。旧负结果继续保留。

### 2.4 差距矩阵（行号为本次核查快照，后续按符号定位）

| 目标/状态 | 当前文件与符号证据 | 实施决定与验收落点 |
|---|---|---|
| A1 公私任务隔离：缺失 | `core/contracts.py:11 TaskSpec` 混 issue/expected_files/tags；`eval/task_spec.py load_tasks` 全量载入；`agents/contracts.py AgentContext` 持有完整 TaskSpec | 新公共 DTO 与私有 EvaluationSpec 分开；旧类型仅 diagnostic；TP01 |
| A1 文件隔离：缺失 | `eval/graph_runner.py:391 _prepare_workspace` 整 repo copytree；`llm_ablation.py _run_llm_single` 同样复制；没有私有 grader 边界 | 由公开 artifact manifest 导出 agent workspace；私有数据不给挂载，宿主负责实际沙箱；TP01 |
| A2 未知定位：缺失 | `agents/locator.py:21` 遍历 expected_files；空列表时 success 仍可能为真；`core/planner.py:17,29` 把答案文件写入 goal | 独立 EvidenceState + issue/tree/search/公开 traceback baseline；无命中返回 insufficient_evidence；TP02 |
| A2 context：部分已有 | `core/context_expansion.py:38 expand_allowed_paths` 有 3/6 路径上限，但第 60/85 行依赖 expected_files；`llm_developer.py _context_paths` 同样依赖 | 复用去重/配额思路，realistic 仅用已观察证据；TP02 |
| 公开测试读写：已有保护但有缺口 | `llm_developer.py _writable_paths`、`core/context_expansion.py:20` 排除 tests/ 写入；`llm/prompts.py:119 build_user_prompt` 却只遍历 allowed_paths（可写集合） | 只读文件可以入正文但不能成为写目标；新增内容级验证，不能只检查 readable_paths 指标；TP02 |
| A5 Skill：部分已有 | `skills/models.py SkillSpec` 有版本、证据、recipe、anti-pattern/verifier；`retriever.py retrieve` 以 tags 加分；`validator.py _evidence_present` 允许标签替代 traceback | 留旧卡供 diagnostic；新卡需来源与公开前提，公共检索不传 tags；后续 A4 |
| domain 小修：已存在 | `skills/domain.py:8 TASK_DOMAIN_TAGS` 包含 auth；`memory/matching.py:8` 引用同一常量 | 不重复“补 auth”；44 项检查含 domain tests |
| A5 Router：缺失无 oracle 路径 | `eval/injection_policy.py:65 resolve_augmentation_policy` 以 task_category(task.tags) 决策 | category 仅离线分析/诊断；realistic 初期 no-op，A4 加公开证据规则；TP01 封边界 |
| A3 provenance：部分已有 | `memory/models.py:12 MemoryHint` 只有 source_task_id/confidence 等；`procedural.py ProceduralMemory` 有来源任务/时间；`episodic.py` 有 JSONL+SQLite index | 新 MemoryRecord 版本层，不破坏旧文件；source run/artifact/hash/版本/validation 不可省；TP03 |
| A4 validity：缺失 | `memory/consolidator.py:22 repo_fingerprint` 只 hash repo_path；profile 无 base commit；不存在 tri-state validity/状态迁移 | repo identity 与代码/环境 fingerprint 分开，依赖级检查；TP04 |
| A6 抽取：部分且污染 | `consolidator.py:25,45` 使用 expected_files/tags，成功后固定 confidence=.85；`matching.py:48,67` 路径约定和 holdout ID 配对 | legacy store 永不直接接入 realistic；新 builder 只读来源白名单轨迹；A2/A4 |
| 候选真实性：需修复新路径 | `memory/retriever.py:82–102 _collect_candidates` 即使没有 profile 仍造 confidence=.92 的 profile hint；按 seed_id 加权 | 空 store 应零候选；source 缺失不是自动已验证；TP03/04 |
| 记忆效果指标：仅代理 | `graph_runner.py:285–293` 注入+最终通过=useful，注入+失败=stale_or_wrong | 新指标分 retrieved/selected/actually_injected/observed_following，旧字段标 legacy proxy，不改历史数值 |
| A7 模型/工具：legacy 可用，集成缺失 | `adapters/base.py WorkspaceAdapter` 和 `ModelClientProtocol` 同步；`llm/client.py ModelClient.complete_json` 直接 HTTP | 新 async PolicyPlugin，宿主执行 action；禁止嵌套 asyncio.run 或阻塞宿主 loop；A4 |
| 调用计量：部分已有 | `llm_developer.py _call_llm/_attempt_json_repair` 都增 call_state；`graph_runner.py run` 每任务归零；`llm/client.py` 区分 provider_usage/char_approx | 保留已测 budget/reset；新路径统一宿主 usage，包括 failed attempt、router/builder/verifier |
| 公平评测：需新增 | `llm_ablation.py _run_llm_single` 一次 patch 后测；L2/5 graph 可 context recovery/replan；warmup 不进 rows | 新 B0–B3 同底座预算，旧 L0/2/5 不改名伪装新实验 |
| 独立结果/事件：缺失 | `FakeAdapter.run_tests` exit_code==0 即 passed；GraphMetrics.success 用它；`core/trace.py TraceEvent` 无 schema/seq/operation/attempt | 新 RunResult 与 EvaluationResult 分离；事件由 harness 写，agent 不实现恢复内核 |

上表源码路径均相对 `reposkill_agent/`（以 reports/tests 等开头的除外）。更细接口冲突见 [CONTRACT-CHANGE.md](../upgrade/CONTRACT-CHANGE.md)。只读代码发现未全部做故障复现，不能把表内推断当新实验结果。

## 3. 架构与阶段依赖

```text
私有数据准备器 ──> EvaluationSpec ─────────────────────> 独立 grader
      └─白名单导出 PublicTask/公开 workspace manifest
                         ↓
              EvidenceState / EvidenceLocator
                         ↓
允许历史轨迹 ─> MemoryRecord store ─> retrieval/validity/router
                         ↓
              PolicyDecision + serialized state
                         ↓
 coding-harness: model/tools/budget/context/events/backend
                         ↓
                公开 observation 回到 EvidenceState
终止后: RunResult + EvaluationResult ─> 逐任务报告
```

初版插件选择 in-process async Protocol + 版本化 JSON payload，采用可选 adapter，不跨仓导入对方内部类。`decide(input) -> decision` 只做策略；读取代码提交 proposal，下一轮接收观察；慢解析/检索必要时用有界 executor，线程取消不等于外部进程取消。旧同步 runner 不套进宿主 loop；不选子进程 JSONL bridge，以免首版新增通信/取消责任。若未来隔离需求迫使改 bridge，另写契约版本和迁移。

| 阶段 | 前置 | 可交付产物 | 通过条件 |
|---|---|---|---|
| A0 当前计划 | 无 | 三份目标、差距表、核查记录、快照索引、4 包、契约建议 | 无源码变更，事实/设计/未测清楚 |
| A1 公开边界与定位 | A0；TP01 后 TP02 | PublicTask/EvaluationSpec、公开导出、EvidenceState、no-op realistic 策略 fixture | 已完成离线 sentinel、边界、定位和 fixture walkthrough；真实 host observation 仍待 harness |
| A2 来源与记忆存储 | TP03 可独立启动；接入需 A1 | MemoryRecord v1、版本 store、legacy 显式隔离、manifest 校验 | 任一条目可追溯，缺证据不 validated，空 store 不生 profile，坏记录隔离 |
| A3 有效性 | A2；TP04 | tri-state validity、依赖索引、冲突与隔离 gate、漂移 demo | API/依赖改变拒绝，无关变化保留，unknown 保守处理；注入有实际收据 |
| A4 策略/轨迹/集成 | A1–3；真实连接依赖 harness 公共接口 | 无标签 router、公共轨迹 builder、async plugin/state restore、支持矩阵 | router、trajectory builder、纯 state restore、单仓 fixture 已完成；async plugin/receipts/真实 harness 仍待接入 |
| A5 固定小实验 | A4 + 版本化协议 + 用户模型费用边界 | A2 漂移对照优先，随后独立 A1 baseline 实验；原始结果/成本/案例 | assigned 全分母、失败保留、来源/split 固定；没有正收益也可验收 |
| A6 可选扩展 | A5 证据指出具体缺口 | 未见 repo/不同 backend、LLM router、更多真实任务 | 每次单变量和独立预算，不强制实现 |

首批文件所有权：TP01 公共/私有 DTO 和数据入口；TP02 evidence 模块；TP03 memory record/store；TP04 validity 模块。它们是可独立交付/验收的包，不意味着依赖包可以无序同时合并。推荐 TP01 → TP02，TP03 可基于固定输入 fixture 单独开展，TP04 在 TP03 后；任何同文件同批只交给一个实现者。用户尚未指定执行者，本轮不启动任何包。

## 4. realistic / diagnostic 的数据边界

| 数据 | realistic agent 可见 | diagnostic_oracle / evaluator |
|---|---|---|
| 原始公开 issue 中的文件名、traceback、复现 | 可用且保留来源，不能为防泄漏删除合理公开线索 | 同样可用 |
| expected_files、gold patch、人工类别、私有测试、目标未来轨迹 | 不存在于 DTO、workspace、索引、prompt、可见 trace/artifact、memory source | 私有 EvaluationSpec；diagnostic 可额外走独立 OracleContext |
| repo identity/base commit/snapshot、公开依赖文件 | 可用；workspace_id 是受控引用 | 私有路径映射由宿主管理 |
| task_id/task_family | id 仅关联，不解析 holdout/seed/category；family 固定为公开任务类型如 code_repair | 分类标签仅 evaluator 分层统计 |
| 公开测试执行结果 | 可迭代定位/验证，构建记忆必须带版本和公共证据引用 | 不能冒充独立修复成功 |
| 私有 grader 结果 | 终止后不回灌当前策略或默认 memory builder | 后置报告，若未来使用需另立数据协议 |
| legacy store / 人工 SkillCard | 默认不导入；人工技能需 human-authored、来源和适用前提审核 | 原卡/命名约定/脚本修复仅 legacy diagnostic |

拒绝策略：新的 realistic 入口对明确 oracle 字段/未知禁止嵌套内容拒绝并记录字段路径，不把 sentinel 原文写入 agent 可见错误。旧 YAML 的转换发生在私有数据准备侧，按字段和文件 allowlist 显式导出，不能靠 `model_dump(exclude=...)` 把原对象留在 context。未知 optional minor 字段先在 wire 层判兼容/审计，插件只接收白名单视图；不得通过任意 metadata 绕过隔离。

工作目录：开发项目根目录包含 scripted_fixes、reports、benchmark 答案信息和 `.env`，绝不能作为 realistic agent 可遍历的 workspace。只导出固定 revision 的任务源码与明示公开测试；grader 私有树不挂载、不进入 repo tree/搜索索引/公开 artifacts，避免仅“另一个子目录”而仍可经 shell 访问。拒绝越界路径和链接的实际执行由 harness 保证；本项目负责导出清单、契约测试、能力要求。离线 FakeAdapter 只能证明数据装配，不证明 OS 隔离。

至少覆盖以下非干扰测试：唯一 sentinel 分别藏于 gold、tags、private filename、hidden test 和 future trajectory；逐项检查输入序列化、候选索引、prompt、trace 公共投影和 workspace listing；改变私有数据不改变固定观察下的策略决策；改不透明 task_id 不改路径/路由；公开 issue 原本包含的文件名仍可用；诊断结果强制标记且报告禁止混算。

## 5. MemoryRecord、版本和有效性设计

### 5.1 记录与存储

保留现有 MemoryHint 作为 legacy prompt DTO；新增 MemoryRecord，避免就地修改老 JSONL。字段最低要求：`schema_version, memory_id, revision, supersedes, kind(episode/procedure/repo_profile), content_hash, status, source_refs, source_repo_id, base_commit_or_snapshot, source_protocol, source_split, created_at, build_method, builder_model, build_usage, applicability, procedure, verification_recipe, dependencies, validation_evidence, last_validated_at, rejection/use_refs, duplicate_group`。来源引用含 task/run/trajectory ArtifactRef 和 hash，时间使用 UTC。没有模型的规则/人工构建明确标 none/human-authored，不假装模型生成。

repo identity 不取本机绝对路径；代码 revision、依赖环境锁文件/包版本和 runtime profile 分开记。依赖条目声明 path/symbol/API signature/package range/fingerprint、提取器版本及证据范围。不可只存一个 repo commit 然后宣布全部语义有效。

首版 memory store 用独立 SQLite（记录 revision、transition、source/use refs 的单事务），原始证据保留受控 artifact 引用；可导出规范 JSON 作审计快照。它只管理知识生命周期，不是 run event store。实验运行固定只读 memory snapshot，builder 在实验声明的边界发布新 revision；应用新 snapshot 的时点由协议/宿主决定。旧 JSONL 只读迁移，缺失来源进入 candidate/unknown，坏记录/secret 命中进入隔离清单；不覆盖旧 db，不将缺来源数据补猜为 validated。

### 5.2 生命周期和 gate

| 当前状态 | 触发与目标状态 | 注入规则 |
|---|---|---|
| candidate | 来源完整+结构校验+当前版本公开验证 → validated；无法验证仍 candidate | 默认不注入，允许独立请求公开重验 |
| validated | 已知依赖不兼容 → stale；同域相斥记录 → conflicted；来源破损/恶意/secret → quarantined | 仍需本次 validity=valid 和来源 ACL/split 检查 |
| stale | 当前版本公开重验成功 → 新 revision validated，保留旧版 stale | 不靠高相似度或 TTL 自动恢复 |
| conflicted | 显式确定适用前提/公开重验解决 → 新 revision；不能判定则保持 | 不同时注入冲突强建议；无胜者就零条 |
| quarantined | 仅显式审核后新建 candidate revision，再验证 | 自动检索/自动重验不得放行 |
| 任一非 retired | 人工停用/重复合并/已被取代 → retired | 历史保留，不再注入 |

validity 是一次针对目标代码/环境的 `valid / invalid / unknown` 结果，含 reason codes、检查器版本、依赖证据和成本；不是用 status 字段代替所有上下文判定。优先级：任何确定 invalid → invalid；否则存在未能检查的必要条件 → unknown；必要条件都满足才 valid。未知证据不自动把全库变 stale，TTL 到期至多要求重验/unknown。status=validated 但当前 validity=unknown 时也拒绝默认注入。

相关 API 签名不同或 package range 不满足是明确 invalid；完整依赖映射证明未变时无关文件变动可保留 valid；仅文件粗 hash 改变但语义不明时 unknown/revalidation，不伪造细粒度判断；commit 一样也检查依赖环境。缺依赖映射用保守 unknown。依赖→memory 反向索引只优化待检查集合，不改变语义；“整库清空”是独立 baseline，不作为唯一主算法。

抽取与使用分离：`retrieved`、`selected/requested`、宿主收据 `actually_injected`、基于可观察 patch/tool 证据的 `observed_following=true/false/unknown` 分别记。没有宿主收据不算注入；只见最终成功不算采用或有用。失败反馈先分环境/未采用/不适用/实现错误，不能一次失败永久删除技能。规则构建成本的 CPU/时间也记录，API 费用仅在确无调用时为 0。

## 6. 首批可实施/可转交任务包

完整包均带角色中立的执行指令，Codex 与 Cursor 使用同一验收线：

1. [TP01 公私任务边界](tasks/TP01-PUBLIC-BOUNDARY.md)：新 DTO、公开数据导出和 legacy 隔离，先封住 oracle 数据流。
2. [TP02 证据定位](tasks/TP02-EVIDENCE-LOCATOR.md)：独立 EvidenceState/locator，公开测试可读不可写，失败可解释。
3. [TP03 记忆来源与状态](tasks/TP03-MEMORY-PROVENANCE.md)：新记录/独立 store/显式迁移和源 manifest。
4. [TP04 依赖有效性](tasks/TP04-MEMORY-VALIDITY.md)：tri-state、漂移/冲突/隔离 fixture 及 gate。

每包 1 个实现者、局部 diff、离线行为证据；完成后 review 实际变更和产物，不仅看工具摘要。预计范围约每包 1–3 个工作日（粗估，取决于环境/交接返工），不是质量或费用承诺。集成/付费 pilot 单独排期。

## 7. 实验合同草案（未冻结、未运行）

### 7.1 先处理数据来源和 split

实验 owner 为 coding-agent 任务维护者；运行/独立 grader owner 为 coding-harness。数据候选尚未选定、无真实任务来源下载；不能把 `phase7_medium_48.yaml` 去掉 tags 就称真实未知 issue。旧 48 题及已暴露的解法只用于 legacy/诊断回归，不作新 holdout。

准备 `experiment-contract-v1`、`dataset-manifest-v1`、`memory-build-manifest-v1` 后才能执行：逐任务记录 repo URL/许可证或本地来源、base commit/hash、公开 issue/artifact hash、公开测试、私有 EvaluationSpec ref、source_type、split、时间依据、重复/相似 patch 分组、所有已看过该任务的开发活动。去重群组整体划分，不声称消除了模型预训练污染。

建议先 3–5 个开发 smoke（不进入测试分母），之后 12–20 个独立固定目标 × 3 repeats pilot。memory_build、dev、heldout 三集合在 task ID、source artifact、重复组上分离；build/dev/test 数量待候选清单产生后冻结，不能虚构现成任务。

- 同仓未来任务协议：同一 repo 上 build → dev → target 按真实时间和版本先后；没有真实日期时称 synthetic version-order。目标解法、未来任务轨迹不入 build，私有 grader 永不当公开源。
- repo-disjoint 协议：按 repo 整体分到 build/dev/heldout，复制/fork/近重复视作同组。只有目前 4 个手工仓库不足以支撑稳定泛化声明；正式未见 repo 实验需新来源清单。
- 首个 pilot 默认 cold/frozen memory：每任务、每 variant/repeat 从同一允许的初始 snapshot 开始，禁止目标写回。warm 为另立实验，固定任务顺序和发布时机，只允许前序已终止任务的公共轨迹更新；同一目标的 repeat 不互相学习。

### 7.2 优先 E-A2 漂移机制；E-A1 另做

| 合同项 | E-A2：依赖失效 | E-A1：公平经验复用 |
|---|---|---|
| 假设 | 依赖检查减少已知 stale 误注入，同时保留未受影响记忆；成功率收益待测 | 同来源结构化经验可能改善修复，但构建/管理成本可能抵消 |
| variants | D0 无记忆、D1 静态、D2 版本变化整库清空、D3 依赖级失效 | B0 无增强、B1 简单 lexical/BM25、B2 结构化、B3 B2+validity |
| 唯一改动 | gate/清空策略；D1–D3 初始 store、检索器与变更集相同 | B1/B2/B3 使用同一原始历史池、候选/注入配额，结构化构建成本另记 |
| 固定条件 | 相同 model、repair loop、tools、公开 context、retry、prompt 基础、总预算、backend/profile | 同左；B0 也可完整探索/JSON repair，不退化成一次调用 |
| 机制 fixture | API 相关变化、依赖配置变化、无关文件、未知映射，加冲突/损坏 | 空 store、零条路由、人工来源、负迁移和超额上下文 |
| 成功信号 | deterministic fixture 期望矩阵全部满足；真实 pilot 同预算误注入更少、有效保留更高，成功/成本无预设收益 | 配对成功率/成本结果支持复用；在线成本与含构建摊销成本都公开 |
| 独立失败信号 | 大量有效记录被拒、依赖映射仅适配手工题、重验耗尽预算、来源泄漏 | 同来源比较后收益消失、结构化比文本更贵而无收益、单题支撑全部 delta |

主指标分别报告，不合成总分：独立修复成功率 = grader pass / 全部 assigned task-runs；按环境错误、超时、budget exhaustion、invalid patch 单列，partial 不算 pass，invalid_env 不事后剔除。重复结果嵌套在任务内，报告 paired task differences，足够 repo 时做按 repo/task 聚类 bootstrap，少 repo 不声称可靠显著性。

stale 误注入率 = 每次 decision 中已注入的已标注 stale 条目数 / 同轮可评价的 stale 候选条目数（以 run/decision/memory revision 去重），分母为 0 输出 N/A；另报 stale-exposed task-runs / 全 assigned。valid 保留率分母为同轮已标注 valid 候选，按同一粒度定义。采用率必须附 observed_following=unknown 比例，不能以模型“我用了记忆”替代证据。定位 recall 仅 evaluator 私下计算。有效性标签不发给策略。

每个 variant 固定候选集合/顺序、task list、模型 ID/可获得的 snapshot、temperature、工具/上下文策略、call/token/时间限制和失败计分；组织 seed 暂拟 42/43/44，provider 若不支持 seed 则如实记录。不能把组织 shuffle seed 当模型可重复性保证。首个 pilot 可描述结果；若要判定“不可接受退化”，正式开跑前在 v1 写数值阈值，不看结果再补阈值。

### 7.3 成本上限草案与待定费用

以下只是容量估算，新的预算执行器尚未实现；不将现有 `max_llm_calls_per_task=3` 当全链预算。拟每 task-run 总模型请求上限 6（包括 repair/router/verifier/retry 每次 attempt），aggregate input ≤48k tokens、aggregate output ≤12k tokens、wall ≤600s、工具 ≤30 次；单任务增强上下文暂限 2k tokens，受同一个总输入上限约束。tokens 测量优先 provider，预检采用保守 estimate 并标 estimator；不支持货币硬上限的 backend 不得宣称具备。

| 候选批次 | task-runs | 在线 calls 上限 | input / output 上限 |
|---|---:|---:|---:|
| smoke 5 tasks × 2 variants × 1 | 10 | 60 | 0.48M / 0.12M |
| pilot 12 tasks × 4 variants × 3 | 144 | 864 | 6.912M / 1.728M |
| pilot 20 tasks × 4 variants × 3 | 240 | 1,440 | 11.52M / 2.88M |

默认规则 builder/规则 router 不产生外部模型费；如果之后开启 LLM builder，拟最多 20 source traces × 2 calls=40 calls、每 call input≤8k/output≤2k，总计另加 0.32M/0.08M。LLM judge 首版关闭，独立 grader 用测试。模型摘要或在线 verifier 必须占上述 6 次和总 tokens，不另开隐含配额；协议要求更多时先版本化更改。

价格未知：设供应商每百万输入/输出单价为 P_in/P_out（币种、价目生效日期、cache/reasoning 计价待定），12 题 pilot 在线估算上限 `6.912×P_in + 1.728×P_out`，20 题为 `11.52×P_in + 2.88×P_out`；可选构建增加 `0.32×P_in + 0.08×P_out`。另记实际本地 CPU/耗时/存储。服务商失败计费或 usage 缺失标 unknown，不能填零；预留/结算由宿主实施。

用户待定：模型/服务、价目/币种、单次及整个实验货币硬上限、可接受样本规模。没有这些不运行付费实验，key 存在也不是授权。离线 fixtures、split/schema、dry-run cost estimator 和报告模板可以先交付。总成本同时列 online、offline build、按预先冻结 N 个目标任务摊销的 cost；repeat 不用于任意扩大摊销分母，失败重跑及 build/dev 花费都单列。

### 7.4 产物与审计

未来新结果写 `reports/upgrade/<experiment_id>/`：contract/data/memory manifests、代码 baseline/diff hash、configs、environment/backend/model、原始 events/artifacts refs、usage ledger、逐任务 results、paired differences、case studies。所有 schema/配置/源 hash 入 manifest。统计主键 `(experiment_id,task_id,variant,repeat_id)`，attempt 保持关联，重跑不覆盖失败。报告可离线再生成；不把 hidden 内容复制到公开报告/模型数据。

正式运行前冻结上述项目；更改 split、指标、预算、记忆写回或调参时新版本记录原因和哪些旧结果仍有效。最终至少整理“复用帮助”“失效或负迁移”“baseline 更好/无差异”三类案例；若某类未观察到就明确缺失，不编造。

## 8. 首个可投递版本与完成条件

R1（首个可投递）：A1–A3、公开 issue 的可演示定位/修复（明确模型与 backend）以及一组受控 API/依赖漂移证据，来源可追溯、无 oracle 路径、拒绝和 unknown 可解释。只有离线 scripted/mock 修复时，先交“离线机制版”，不能称已完成真实模型修复；真实模型演示等待明确费用范围。R1 可保留 legacy host 执行的诚实能力说明，不宣称已获得容器隔离/取消/恢复保证。

R2（集成证据版）：A4 至少一次真实 harness 任务与插件开关对照，日志关联选择→实际注入→模型→工具→patch→独立验证；README 支持矩阵包含实现/stub/未验证。R3（研究证据版）：A5 小规模配对 pilot 与全成本和负结果，不设 80% 或一定胜过 baseline 的门槛。

共享契约方向一致，但 diagnostic oracle 的承载方式、task_family/ID 标签旁路、public/graded artifact 访问分级、selected/actually_injected 反馈和 async action 语义需要澄清；详见 [CONTRACT-CHANGE.md](../upgrade/CONTRACT-CHANGE.md)。当前仅本仓提案，不代表两仓已经达成新版本。它们不阻塞 TP01–04 的独立离线部分；真实联调前由用户/协调任务同步。

## 9. 本轮交付与下一步边界

交付仅新增 `docs/upgrade/` 下的目标副本、计划、4 个任务包、核查记录、契约建议和源码 hash 清单，另有离线测试生成的专用临时产物。没有改产品源码、旧报告或 `.env`。

等待用户选择“由本任务实施哪一包/阶段”或“交给 Cursor”。得到阶段授权后，完成该阶段的常规实现/回归/产物整理，无须逐文件重复请求；费用边界和破坏性公共契约变更仍单列，不把这份路线图等同全部实现授权。

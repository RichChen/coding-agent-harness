# FIX-13 四组工程适配状态

## 2026-11-11 正式开跑就绪

真实 Docker 无模型预检 **64/64 通过**，覆盖新 30 题各自的空补丁与标准补丁、首题四组在正常入口的提示/工具接线、R 的一次提醒及 UT reasoning 回传。空补丁结果逐题与校准的计数及缺失 ID 精确核对，包括原 P2P 排除口径。一次容器清理超时的原现场与同设置重试通过记录均保留。

身份冒烟完成 2 次 provider 请求，标准峰价费用合计 **0.000212 元**，完整 usage 均已核对，已计入本轮全流程预算。U 与 UT 返回模型均为 `deepseek-flash`，fingerprint 均为 `bc19ba97cbe016479903222bb1578db6`。当前官方版本为 `DeepSeek-V4.1-Flash-1017`，标准峰价命中/未命中/输出为每百万 token 0.04/2/8 元，前后价格页一致。

读取价格页时，旧 helper 因环境 SOCKS 代理缺少 socksio 在任何模型请求前失败。本轮启动器改用与模型配置一致的 `trust_env=False` 读取同一官方价格页，旧 helper 与全局代理保留原设置。针对性启动器检查 23 项通过，随后身份冒烟正常完成，首次失败目录保留。

最终就绪绑定为 `runs/fix13-preflight/ready.json`，SHA-256 为 `883c12735284b8bafcf95bce256b7d2fb8821e2f92a6b1249791f94a763ca690`。它核对实际配置、代码、任务清单、原始预检输出、离线参照/分析代码和身份费用 ledger。正式运行继续固定 240 名额、并发 3、每次 4 元和全流程 160 元上限，并依 148 元启动阈值保留 12 元在途缓冲。

依据本次明确授权启动的命令为

```powershell
.venv/Scripts/python.exe -u scripts/rebench/run_fix13.py --authorized 240,160 --prior-ledger runs/fix13-preflight/identity-smoke/cny-ledger.json
```

正式输出为 `runs/fix13`，驱动日志为 `runs/fix13-preflight/formal-driver.log`。`progress.json` 保存累计进度，每五个题区组输出一次检查点。后续原始运行结果和预定停批以实际记录为准。

正式批次已于北京时间 **11-11 03:15:48** 启动，启动器 PID 为 `21716`，独占锁位于 `runs/fix13/run_unleash.lock`。该锁名复用既有实现，实际启动入口和输入绑定均为 FIX-13 四组。

### 最后区组的未知账单状态

终端中断后，holoviews 最后区组的 E/U/UT 四个名额均有完整 summary、grader 和 settled ledger。R/r1、R/r2 各有 1 个 outstanding reservation，ledger 分别记录 88/70 次已启动调用和 0.364159/0.371128 元已结算费用，缺少对应的 `model.settled` 事件、summary 和 grader 文件。两个 child PID 均已结束，formal runner 已确认不在运行。

因此当前有 238 个可判定最终名额，已知结算标准费用 **54.139546 元**，两个 R 名额的账单状态未知，上界预留为 8 元。已写入 `runs/fix13/recovery-blocked.json` 和 `runs/fix13/batch-report.recovery-blocked.json`，旧 `progress.json` 与运行锁证据保留。未重跑这两个名额，也未把未知费用当作零。

### 正式运行检查点

| 北京时间复核 | 完成题区组 | 最终名额 | 全尝试标准费用/元 | 异常记录 |
|---|---|---|---|---|
| 11-11 04:37 | 5/30 | 40/240 | 6.121094 | 40 次尝试，退役与最终 infra 均为 0 |
| 11-11 07:02 | 10/30 | 80/240 | 16.292241 | 82 次尝试，退役 2，最终 infra 为 0 |

前十区组两次退役分别为 home-assistant E 的容器清理失败（0.058189 元）与 vyper UT 的 `container_gone_before_snapshot`（0.778939 元）。均按既定规则保留现场和费用、仅重试一次，最终名额均可判定。上述检查点未触发停批，harness 工具错误为 0，继续按固定顺序运行。

### B1 后处理 Unicode JSONL 修复与恢复

北京时间 07:50 复核时，批次在第 12 区组暂停。taipy UT/r1 已完成 54 次模型请求和评分，原 grader 为 0/1 F2P、84/84 P2P，普通未解出。随后 `run_swe_task.py` 用字符串 `splitlines()` 读取导出的 JSONL，把 JSON 字符串内的合法 U+0085 分隔符切断，子进程退出。原文件有 445 个完整物理事件行，错误拆成 446 段；SQLite 日志、候选和 grader 原件均完整。

原账本的 54 次请求全部为 provider usage，outstanding 为 0，费用为 0.529440 元。恢复仅从原 journal、候选、grader receipt 和账本补回同一条失败 summary，provider 与 grader 重放均为 0 次。该 summary 的原会话写入耗时、原 agent wall time、运行阶段 Docker retry 明细和 export skipped 明细无法完整重建，相关字段留空并在 `recovery.metadata_unavailable` 中注明。原 loop elapsed 仍从终态保留。

最小共享入口修复覆盖 `scripts/run_swe_task.py` 的后评分事件读取，以及 `scripts/run_cr2.py` 被本轮启动器复用的 provider 状态读取，采用 JSONL 物理行读取，保留字符串内的 Unicode 字符。65 项针对性检查通过，既有 64 个真实预检事件导出经新旧读取比较完全一致。模型循环、提示、资源条件、任务资格和评分逻辑沿用原设置。

原暂停报告、原输入绑定、原 ready、修复前代码与补丁均保存在 `runs/fix13-preflight/repair-jsonl-1`，恢复入口为 `scripts/rebench/fix13_reconcile_summary.py`。原始候选、grader、事件和账本字节 hash 逐项核验一致，补回后为 **94 个最终名额、96 次正式尝试，已核对全流程标准费用 19.570358 元**，预定停止检查通过。

恢复绑定链记录于 `binding-chain.json`。ready 从原 `883c1273…ca690` 更新为 `1bc9592088716c8872227614c44f03976ec56c270ee933a079d60c0d89b6cfbf`；输入绑定从 `88927581…3695c` 更新为 `ffbbdc1dffbeb1a7df792ef2b021a5b0ada7022149743e99cb2023ac55e79780`。模型运行时 `src` 指纹保持一致。续跑继续使用相同正式命令和同一身份 ledger，日志为 `formal-driver-resume1.log`。

### 正式批次完成与报告

用户授权的两个 R 名额恢复尝试均已结束，正式批次达到 **240/240**，30/30 题的 8 个名额均有 summary、grader 和 settled ledger。R/r1 恢复后未解出，R/r2 恢复后解出。两次第一次中断目录、两次恢复目录、退出日志和账本均保留，旧中断的未结算预留上界不再作为当前活动预留。

当前已结算标准费用为 **55.442973 元**，包括 0.000212 元身份冒烟、正式 ledger、两次恢复和退役尝试。历史中断 ledger 的已结算部分已包含在内。供应商账户账单仍需独立对账，报告中保留 8 元历史未知预留上界作为证据边界。

统一事实汇总和配对统计见 [FIX-13 结果报告](../../analysis/fix13/FIX-13-RESULTS.zh-CN.md) 与 [report.json](../../analysis/fix13/report.json)。报告使用 30 个完整题目，U/R/E/UT 解出运行数分别为 16、18、26、28，R−U 为 +3.3 个百分点，E−U 为 +16.7 个百分点，UT−U 为 +20.0 个百分点。三项主比较采用题级分层 bootstrap 和符号翻转，Holm 校正后的 p 值与普通区间均在报告中列出。R/E 遵从性、重复、定位后未编辑和 UT reasoning 作为描述性指标报告。

必要复核已完成并写入 `docs/analysis/fix13/review.json`。失败分类按 seed 20261119 抽样 12 条，12/12 与独立复核一致。说明行为按 seed 20261120 对四组各抽 8 个有工具轮，E 样本中 4 条提及上步结果、0 条泛泛下一步、4 条无句。该语义复核由单人完成，作为描述性核查。

最终报告不把恢复尝试视为新的独立题目样本，也没有用旧 30 题结果改动提示或题目资格。四组的提示内容、模型配置差异和评分口径沿用预注册设计。E 的可见说明与 UT thinking 的内部路径仍存在共同变动，报告保留这一归因限制。

## 2026-11-11 连续执行授权与预检准备

用户现已明确授权，新 30 题全部到位后完成全流程无模型检查及开跑收尾，通过即启动唯一正式四组批次，无需再次询问。执行采用 240 名额、并发 3、单次 4 元、全流程标准峰价 160 元，148 元启动阈值及 12 元在途缓冲。下文 11-10 的待授权表述保留为当时记录。

接入执行者已于北京时间 11-11 02:25:51 完成 30/30，并明确交接进程退出、累计文件停止写入。交接 plan、exposure、manifest、ingest 四项 hash 已核验，轻量检查确认 240 名额及四组配置一致。30 题离线参照已生成，SHA-256 为 `eba3f3b60f808b73e956a7f93676dc3b9403ca5a78a0abb9435d92fd7eb7788e`。

`scripts/rebench/fix13_preflight.py` 已开始复用正常 `run_task`、真实 Docker 后端、评分器和本地 scripted HTTP 传输，逐题执行 empty/gold 与首题四组接线，共 64 个无模型 case。输出隔离在 `runs/fix13-preflight`，驱动日志为 `e2e-driver.log`，累计结果为 `e2e-report.json`。当前处于实跑阶段，完整通过状态以最终累计结果为准。

新增开跑收尾包括离线 gold 参照与分类代码绑定、0.5 元内两请求身份冒烟入口、身份 fingerprint 与价格窗口对接、正式启动前证据 hash 核验，以及每题区组的轻量进度文件和每五区组检查点。身份冒烟 ledger 必须通过 `--prior-ledger` 计入全流程费用。上述属于实现既定预检与证据绑定，比较提示、限额和评分口径沿用已批准设计。

补充本地检查已完成，四组五轮 scripted SSE fixture 通过，启动器针对新增绑定检查共 22 项通过，预定配对 bootstrap/符号翻转/Holm 的合成数据检查通过。离线统计入口为 `scripts/analysis/fix13_report.py`。这些本地结果与后续真实 Docker 检查分别记录，当前尚未进行本轮身份付费请求。

### 无模型预检的第一次基础设施重试

第 12 个 case（home-assistant 的 gold）12 次工具均成功，但 Docker 删除容器在已有重试后仍超时，清理累计耗时约 10.41 秒，实际结果按 `cleanup_failed/infra_error` 保留。核验时该运行容器已退出，现场保存在 `gold.infra1`、`e2e-report-before-infra-retry1.json` 和原驱动日志。

本次仅在同一题、同一配置和同一清理限额下重试一次，比较条件与运行时代码沿用原值。此前 11 个通过 case 复核并复用。重试已完整通过正常作答、导出和评分路径，原异常继续保留在 `preflight-retries.json`。本阶段 provider 请求仍为 0。

## 2026-11-10 实现交接记录

2026-11-10。供主会话审阅后采纳。此次实现、mock 和开发轨迹回放均为本地工作，付费模型调用 0 次，Docker 执行 0 次。正式运行目录 `runs/fix13` 尚未创建。

## 摘要

- 用户已同意采用简单重复提醒并保留 U、R、E、UT 四组。R 已按这个明确决定实现，当前规则为连续 3 个单工具轮的工具名和 JSON 参数相同后提醒一次。提醒明确允许合理重复。
- E 采用设计稿的固定系统提示，工具调用与可见说明沿用同一响应。缺句、空句、超长说明和纯文本结束均经过正常循环入口的 mock 检查。
- 独立四组配置和 `run_fix13.py` 已就位。候选规模固定为 30 题、240 名额，每题 8 个名额，最多并发 3，题区组结束后进入下一题。
- 预算采用设计允许的简单方案。在 160 元候选总界内保留 12 元在途缓冲，已结算全流程费用达到 148 元时停止启动。每次尝试复用既有 4 元标准峰价 ledger。
- 当前无模型预检的配置检查通过。正式名额检查返回失败，原因为完整 30 个位置尚未接入，符合目前仅首 5 个位置获准接入的状态。预算和付费开跑仍需独立审阅。

## 变更位置

| 文件 | 本次变化 |
|---|---|
| `src/config/schema.py` | 新增可选 `runtime.tool_intervention`，默认值省略序列化，保留旧配置 hash |
| `src/harness/factory.py` | 将干预配置接到正常 AgentLoop 构造路径 |
| `src/runtime/tool_intervention.py` | 两条固定提示及简单同调用检测状态 |
| `src/runtime/loop.py` | R 在第三次工具结果之后追加一次普通 user 消息，E 在原系统提示末尾追加固定段落 |
| `src/runtime/session.py` | 启用干预时绑定运行时指纹，恢复沿用已有会话机制 |
| `configs/rebench/fix13-{u,r,e,ut}.yaml` | 四份独立配置，模型固定 `deepseek-flash` |
| `scripts/rebench/run_fix13.py` | 四组名额、块内并发、预算缓冲、一次基础设施重试、费用预测与恢复核验 |
| `tests/test_fix13_intervention.py` | 干预边界、循环入口、工厂接线和持久恢复测试 |
| `tests/test_fix13_launcher.py` | 注入本地 runner 的名额、费用、停止、崩溃与恢复 mock |
| `docs/roadmap/FIX-13-FOUR-ARM-DESIGN.zh-CN.md` | 第 3.2 节及相关预检记录用户批准的简单提醒修订 |

U、R、E 的其余设置与原 U 对齐。UT 沿用 thinking 开启、high、32768 输出额度，其余共同设置对齐 U。旧 FIX-12 配置及 `run_unleash.py` 文件保持原内容。

## R 的已批准修订与开发证据

旧严格规则的全文回放读取原始 `tool.intent` 和 `tool.result`，展开完整 artifact 并校验引用 SHA-256。严格检测曾要求成功、只读、3 次完整可见输出相同。包装末行包含 `duration_ms`。

| 规则 | 既有 U 高重复 27 次中的触发 |
|---|---|
| 原严格规则 | 0/27 |
| 诊断副本仅固定包装末行的 `duration_ms` | 1/27 |
| 用户批准的简单同调用提醒 | 24/27 |

去计时唯一命中为 `Kozea__WeasyPrint-2494/U/r1` 第 35 请求。三次完整输出分别为 6835、6835、6836 字符，`duration_ms` 为 94、96、109。诊断保留完整 stdout/stderr，以及 exit_code、status、truncated、bytes_seen、bytes_kept、cleanup 等字段。其他未命中涉及更广的适用域和交替调用。

简单规则轻量复用了 `scripts/analysis/fix12_trajectory/out/runs.json` 的完整调用键与请求分组。其余 33 次 U 运行触发 0/33。全部通过运行触发 2/23，全部失败运行触发 22/37。两次通过者分别为 anyio 的命令执行和 Agents-for-python 的重复编辑。高重复集漏掉的 3 次为 benefits/r1 与 sqlglot/r1、r2 的交替调用。完整任务名及请求号保存在设计稿第 3.2 节。

这些数字只描述已暴露开发轨迹的覆盖。通过者触发提供人工语境复核线索，提醒后的收益与代价需要新题比较测量。用户于 2026-11-10 看到这些结果后同意简单提醒和四组保留，线上实现已移除严格只读解析与结果哈希依赖。

当前 R 检测仅输入工具名和合法 JSON 参数。参数按键排序，保留命令空白、路径和参数值差异，多工具轮及不同调用重置计数。成功、失败、截断、输出变化、重复写入及测试调用均按同一规则处理。每次运行最多一条提醒，所有后续工具仍经过原执行路径。触发请求号、调用键和提醒写入正常历史与 context 事件。

## 预算、停止与恢复

工程选择为设计第 8.2 节允许的 148 元启动阈值和 12 元缓冲。启动前同步检查已结算费用，最多 3 个在途尝试各受既有 4 元 ledger 限制。边界 mock 从 147.90 元已结算费用出发，并发 3 次各 4 元尝试，最后记录 159.90 元并停止新增尝试。恢复时已结算 148 元会启动 0 次。

正式运行前须通过重复 `--prior-ledger PATH` 列全本轮已有开发、冒烟及作废尝试的 ledger，初始费用为这些 ledger 的合计。传入文件及金额绑定到本批输入，恢复检查其一致性。当前本任务产生的付费费用为 0。批内正式尝试、基础设施作废和重试均由原 ledger 计入，基础设施名额最多自动重试一次。

启动器复用原有标准峰价、模型身份、usage、答案隔离、评分与价格页检查。新入口独立处理四组比例门槛、最近 10 次至少 3 次基础设施错误、每完成 5 个题区组后的保守预测。价格页暂时无法读取时停止新增尝试。

恢复核验包括独占运行锁、child PID、原名额、mock/正式属性、profile/variant、输入文件与代码 hash、已有费用。完整目录中的最终名额按原状态保留。无 summary 的中断目录要求先核对进程和费用。新崩溃报告将费用完整合计记为未知，并列出每个待核对尝试的 4 元预留，保留已确认费用，避免把中断尝试显示为免费。

160 元界限依赖每次尝试的 4 元 ledger、最多 3 次并发、完整的先前费用清单与单一批次运行锁。它约束按 provider usage 计的标准峰价费用，账户实际账单仍待供应商对账。

## 已执行检查

新增检查通过正常工厂、AgentLoop、RunSession 与注入 runner 的启动器入口覆盖以下情形。

- R 第三次才提醒，不同调用和多工具轮重置，每运行一次上限，恢复后保持一次上限，提醒后后续相同工具照常执行。
- R 在成功、失败、截断和不同可见输出下均按调用重复规则工作。重复编辑和测试型命令也可触发。
- E 的说明和工具在同响应中留存。缺句、空句与超过 40 词均保留原调用次数，纯文本仍正常结束。
- U、R、UT 原系统提示一致，只有 E 追加固定段落。默认配置序列化与既有配置 hash/fingerprint 回归通过。
- 四组与 240 名额校验、旧 180/250 授权拒绝、3 并发和题区组顺序、恢复幂等、重试次数与费用、预算边界、在途进程、中断目录、输入绑定、价格变化、未知 usage、错误组别与资源比例停止。

测试在 `.venv` 中执行，最终合并检查 **131 项通过**，其中新增干预与启动器检查 40 项，原 FIX-12/AgentLoop/配置锁回归 91 项。随后针对价格页读取失败处理复核启动器测试，20 项通过，该子集已包含在 131 项内。`compileall` 与受改已跟踪代码的 `git diff --check` 也已通过。

## 后续必要检查

- 主会话审阅实现、设计修订及 160 元候选预算，确认正式运行授权和完整费用账本清单。
- 完成全部 30 个位置接入与必要的新题隔离检查，全量无模型作答/评分走通，冻结最终 manifest、顺序和分类参照。此任务仅执行纯本地 mock，真实 Docker 端到端检查留在接入流程。
- 核验开跑时价格、返回模型身份与 fingerprint，保存正式批次绑定。
- E 的语义遵从性及“解释后提前结束、没有交出候选”仍需依设计在离线报告中统计和人工复核。现有完整响应与轨迹供该统计使用，已分配名额保留在组内。

当前代码与设计稿交主会话审阅，正式运行就绪状态仍为待完成预检。

## 本地复核命令

在 `D:\crq\src\coding-harness` 执行。

```powershell
.venv/Scripts/python.exe scripts/rebench/run_fix13.py --preflight

.venv/Scripts/python.exe -m pytest tests/test_fix13_intervention.py tests/test_fix13_launcher.py -q --tb=short --disable-warnings

.venv/Scripts/python.exe -m pytest tests/test_fix12_config_diff.py tests/test_fix12_ledger.py tests/test_fix12_launcher.py tests/test_fix12_remaining_calls.py tests/test_fix12_request_options.py tests/test_agent_loop.py tests/test_fix1_config_lock.py -q --tb=short --disable-warnings

.venv/Scripts/python.exe -m compileall -q src/runtime/tool_intervention.py scripts/rebench/run_fix13.py tests/test_fix13_intervention.py tests/test_fix13_launcher.py
```

首条命令只读配置和输入，当前会因完整接入未完成以状态码 1 退出。正式执行入口要求独立审阅后的 `--authorized 240,160`，本轮使用命令限于上述本地检查。

## 最终状态

上述 11-10 记录中的“尚未完成”表述属于实现阶段快照。当前正式批次已完成 240/240，统一事实汇总见 `docs/analysis/fix13/FIX-13-RESULTS.zh-CN.md`。最终进度和账本摘要见 `runs/fix13/progress.json` 与 `runs/fix13/batch-report.json`。

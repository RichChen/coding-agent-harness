# 下一阶段执行状态

更新：2026-10-30。依据：[纲领](HARNESS-NEXT-CHARTER.zh-CN.md)、[阶段方案](HARNESS-NEXT-PLAN.zh-CN.md)。

作者已审阅方案并授权开始分派和推进，优先使用可见会话；主会话负责协调、审查和集成。实质性方向选择或需要作者审阅的阶段检查点再停下讨论。

## 当前阶段

**已到 P3 检查点：正常入口、工作区检索、公开验证与有界反馈、候选交付及只读检查/导出均已实现，通过独立技术审查。** P3 从 `5e33b40` 开始；真实 Sphinx 机制演示见 [P3 报告](P3-INTEGRATION-RESULTS.zh-CN.md)。首版默认关闭，仅支持固定 Sphinx profile、react 和持久会话。

**P4 首轮已完成并停止在讨论检查点。** 2 次协议请求 + 1 次真实任务（24 次模型请求），没有补跑；最终补丁四项公开目标检查全部通过，既有 B 回归通过。运行本身因 `step_limit` 结束，保持 `success=false`；第 23 次才修改、第 24 次自测，不能说成修好后反复测试。含 probe 的峰值无缓存费用估算 0.426664 元，未核验账户账单，剩余额度不继续使用。详情见 [首轮结果](P4-FIRST-RUN-RESULTS.zh-CN.md)。

作者批准的首轮总预算为 5 元，主会话唯一启动。A/B 完成实现，Astra 独立审查并复核修正；主会话联合检查 91 passed（22.06s）。当前没有继续运行的模型任务或本轮容器，下一轮题量与改进点等待讨论。

公开基点实际复现两项连续构建失败，默认新鲜 EPUB 与显式启用对照通过；B 回归为 8 passed。Astra 发现候选读取器丢失会话声明配额的问题，修正后独立 26 项测试通过，原 2,097,156 字节复现解除，未提高配额。新检查身份 `586c07f4…` 的两条 02 预检保持 2 passed / 2 behavior_failed，输入清单不变且清理成功；旧 01 保留原身份，见 [P4-01/02 报告](P4-01-02-RESULTS.zh-CN.md)。

最终 [审查文件清单](evidence/p4-integration/reviewed-files.json) 共 17 项，主会话逐一核对无审查后变化，文件 SHA-256 为 `be4e9a9950c9afd04a6f7a61c82c5902b8fd834124473969c42874d3174d5867`。运行源码指纹 `7cbc503ff081b010268d47fb70bd07da135fcaf5ae89b19f86782c54f552b6ae`；本轮唯一产物根为 `D:/crq/artifacts/p4-sphinx8721-20261030-01/`。26 次真实请求的 usage 均完整、返回同一 fingerprint；186 条事件以唯一 run.finished 结束，无 pending，4 只本轮容器均确认不存在。原始输入、配置模板、模型轨迹、最终候选与目标检查结果均已落盘。

P4 分工（主树 `D:/crq/src/coding-harness`，不沿用早期 worktree）：

| 执行者 | 模型 | 当前任务 | 写入与执行边界 |
| --- | --- | --- | --- |
| 既有 A 会话 `01a18899-334d-7711-a875-80da957b1140` | 6.1 Sol / high | P4-01/02：公开目标检查、任务材料、基点复现和最终候选检查入口 | 新目标检查模块/脚本/fixture/测试/报告；独占本轮 Docker 预检，不改生产 B 契约或调用求解 API |
| 既有 B 会话 `01a18899-9ff6-7882-8256-9932c8b54330` | 6.1 Sol / high | P4-03：模型元数据、未知计费停机、专用配置与离线验证 | 必要 LLM/预算运行接线及对应测试；不碰目标检查材料、不运行 Docker 或求解 API |
| 主会话 | 当前模型 | 集成、预算、审查排期与唯一付费启动者 | 文档与集成；独立审查复用原 Astra 会话，新增跨范围修改先协调 |

所有人保持冻结 A5/A6/CR 原样，不 push。A/B 实现、Astra 审查与首轮轨迹分析均已完成；本轮余量不用于自动重跑、换模型或增题。

主会话统一调度和集成，基础层会话使用 6.1 Sol / xhigh 作为 P3 唯一生产代码写入者，Astra / high 完成独立范围检查与代码审查。复用原可见会话，没有重复创建自动 worktree；没有调用付费求解模型或启动修复率实验。

本轮范围复核：不因尚未证明修复率增益而回滚候选版本、工作区检索和公开验证模块；同样不把模块测试通过当成复杂度必要性的证明。A 冻结功能扩展，首个正常入口采用 BM25；B 接通当前补丁、公开验证、有界反馈和候选交付。保留现有可选编码器，未做完整仓库 embedding 性能实验；首版只支持 Sphinx，没有新增记忆平台、第二环境或 Docker 中断续跑。现有文件/命令工具通过薄适配共用新 coordinator，模型账本、session 和 ArtifactStore 均复用原机制。

本轮检查点是通过同一正常入口运行的完整流程和可核查交付物。第 07 次真实 Sphinx 演示成功，约 67.52 秒：基线 8 通过 → 故障候选 6 通过/2 失败 → 一次宿主反馈 → 修正候选 8 通过；6 次脚本回复、0 次求解模型 API 调用。脚本化故障与修复只能证明控制流程，不能作为模型自主修复或修复率收益证据。

接线前独立范围检查已完成：Astra 未发现应整块回滚的 P2 实现；后续聚焦单一编辑环境、统一完成控制、封存一致性、共享预算/事件及检索失败不可返回旧结果。主会话接线前运行旧 recovery/model_budget/execution_limits/config_dotenv 四个测试文件，37 项通过（14.16s），作为兼容基线。

P3 预算算术修正：P2 验证操作上限已是 162 秒，最终编辑树捕获另需最多 20 秒，因此新模式最终预留至少 182 秒，从同一任务总时限扣除。此项替代 P1 的拟议 162 秒组合值，不延长总期限或修改旧实验预算。

本轮机器蓝屏使执行和审查会话中断，作者授权后从保留的代码续做；中断前的 47 passed 只是恢复点。Docker Desktop 恢复后，01–04 初始化失败、05 演示语句导致导入错误、06 故障未被固定公开测试检出，均未算作闭环成功。01 的清理成功标记后来被延迟 create 证伪，精确 owner 残留已清理，并补上未知 create 不得报清理成功的修正；原始失败记录保留，详情见 P3 报告。

初稿审查的三项问题均已修正：业务预算/基线失败与 journal 故障区分，导出包含验证依赖闭包，初始化容量失败保留准确原因。Astra 最终复核 39 项关键测试通过，导出追加修正后另跑 3 项通过；这些集合有重叠，不累加。独立核验 63 条事件、4 次 capture、494 个 blob 及包内基线身份：无 pending、唯一 run.finished 位于最后，最终候选与验证/交付一致。主会话对 [审阅文件哈希清单](evidence/p3-integration/reviewed-files.json) 的 118 个文件逐一比对，无审查后变更；规范化清单 SHA-256 为 `ecc1a13b79a90fcc4605f8acb39644ca1ce59415c2a7c0974f8fd55795d3eb32`。

执行者冻结代码后的联合检查为 82 passed（22.91s），随后新增过期只读边界单独 1 passed（1.28s）；不是一次 83 项联合运行。主会话额外旧入口兼容组原为 75 passed、1 个新配置白名单失败；精确放行新 opt-in 配置后，该文件 7 项均通过并包含于上述联合检查。没有修改冻结 CR 配置/结果。主会话已核对最终报告，源代码指纹与执行者冻结清单相符；本轮全部仅本地交付，不 push。

| 可见会话 | 模型 | 任务与交付 | 状态 |
| --- | --- | --- | --- |
| Harness P2-A：工作区代码检索（含 P0 轨迹） | 6.1 Sol / high→xhigh | A 与 GPU smoke 完成；独立审查 B，修正后复审放行 | 当前任务完成 |
| Harness P2-B：公开验证服务（含 P0 环境） | 6.1 Sol / xhigh | 核心修正、生产 Docker executor、公开 profile 与 Sphinx 演示 | 已审查并合入 |
| Harness P1：接口设计与独立审查 | Astra / high | P3 范围、代码修正、transport 差异及 07 原始证据独立复核 | 已放行 |
| Harness 基础层：版本契约与捕获 | 6.1 Sol / xhigh | P3 factory/config/loop/session、工具与正常入口串行接线 | 已实现并审查通过 |

会话标识分别为 `01a18899-334d-7711-a875-80da957b1140`、`01a18899-9ff6-7882-8256-9932c8b54330`，主机均为 local，关联 coding-harness 项目。

P1 会话标识：`01a188a4-1ab1-7e31-9bdd-4335e2ffebf9`，主机 local。主会话保持统一调度；独立 Astra 会话承担接口设计与技术审查，没有并行接管项目指挥。

基础层会话标识：`01a188b5-63e8-78d0-b271-2f136db157a5`，主机 local。P2 独占主工作区新增 workspace 文件；本轮是 P3 共享运行时文件的唯一生产代码写入者，主会话只维护执行状态与集成。

共享基点：`d0bdf6f`，由独立 Astra 审查并实际运行 73 项相关测试通过；前序文档与 P0/P1 证据提交为 `15a040c`。全部仅本地提交，没有 push。A/B 从该共同基点独立开发。

自动 worktree 新会话请求 A=`client-new-thread:4ce76b70-65bd-46ac-b930-c5ab9631763f`、B=`client-new-thread:56d79215-479d-4ab3-b451-6c9081de8b01` 未启动：创建在主树扫描被忽略的 `AGENTS.override.md` 时卡住，两个 git 扫描持续超过 6 分钟后由主会话终止。未获得正式会话 ID，不把它们计为执行者。已完成的两个 P0 可见会话被复用并改名执行 A/B，避免重复创建与重复派工。

A 工作区：`C:/Users/Administrator/.codex/worktrees/57b9/coding-harness`；B 工作区：`C:/Users/Administrator/.codex/worktrees/8571/coding-harness`。均已核对从 `d0bdf6f` 开始且干净，根 AGENTS.md 已复制；工作区 src/tests/scripts 未发现额外 AGENTS。命令显式使用各自工作区，分支分别为 `codex/p2-workspace-retrieval`、`codex/p2-public-validation`。会话 UI 关联原项目；这次使用显式路径，未宣称自动工作区挂接已成功。

P0 两任务先只读并各写一份报告，再复用为独立模块开发。A/B 先跑纯 fixture，随后主会话分别排期授权已有 Qwen 的本地 GPU 小检查、已有 Sphinx 镜像的 Docker 验收。所有人没有调用求解模型 API。只有主会话负责合并；没有重复启动失败的 worktree 会话。

P0 关键结论：固定抽样18条，3条非空候选没有检查，1条只有展示探针，均为预算或上下文耗尽；5条自然完成都有测试通过正文。不能把它描述为正常结束经常漏测。现有数据支持补齐候选与检查的可核查关联，尚不支持修复率收益。Sphinx与pytest现有干净镜像各通过8项公开回归小检查；建议先Sphinx，再适配pytest。P1需补真正候选身份、捕获一致性及完成控制，并明确Docker中断恢复边界。

## 检查点之后

1. 作者可先查看第 07 次现成结果与导出包，或运行下面的统一入口；无需先读完整实现。
2. P4 首轮已按 [批准清单](P4-TASKS-AND-BUDGET.zh-CN.md) 完成，见 [结果与轨迹](P4-FIRST-RUN-RESULTS.zh-CN.md)。候选通过公开验收；没有主动检索或宿主失败反馈，本轮不能支持这两项收益。后续题量或同期对照另行决定。
3. Docker 中断续跑仍明确拒绝；只读 inspect/export 已实现，可核验过期/中断会话的已提交证据，不恢复未知执行。
4. 尚未验收 pytest 4.5 第二适配、完整仓库 hybrid 性能或修复率增益。P4 已观察到真实模型使用命令、目录与编辑工具，尚未观察到它主动调用 retrieve_code/validate_patch；普通入口仍采用 BM25，后续不自动扩展范围。
5. 本检查点只支持工程机制结论，不据此修改简历中的修复效果数字。

代码起点：`76388a59116c415267f8923a14c28c1104a89ef6`。原实验产物及冻结文件保持原样。

## 已验收的本地提交与证据

- 捕获基础 `3cf237d`：完整真实候选、静止屏障、blob 发布和 fresh replica 物化；[报告](P2-FOUNDATION-RESULTS.zh-CN.md)。原 84 项局部测试与 14 个不同真实 Docker 场景分批通过。
- Astra 审查复现了锁获取与取消同时完成的泄漏、普通命令被错误限制为 30 秒。修正 `ceedd05` 后 97 项局部测试通过；Astra 独立复跑新增 13 项并解除两项阻断。无透明 Docker 中断恢复或抗恶意执行保证。
- A 实现 `f4873b5`，报告 `51bac37`，真实 GPU 证据 `f05db7b`；[报告](P2-A-RESULTS.zh-CN.md)。243 项确定性/旧 CR 兼容检查通过，Astra 对固定代码定向复现后放行。原 worktree 提交对应为 `38efd6c`、`89159d3`、`0f6b7a6`。
- 真实编码器是已有 Qwen 0.6B，**不是旧 CR 冻结的 Jina**；本地 RTX 2080 Ti/FP16 的机制 smoke 通过（加载约 148s、首次小批编码约 42s），C0/C1 与缓存复用证据见报告及 JSON。没有检索质量或修复率新证据。
- B 核心在主树为 `0360808`、修正为 `6d2fc1d`（worktree 对应 `c72d7c7`、`9c5096e`）；[报告](P2-B-RESULTS.zh-CN.md)。初版 141 项检查通过（68 新增、73 共享），使用本机 pytest 9.1.1 fixture。
- B 独立审查复现并修复三项：基线必须来自已提交且证据完整的执行；相同 operation 重放也须核验当前资格与证据；完成及证据发布后检查单次 request 期限。新增 11 项回归在旧代码全部失败，修正后相关 47 项通过；原审查者独立跑 15 项定向回归及最小复现，解除阻断。没有通过修改证据标准来放行。
- 首次真实捕获→检索演示 `6645182`：完整 Sphinx 1,480 文件，534 个 Python 文件、7,116 chunks；不变复用、新增/修改命中当前 SHA 与行号、删除不残留均通过，约 31 秒，owned 容器清理确认。入口为 `scripts/demo_workspace_retrieval.py`，没有运行验证服务或 agent 循环。
- 主会话在合并后 `6d2fc1d` 跑联合检查：新契约/捕获/检索/编码器/验证服务与修正回归、旧检索纯函数/隔离/CR-2 serve，共 **351 passed、1 skipped，14.43s**。唯一 skip 是显式关闭的真实 Docker 检索演示，已另有真实运行证据；未跑整个仓库测试。与原代码起点比较，现有 src/tests/configs/scripts 文件没有修改，新增能力仍未接入默认路径。
- Sphinx Docker 验证适配 `1caf4fc`（worktree `b8f4abb`）：真实基线 8 通过、合法候选 8 通过、人工源码故障 7 通过/1 失败、恢复候选 8 通过；同版本复用不重测、冻结测试修改不创建副本，4 个副本及编辑容器清理确认。覆盖为 `regression_only`，独立评分 `not_evaluated`、`solved=null`，不代表修复 Sphinx-8721。
- Astra 对该适配器审查通过：核对 13 份唯一原始证据的长度/SHA256、四份提交报告与原始 runner 报告、runner 字节、前后 manifest、实际环境和复用记录。没有重复跑四副本演示。主会话合入后另跑 13 项适配器测试通过（0.57s），CLI 帮助可用；查询本轮 owned label 的容器列表为空。

上述测试集合有重叠，不将数字直接相加。所有提交仅本地，未 push；无求解模型调用、无新付费修复实验。

## 从这里上手

P3 优先入口（在仓库根目录的 PowerShell 中）：

```powershell
$harnessP3Output = Join-Path $env:TEMP ('harness-p3-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe scripts/run_workspace.py --scripted-demo --output-dir $harnessP3Output
.\.venv\Scripts\python.exe scripts/run_workspace.py --inspect (Join-Path $harnessP3Output 'session')
```

现成结果位于 `D:/crq/artifacts/p3-sphinx-20261030-07/result.json`，完整导出在同目录 `delivery/`。查看 `scripted_sequence_complete`、三个验证 record、候选身份和 `regression_only` 覆盖范围；普通任务接入和只读 export 命令见 P3 报告。需要现有固定镜像；脚本演示不调用模型服务。

以下保留 P2 的两条独立模块演示：

在 `D:/crq/src/coding-harness` 的 PowerShell 中，使用现有 `.venv` 和 `desktop-linux` 的固定 Sphinx 镜像：

```powershell
$harnessP2DemoRoot = Join-Path $env:TEMP ('harness-p2-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe scripts/demo_workspace_retrieval.py --output-dir (Join-Path $harnessP2DemoRoot 'retrieval')
.\.venv\Scripts\python.exe scripts/demo_public_validation.py --output (Join-Path $harnessP2DemoRoot 'validation')
```

两条命令都拒绝覆盖已有输出目录。它们使用已准备好的镜像，缺镜像或 Docker 不可用会失败，不会自动下载、付费调用或安装依赖。

1. 检索演示先看 `retrieval/result.json` 的五个阶段：基础、不变、新增、修改、删除。沿 `capture_id/candidate_id/corpus_id/index_id` 看变化，再看命中位置与 `file_sha256`；全部正文和捕获清单在该输出目录的 `artifacts/`。
2. 验证演示先看 `validation/summary.json`，再看同目录每个阶段的记录和 `events.jsonl`。对照合法候选、故障候选、恢复候选各自的 `candidate_id`、节点报告与计数，确认复用没有新增执行。
3. 不运行也可查看已提交的小证据：[真实检索](evidence/p2-workspace/retrieval-docker-01.json)、[公开验证摘要](evidence/p2-validation/sphinx-public-run.json)、[四次结构化测试收据](evidence/p2-validation/sphinx-public-receipts.json)、[Qwen GPU 小检查](evidence/p2-a/qwen-gpu-smoke-01.json)。

上述两条 P2 脚本仍是独立模块演示；P3 统一入口已经接入主循环和完成控制。两者均不能替代真实模型任务的使用性与效果检验。

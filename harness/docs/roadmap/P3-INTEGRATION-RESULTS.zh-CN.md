# P3：正常入口、完成控制与候选交付

日期：2026-10-30。接线基点：`5e33b40`。Astra 已独立复审放行生产代码及 07 机制演示；本报告文字由主会话核对，不替代其最终集成验收。

P3 已通过现有 factory、AgentLoop、RunSession 跑通真实 Sphinx 的“检索/读取 → 人工故障 → 公开测试失败 → 宿主反馈 → 脚本修正 → 新候选通过 → 导出”。验收证据是第 **07** 次运行；第 06 次虽然回归绿色，但没有失败或反馈，不能作为完整闭环证据。没有调用求解模型 API、使用 GPU、下载模型或拉取镜像。

## 实现范围与预算

- `workspace.enabled` 默认关闭。首版仅支持 react + RunSession + 固定 Sphinx Docker profile + BM25。拒绝注入旧执行后端、CR 检索、子代理或付费辅助上下文循环。旧配置和冻结 CR 文件未改。
- 编辑容器只有一个 WorkspaceCoordinator。原文件/命令工具通过薄 ExecutionRequest adapter 使用它；capture、检索更新和封存也使用同一 coordinator。公开验证继续复用 P2 的独立副本，不回写编辑容器。
- `retrieve_code` 每次先捕获当前树，再更新索引、搜索；捕获失败不会返回旧索引结果。结果带 capture/candidate/corpus/index 身份及文件 SHA、符号和行号。`validate_patch` 参数固定为 `{}`，选择器、环境和 runner 由宿主冻结。
- 沿用原 step 限额、ModelBudgetLedger、绝对期限、输出计量和 ArtifactStore。基线 1 次单列；候选最多 3 次，最后一个槽拒绝主动工具占用；自动反馈最多 2 次，同一 record 只交付一次观察。反馈是普通 `role=user` 消息，继续原循环，不伪造 tool_call。
- 最终预留 **182 秒 = 捕获 20 秒 + P2 验证操作 162 秒**，从原总时限 600 秒扣除。P2 各阶段上限未缩短。编辑容器启动与首次 seed 分别有最多 60 秒的初始化期限，同时受 agent_deadline 限制，消耗普通时间；后续及最终捕获仍为 20 秒。
- 原 session 总产物上限仍为 64 MiB；新入口显式声明单件 8 MiB、最终产物预留 16 MiB。输出总限 16 MiB、最终预留 4 MiB。原生发布、journal spill、command staging 都计入同一存储配额；反馈文本必须完全容纳于普通输出余额。
- 模型/上下文/step/普通预算耗尽时，条件满足则用原预留做宿主结算，不再调用模型。基线未成功绑定时不启动候选验证。取消或总期限到达后不开始新 capture、测试或模型调用，只做有界清理；没有可靠最终 capture 就标记 `final_candidate_unknown`。
- 封存后禁止编辑；不走旧 snapshot 覆盖候选。持久产物先于引用事件，最终清理后只写一次 `run.finished`，最后才关闭生命周期事件输出。run、delivery、development_validation 与 independent_evaluation 分开表达。

普通容量拒绝使用明确的 ArtifactQuotaError，运行级预算/基线拒绝使用 WorkspaceStop；真正的 StoreError/OSError 持久化故障仍停止新外部操作。没有按异常文本猜测错误类别。seal/record 提交失败不会跳过编辑容器清理。

Docker 中断续跑明确拒绝。inspect/export 只读取已经提交并通过哈希校验的产物，不续跑 pending 操作，不校验执行用的 expires_at。导出带最终 delta、capture、全部已提交 capture/record 的引用与原始 JSON、spec、environment、baseline binding、基线记录及相关证据。完整基树的重新物化仍依赖固定镜像和 revision，导出不是整个镜像的备份。

## 离线验证

冻结生产代码后的联合检查：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_workspace_completion.py tests/test_workspace_capture_transport.py tests/test_recovery.py tests/test_model_budget.py tests/test_execution_limits.py tests/test_config_dotenv.py tests/test_cr2_harness.py -q -x
```

结果：**82 passed / 22.91s**。随后新增的过期只读边界单独运行：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_workspace_completion.py::test_expired_terminal_session_still_allows_readonly_inspection_and_export -q -x
```

结果：**1 passed / 1.28s**。这两次共覆盖 83 个不同用例；没有把它写成一次 83 项联合运行。

完成控制文件目前有 31 个用例，覆盖失败反馈修正、主动验证复用、最后槽位、两轮反馈、同 record 去重、模型调用/token/context/step/软期限硬停、用户/异步取消、总期限、旧索引不可复用、普通配额耗尽仍结算、早期容量拒绝、失败基线、record/seal 崩溃窗口、编辑/副本清理失败、证据损坏、导出依赖链、过期只读入口及生命周期事件收尾。transport 的 8 个用例含真实本地子进程重收：helper 可超过管理 RPC 内部上限，但仍受调用方 deadline/cancel 控制；不确定的延迟 create 不能凭一次“未找到”就宣称清理完成。

主会话另跑旧 agent_loop/cancellation/timeout/event_store/snapshot_on_stop/CR/H4/tool 兼容组，初次为 75 passed、1 failed；失败是旧测试全扫配置时未允许新建的显式 workspace 配置。已仅对白名单中的这一个新文件增加声明检查，保留所有旧配置断言；对应 `test_cr2_harness.py` 的 7 项已通过，并纳入上面的联合检查。未修改旧 CR 配置来迁就新入口。

## 真实演示 07

命令：

```powershell
.venv/Scripts/python.exe scripts/run_workspace.py --scripted-demo --output-dir D:/crq/artifacts/p3-sphinx-20261030-07
```

固定环境：Sphinx revision `82ef497a8c88f0f6e50d84520e7276bfbf65025d`；镜像 `coding-harness-a5-clean@sha256:1a6e20fb4b37194f8596111bc3658261cc40dbd85dc09a0e5be6d6690220b4e2`；Python 3.9.25 / pytest 8.4.2；冻结公开选择器 `tests/test_util.py`。覆盖为 **regression_only**。

运行 ID：`33adb015fa1849eaa4989b95a64d5f7f`。run 耗时 **67.523 秒**，包含初始化与清理；此数不包含结束后的只读导出检查。六次模型接口调用均为宿主编写的有限脚本回复，仍按正常调用消耗同一 ModelBudgetLedger；外部模型调用为零。检索回执为 BM25、534 个 Python 文件、7116 个 chunk、encoded=0。

| 顺序 | 真实公开结果 | 候选身份前缀 | 作用 |
| --- | --- | --- | --- |
| 基线 | 8 passed | `70e83bcd…` | 绑定冻结检查 |
| 人工故障 | 6 passed / 2 failed | `d5968a09…` | `behavior_failed/test_failure`，触发一次宿主反馈 |
| 脚本修正 | 8 passed | `8a58fbf0…` | 与故障候选不同；最终导出绑定此记录 |

脚本通过注册工具读取 `display_chunk` 的当前源码，再将其返回行为替换为人工故障。收到真实公开失败反馈后恢复原语句并留下演示注释，保证最终 delta 非空。基线及两次候选执行均为真实新副本，cleanup_state 均为 cleaned。ledger 为基线 1 / 候选 2 / 反馈 1；pending model/tool/validation 为空；`run.finished` 唯一且为最后一个持久事件。

最终候选：`candidate-v1:8a58fbf0ef47f4b99fa69eb7af8d19b467c33439f55e1cda13dfac677b352a30`。

最终记录：`validation-record-v1:9acf3341ca1a695cbb7569ab3bb2eabae8fb92b45d8b939430a33226b01188b3`。

最终修改文件：`sphinx/util/__init__.py`，SHA-256 `c95de6e887fe1c3d3021cba662b6c19b814865c6982b5c9998cb71ff9f29aaf0`。最终结果为 run=completed、delivery=exported、development_validation=passed；independent_evaluation 始终为 `not_evaluated`，`solved=null`。

原始证据目录：`D:/crq/artifacts/p3-sphinx-20261030-07/`：

- `result.json`、`session/events.sqlite3`、`session/artifacts/`：原始执行与持久证据，未覆写。
- `delivery/`：运行结束时导出；`cli-export/`：冻结代码通过正常 `--export` 入口再次纯只读导出，包含完整引用索引。
- `acceptance-audit.json`、`evidence-audit.json`：对原始事件链的检查与七次运行摘要。
- `final-review-audit.json`：冻结代码的演示验收检查全部为 true；另核验 10 个顶层引用，覆盖 4 个 capture、3 个 record 和 3 个基线依赖。嵌套 blob/evidence 在导出前逐一校验。
- `inspect-cli.json`、`export-cli.json`：正常只读 CLI 输出。

当前 `--scripted-demo` 只有基线通过、候选真实失败、一次普通用户反馈、不同候选通过、冻结 binding 一致、最终导出绑定、无 pending、六次脚本调用、唯一 finished 和清理成功全部满足时才视为成功。普通真实任务无需先失败，CLI 仍按该任务的验证与导出状态退出。

## 失败尝试与诊断记录

每次都使用新的输出目录和 session，没有恢复旧 Docker session。以下 `01` 等均指 `D:/crq/artifacts/p3-sphinx-20261030-NN/result.json`，原记录全部保留。

| 尝试 | run 秒数 | 已提交 capture / 脚本调用 | 实际结果 |
| --- | ---: | --- | --- |
| 01 | 21.486 | 0 / 0 | 启动误用捕获 20 秒上限，启动阶段退出；原 cleanup_ok 另有下述更正 |
| 02 | 62.849 | 0 / 0 | 启动约 30 秒后进入首次 seed，seed 的 20 秒捕获期限耗尽 |
| 03 | 50.470 | 0 / 0 | 首次 seed 改用初始化期限，但 helper 被内部管理 RPC 的 30 秒上限截断；清理未确认 |
| 04 | 108.735 | 0 / 0 | helper 服从调用方期限后，仍在 60 秒首次 seed 期限内未完成；停止完整演示重跑并做分段诊断 |
| 05 | 50.424 | 3 / 4 | 基线 8 passed；演示脚本仅替换多行 return 的第一行，导致候选导入失败，准确记录 inconclusive/environment_error；无自动反馈 |
| 06 | 47.955 | 3 / 4 | 修正语法后的 docname_join 故障未被所选公开测试检出；候选 8 passed，但反馈为 0，未形成失败修正闭环 |
| 07 | 67.523 | 4 / 6 | 使用所选公开测试实际覆盖的 display_chunk；完成上述真实失败反馈修正链 |

**01 的原清理声明被证伪，不能沿用。** 原摘要记录 cleanup_ok=true，但 Docker daemon 后来完成了已超时的 create，出现 Created 容器。精确 owner 为 `147c957ceb19459f85b1afdb7617d507`，容器 ID 为 `1e701b1c289cd6ba2c3726f030c92a8d4e382a9b1b8c64034f5b29caab0ae94e`。核验匹配标签后按该 ID 移除；未扫杀其它容器。补充更正在 `01/cleanup-correction.json`，原 result/journal 未改。之后确认列表为空再继续运行。最终再次核查未发现 workspace-v1 残留。

对此增加 create_pending 状态：超时的 Docker create 不能靠 CLI 进程已退出或一次名字缺失就认定 daemon 请求已结束；只在调用方清理宽限内等待/移除精确 owned 容器，无法确认就保留 cleanup_failed。该修改与 helper deadline 修改均在 `src/workspace/docker_transport.py`，需要纳入本轮基础层复审。

诊断没有扩大正式演示总时限，也未改变持久化策略：

| 诊断 JSON（均在 `D:/crq/artifacts/`） | 实测 |
| --- | --- |
| `p3-seed-diagnostic.json` | 启动 16.290s；源 metadata 检查 15.191s；复制阶段触发 30s 期限，含清理观测为 34.482s；清理成功 |
| `p3-exec-diagnostic.json` | 启动 1.259s；短 exec 0.130s；大 helper 定义加 metadata/单文件复制总 0.173s；容器内 import 0.016s、inventory 0.033s、单文件复制 0.00018s；清理成功 |
| `p3-seed-diagnostic-recheck.json` | 同上限复查：启动 1.331s、inventory 0.217s、复制 0.540s、完整双 scan/hash/静止检查 0.333s；1480 entries、534 blobs、3970374 corpus bytes；清理成功 |

这些结果证实了阶段性能的显著波动。复查没有扩大扫描范围；失败 01–04 均尚未进入宿主 blob 发布，不能将其超时归因于该阶段。没有据此确定 Docker/WSL、内存压力或重启后的某一个系统根因，也没有更改排除目录或降低一致性检查来放行。

## 冻结代码与交接

生产文件共 14 个：

```text
src/config/schema.py
src/harness/factory.py
src/runtime/loop.py
src/runtime/recovery.py
src/runtime/session.py
src/tools/container_files.py
src/tracing/artifacts.py
src/workspace/docker_transport.py
src/execution/workspace.py
src/runtime/completion.py
src/runtime/workspace_io.py
src/tools/workspace.py
scripts/run_workspace.py
configs/workspace/sphinx-bm25.yaml
```

本会话新增文件共 8 个：上表中的 `src/execution/workspace.py`、`src/runtime/completion.py`、`src/runtime/workspace_io.py`、`src/tools/workspace.py`、`scripts/run_workspace.py`、`configs/workspace/sphinx-bm25.yaml`，以及 `tests/test_workspace_completion.py` 和本报告。上表其余 8 个生产文件为修改。

其余修改文件共 3 个：`tests/test_workspace_capture_transport.py`、`tests/test_cr2_harness.py`、`docs/roadmap/P1-INTERFACES.zh-CN.md`。P1 仅添加主会话在已授权范围内复核决定的 182 秒算术修正。合计本会话交付 19 个文件；`docs/roadmap/EXECUTION-STATUS.zh-CN.md` 和 `docs/INDEX.md` 属主会话维护，不计入本会话改动。

逐文件 SHA 清单在 `D:/crq/artifacts/p3-production-freeze.json`。冻结 source_fingerprint 为 `f8998f4ff70dccf6763ccb3138e852ed0df0f9c63423de94aaf937430cc6df8c`。

07 原始 manifest 的 code_id 为 `748b5217aca4fbd0f425fb5ea5d6ce4ef53d19459c1cb1fad0b1577bc8150ae2`。演示后仅补了反馈文本字节余额检查、只读导出的原始 JSON/引用索引及脚本演示的完整验收断言；相应离线回归和对 07 原始事件链的只读核验已通过，没有再跑一场完整 Docker 演示。不得把 07 描述为冻结后字节完全相同代码的重新运行。

独立复审还实际运行了 39 项关键检查及导出追加后的 3 项检查，并独立核验 07 的 63 个事件、4 个 capture、494 个 blob 和包内 binding；最后的只读导出追加也已补审放行。这些是复审运行，未混入上面的 83 个不同用例计数。

这证明已支持范围内的控制流和候选/检查关联。它不证明模型自主修复、检索质量或修复率收益；未认证目标 issue 已解决。pytest 第二适配、GPU/hybrid 性能、付费对照、记忆平台、Docker 中断续跑和更强抗恶意执行仍不在此次交付范围。本会话没有 commit 或 push；交由主会话核对报告后集成。

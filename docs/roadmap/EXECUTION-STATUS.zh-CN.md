# 下一阶段执行状态

更新：2026-10-30。依据：[纲领](HARNESS-NEXT-CHARTER.zh-CN.md)、[阶段方案](HARNESS-NEXT-PLAN.zh-CN.md)。

作者已审阅方案并授权开始分派和推进，优先使用可见会话；主会话负责协调、审查和集成。实质性方向选择或需要作者审阅的阶段检查点再停下讨论。

## 当前阶段

**已到 P2 阶段检查点：首批工作区检索、真实候选捕获、公开验证服务和 Sphinx Docker 适配均已完成独立审查并合入本地 main。两条真实模块演示可重复运行。** 功能代码最终集成提交为 `1caf4fc`；本页是之后的状态整理。

主会话仍统一调度和集成，Astra 负责独立审查，无需更换项目主持人。按作者授权停在这一可检查节点；所有执行会话本轮任务已结束。P3 的主循环、工具、全退出完成控制和 session 接线尚未开始，当前不是完整求解 agent 的新入口。没有启动付费求解模型评测。

| 可见会话 | 模型 | 任务与交付 | 状态 |
| --- | --- | --- | --- |
| Harness P2-A：工作区代码检索（含 P0 轨迹） | 6.1 Sol / high→xhigh | A 与 GPU smoke 完成；独立审查 B，修正后复审放行 | 当前任务完成 |
| Harness P2-B：公开验证服务（含 P0 环境） | 6.1 Sol / xhigh | 核心修正、生产 Docker executor、公开 profile 与 Sphinx 演示 | 已审查并合入 |
| Harness P1：接口设计与独立审查 | Astra / high | [接口契约](P1-INTERFACES.zh-CN.md)，捕获、A、Docker 验证适配及修正的独立审查 | 本轮审查完成 |
| Harness 基础层：版本契约与捕获 | 6.1 Sol / xhigh | 捕获、物化与审查修正；真实捕获→BM25 检索演示 | 已审查并合入 |

会话标识分别为 `01a18899-334d-7711-a875-80da957b1140`、`01a18899-9ff6-7882-8256-9932c8b54330`，主机均为 local，关联 coding-harness 项目。

P1 会话标识：`01a188a4-1ab1-7e31-9bdd-4335e2ffebf9`，主机 local。主会话保持统一调度；独立 Astra 会话承担接口设计与技术审查，没有并行接管项目指挥。

基础层会话标识：`01a188b5-63e8-78d0-b271-2f136db157a5`，主机 local。本轮独占主工作区新增 workspace 捕获、协调、物化及对应测试，没有修改已有执行/运行时文件。

共享基点：`d0bdf6f`，由独立 Astra 审查并实际运行 73 项相关测试通过；前序文档与 P0/P1 证据提交为 `15a040c`。全部仅本地提交，没有 push。A/B 从该共同基点独立开发。

自动 worktree 新会话请求 A=`client-new-thread:4ce76b70-65bd-46ac-b930-c5ab9631763f`、B=`client-new-thread:56d79215-479d-4ab3-b451-6c9081de8b01` 未启动：创建在主树扫描被忽略的 `AGENTS.override.md` 时卡住，两个 git 扫描持续超过 6 分钟后由主会话终止。未获得正式会话 ID，不把它们计为执行者。已完成的两个 P0 可见会话被复用并改名执行 A/B，避免重复创建与重复派工。

A 工作区：`C:/Users/Administrator/.codex/worktrees/57b9/coding-harness`；B 工作区：`C:/Users/Administrator/.codex/worktrees/8571/coding-harness`。均已核对从 `d0bdf6f` 开始且干净，根 AGENTS.md 已复制；工作区 src/tests/scripts 未发现额外 AGENTS。命令显式使用各自工作区，分支分别为 `codex/p2-workspace-retrieval`、`codex/p2-public-validation`。会话 UI 关联原项目；这次使用显式路径，未宣称自动工作区挂接已成功。

P0 两任务先只读并各写一份报告，再复用为独立模块开发。A/B 先跑纯 fixture，随后主会话分别排期授权已有 Qwen 的本地 GPU 小检查、已有 Sphinx 镜像的 Docker 验收。所有人没有调用求解模型 API。只有主会话负责合并；没有重复启动失败的 worktree 会话。

P0 关键结论：固定抽样18条，3条非空候选没有检查，1条只有展示探针，均为预算或上下文耗尽；5条自然完成都有测试通过正文。不能把它描述为正常结束经常漏测。现有数据支持补齐候选与检查的可核查关联，尚不支持修复率收益。Sphinx与pytest现有干净镜像各通过8项公开回归小检查；建议先Sphinx，再适配pytest。P1需补真正候选身份、捕获一致性及完成控制，并明确Docker中断恢复边界。

## 检查点之后

1. 作者可先运行下面两条演示，并从摘要打开候选版本、测试收据与事件，检查功能是否符合预期；无需先读整个实现。
2. 下一项是 P3 串行接线：一个负责人统一改 factory/config/loop/session，接入当前工作区检索与 validate_patch、自然结束检查、硬停止结算和时间/产物预留。新模式默认关闭，旧路径保持兼容。
3. 首版 Docker 中断续跑继续明确拒绝。P3 的只读 inspect/export 与恢复事件接线尚未实现；当前磁盘产物可检查不等于已完成恢复入口。
4. 尚未验收 pytest 4.5 第二适配、完整 Sphinx 语料的真实 hybrid 性能、求解模型主动使用工具或修复率收益。当前 Qwen 冷启动较慢，普通入口采用何种默认检索配置应结合后续使用检查决定。
5. 付费小对照及更大的受支持范围继续留到机制/使用性完成之后；本检查点不据模块通过修改简历的效果数字。

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

这里的两条链路由脚本驱动，没有求解模型、自动修改决策或完成 gate。它们是进入 P3 前可独立复核的模块成果。

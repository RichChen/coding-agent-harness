# P2 共享捕获与物化基础层结果

日期：2026-10-30（Asia/Shanghai）。依赖纯契约基点 `d0bdf6f`；集成与审查状态见 执行状态。范围为 P1 第 2、3、5.2、9–11 节的基础机制，不代表 P3 全链路或公开验证服务完成。

## 1. 交付与调用方式

新增八个模块，未修改 `contracts.py`、原执行后端、factory、runtime/session 或旧实验文件：

| 文件 | 职责 |
| --- | --- |
| `src/workspace/control.py` | `CaptureError(reason, evidence=...)`；绝对单调期限/取消/配额 `CaptureControl` |
| `src/workspace/policy.py` | frozen `CapturePolicy`；完整配置产生 `rules_id`；候选可修改范围判断 |
| `src/workspace/docker_transport.py` | `DockerWorkspace`、`CommandResult`；独立、受限的 Docker CLI，唯一 owned 名称/标签 |
| `src/workspace/coordinator.py` | `WorkspaceCoordinator`；启动、编辑、捕获、物化、关闭共用一个锁 |
| `src/workspace/helper.py` | 固定宿主代码，通过绝对 Python `-I -S -c` 运行；唯一容器扫描器 |
| `src/workspace/blobs.py` | `WorkspaceBlobStore`；受控键、长度/SHA 校验、fsync/原子发布、磁盘配额 |
| `src/workspace/capture.py` | `CaptureProvider`、协议解析；核验后发布完整捕获 |
| `src/workspace/materialize.py` | `materialize`；同镜像新副本 base+delta 后完整清单复算 |

核心 API：

```python
CaptureControl(operation_id, deadline, cancel=None,
               wire_limit=32 * 1024 * 1024, artifact_limit=16 * 1024 * 1024)
# deadline 是 time.monotonic() 绝对值；cancel 是可选 asyncio.Event。

DockerWorkspace(image=..., context="desktop-linux", python="/usr/local/bin/python",
                cpus="1", memory_bytes=512 * 1024 * 1024,
                workspace_bytes=128 * 1024 * 1024, tmp_bytes=32 * 1024 * 1024,
                pids_limit=64, docker_bin="docker")
CommandResult(exit_code: int, stdout: bytes, stderr: bytes)

WorkspaceCoordinator(transport)
await coordinator.start(control)
await coordinator.execute(argv, control, stdin=None, env=None)  # -> CommandResult
await coordinator.close(control=None)  # -> bool；确认 owned 容器已移除

WorkspaceBlobStore(root, limit_bytes=128 * 1024 * 1024)
store.put(data)                  # -> ArtifactRef
store.read(ref)                  # -> bytes，校验受控键/长度/hash
store.commit(capture)            # -> 已原子提交的 capture JSON 路径

CapturePolicy(editable_root="sphinx", ...)
CaptureProvider(coordinator, store, base_revision=..., policy=None)
await provider.capture(control) # -> WorkspaceCapture
provider.base_entries            # 只读基础 FileEntry tuple
provider.policy                  # frozen policy
provider.last_capture            # 最近成功发布的捕获，失败不会替换

await materialize(capture, reader, replica_provider, control)  # -> tuple[FileEntry, ...]
```

`capture` 的第一次调用在已启动、尚未编辑的 owned 容器中从 `/opt/repo` seed，再读取真实 `/workspace`；后续 delta 始终相对该基础清单。先初始化再开放模型工具是集成方责任。不提供宿主任意目录捕获，也没有生产 MemoryCapture 替身。

物化调用方应先启动独立 coordinator，构造尚未捕获过的 `replica_provider`，并在 `finally` 关闭该 coordinator。函数验证输入 capture 的所有 blob，捕获副本真实基础树，核对 image digest、实际 image ID、revision/rules/base manifest，重新检查 admissibility，清理生成缓存，应用删改，再复算完整清单。只有与原 `capture.manifest_id` 相同才返回；不启动测试。返回清单绑定调用方传入的 capture；`replica_provider.last_capture` 此时仍是副本基础捕获历史，不是候选的验证记录。

## 2. 一致性、失败和配额

- `WorkspaceCoordinator` 的锁覆盖所有受支持操作；工具适配器必须调用 coordinator，不能直接调用 transport 或外部 `docker exec`。原后端不自动受这个锁保护；接线未实施。
- 创建时登记唯一名称/标签，并固定 PID1 为绝对 Python 单线程 sleep。helper 直接读取 `/proc/*/stat` 的 PID/starttime 和 task 线程，核对 PID1 完整 argv 与登记 starttime，只放行 PID1 和自身；在扫描前、中、后重复核对。
- 元数据预检先遍历全树，包括排除子树；非法路径、大小写碰撞、私有路径、symlink、特殊文件直接拒绝。未完成全树检查前不输出正文。普通文件通过 dir-fd、`O_NOFOLLOW`、lstat 等价检查、open/fstat 前后比对读取；前后全树元数据和每个受检文件原始 SHA 都必须相同。
- helper 完成双扫描后输出 JSONL：逐条 manifest、48 KiB 原始 blob 分块和完成 footer。宿主检查 offset、总配额、完整文件集合、长度/hash、进程 receipt、完整 manifest 身份；没有 footer 不发布 capture。输入也按 64 KiB 写入 Docker stdin；不使用旧 5 MiB export 路径。
- 默认候选 200 文件/4 MiB；完整受检树 10,000 文件/64 MiB；Python 8 MiB，单文件 1 MiB；元数据遍历另限 100,000 节点。所有限额与路径/排除/拒绝/mode/辅助程序版本规则进入 `rules_id`。Python 超单文件限额明确记录排除，A 负责编码/AST 语义处理。
- capture 在实际调用共享 `validate_against_base`、`validate_blobs` 并落盘全部 blob 后，才原子提交 capture JSON；发布前失败可留下无引用 blob，绝不会替换 `last_capture` 或发布半份 capture。store 有独立总磁盘配额。
- 未支持的普通文件或 mode 修改保存真实完整身份和 delta，`admissible=False`；后台进程、捕获不完整、超限、非法类型/路径等抛稳定 `CaptureError.reason`，不伪造空 delta。
- 管理 RPC 的等待保留 `min(30 秒, 剩余绝对期限)` 上限；普通命令/公开检查及协调锁等待使用调用者的剩余绝对期限，不再暗中截断为 30 秒（修正见 §6）。支持取消事件和 asyncio cancellation。在途容器操作超时/取消/状态未知使会话 failed，结算 CLI 后清理整个 owned 容器。尚未进入操作的等待者被取消时不操作容器，也不释放其他操作持有的锁。仅清理可在取消/总期限后继续，使用独立的最多 10 秒窗口；失败明确保留 `cleanup_ok=False`。不尝试杀后台进程后继续求解。
- 清理核对唯一标签和精确容器 ID，移除后再次查询确认；不扫描或删除其他会话的容器。不会拉取镜像或挂载宿主目录。

这是 P1 定义的受控静止点机制，不是面对蓄意规避 `/proc` 或篡改解释器的恶意程序的形式化快照保证。身份描述 regular-file 验证视图；不声称复现编辑容器缓存、`/tmp` 或任意后台状态。

## 3. 已运行的验证

宿主 `.venv`：Python 3.13.9 / pytest 9.1.1；容器使用已有 Sphinx digest，Python 3.9.25。无依赖安装、无镜像拉取、无 solver API、无仓库修复实验，未调用公开 pytest runner。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_workspace_contracts.py tests/test_workspace_capture.py -q
# 84 passed

$env:WORKSPACE_DOCKER_TESTS='1'
.venv/Scripts/python.exe -m pytest tests/test_workspace_capture_docker.py -v -rP -x
# 当时的 11 项全部通过：11 passed in 213.91s；增补后共 14 个不同场景，分批结果见 §5。
```

首轮真实机制检查：

| 场景 | 实际结果 |
| --- | --- |
| 初始捕获、合法源码修改、fresh replica 物化 | 通过；两份完整清单逐项相同 |
| 改冻结 `tests/test_util.py`；改变已有源码 mode | 保留真实新 candidate，均 inadmissible |
| CLI 已结束，后台 sleeper/持续 writer/派生子进程仍在 | 均 `workspace_not_quiescent`，拒绝发布并清理 |
| symlink、FIFO、`.env-secret`、反斜杠路径、大小写碰撞 | 均拒绝；没有新增 capture 或传播正文 blob |
| 已有源码 chmod 000 导致读取失败 | `capture_io_error`，拒绝发布并清理 |
| 恰好 4 MiB 改动；再加 1 字节 | 前者完整分块捕获，后者 `delta_byte_limit`；保留此前捕获 |
| 恰好 200 文件；第 201 文件 | 前者捕获，后者 `delta_file_limit`；保留此前捕获 |

4 MiB 的 Base64 正文本身大于 5 MiB；这个边界实际经过新分块路径，未复用旧导出上限。

Sphinx 初始实际证据：

```text
image_digest = sha256:1a6e20fb4b37194f8596111bc3658261cc40dbd85dc09a0e5be6d6690220b4e2
base_revision = 82ef497a8c88f0f6e50d84520e7276bfbf65025d
base_manifest_id = tree-v1:58a43a82c35f2f1ec34a83629d87a7562c282eb44ce7a671ab24beaff4b54928
regular_files = 1480
tree_bytes = 16531532
python_files = 534
python_bytes = 3970374
probe_manifest_id = tree-v1:309bf4af6322611e7ec9a3145f9c48de50807ffdca5e78df9b4b996f11b214d3
probe_candidate_id = candidate-v1:379898cb7965d071d5a8bc8912134eca6a844b5f6f858d9e2dc0cec77810d0f0
```

正文为机制用注释追加，不构成 Sphinx 问题修复或公开测试通过证据。测试产物位于 pytest 临时目录；没有写回旧实验/报告目录。

## 4. 集成待办和限制

- A/B 需要使用共享 provider/store/coordinator；B 的 runner、parser、环境 fingerprint、冻结检查 spec、预算账本和记录提交不由本模块实现。
- 新 transport 的 `CommandResult` 是有界机制返回值，不冒充旧 `ExecutionResult`；接入工具执行/输出计量由 P3 统一适配。
- runtime/factory/session 事件、持久 operation 意图、绝对运行预算、完成 gate、Docker 中断续跑拒绝入口和只读 inspect/export 尚未接线。这里的磁盘 capture commit 不是 `run.finished`。
- 本轮真实验收仅覆盖 Sphinx 现有镜像；pytest 4.5 镜像及其他仓库的生产接线未验证。
- 固定 helper 仍依赖可信镜像中的绝对解释器和 Linux `/proc`。不提供后台服务支持或失败后的继续编辑；不把本模块通过计作全链路完成。

## 5. 最终补充结果

在首轮 11 项通过后，补强后台进程父命令退出前的存活断言、实际 writer 正文断言、派生进程数量断言，并增加真实在途命令的期限/取消清理检查和不可读文件检查。还补跑了增加实际 image ID 校验后的 fresh replica 路径。

```powershell
$env:WORKSPACE_DOCKER_TESTS='1'
.venv/Scripts/python.exe -m pytest tests/test_workspace_capture_docker.py -v -rP -x -k 'cli_ends or inflight'
# 5 passed, 8 deselected in 92.30s

.venv/Scripts/python.exe -m pytest tests/test_workspace_capture_docker.py -v -rP -x -k 'initial_edit or chmod'
# 2 passed, 12 deselected in 54.01s

.venv/Scripts/python.exe -m pytest tests/test_workspace_contracts.py tests/test_workspace_capture.py -q
# 最终复核：84 passed in 1.10s

docker --context desktop-linux ps -a --filter label=coding-harness.workspace-v1 --format '{{.ID}} {{.Names}} {{.Status}}'
# 输出为空：本轮 owned 工作区容器没有残留。
```

合计覆盖 14 个不同真实 Docker 场景，按上述三批运行；不是声称最后一次命令同时执行全部 14 项。无捕获失败被当作空补丁成功，无失败路径覆盖此前封存候选。最终代码没有修改共享纯契约或任何既有执行/运行入口。

## 6. 对 3cf237d 独立审查的定向修正

独立 Astra 审查发现两项先前测试未覆盖的真实缺陷。以上 84 项单元与 14 个 Docker 场景保留为历史证据，不能据此声称这两项缺陷原本已通过。

1. **锁获取/停止竞态。** 原先在 `try/finally` 外等待 `lock.acquire()`；`CaptureControl.wait` 在工作和取消同时完成时优先抛取消，可能丢弃已经获取的锁。`execute` 和 `close` 都能稳定复现。现在统一使用私有 `_locked` 上下文：获取协程在成功返回后立即记录自己的所有权，覆盖获取全过程的 `finally` 仅释放该协程实际取得的锁；停止或取消的等待者不会释放现有持有者的锁。取得锁后再次检查取消/绝对期限，边界到期时不执行新工作。取消优先策略保留，asyncio cancellation 仍向上传递。
2. **普通命令错误的内部 30 秒上限。** 原先管理调用和命令共享 `wait` 默认上限，会提前终止合法的 90 秒公开检查。现在普通命令显式使用 `maximum=None`，保留调用方的绝对 deadline；管理调用继续使用原内部上限。创建子进程和执行/I/O 阶段都重新读取同一个 deadline，不另加时长。

API 变化仅在基础层：`CaptureControl.remaining/wait` 的 `maximum` 参数新增接受 `None` 的语义；`DockerWorkspace.cli` 新增关键字 `timeout=30.0`，其中 `None` 表示只有调用方绝对期限。coordinator 的公共方法签名、共享 `contracts.py` 和 capture 身份规则未改。

回归覆盖 `execute/close` 两个入口：获取与取消同时完成、获取边界到期、获取时 asyncio cancellation，以及排队等待取消/超时时不得释放别人的锁；每项都检查后续 `close` 能执行清理。新本地子进程测试将管理上限缩为 20ms，在 90 秒调用期限内运行实际 150ms 子进程，证明普通命令成功且 coordinator 保持 active；另验证管理调用仍超时、短绝对期限仍终止命令并清理。没有等待 31 秒或重跑 Docker 场景。

新增回归先在旧实现上观察到两个漏锁失败和普通命令 `operation_timeout` 失败；修正后：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_workspace_contracts.py tests/test_workspace_capture.py tests/test_workspace_capture_transport.py -q
# 97 passed in 1.07s（原 84 项 + 本轮 13 项回归）

git diff --check -- src/workspace/control.py src/workspace/coordinator.py src/workspace/docker_transport.py tests/test_workspace_capture.py docs/roadmap/P2-FOUNDATION-RESULTS.zh-CN.md
# exit 0，无空白错误；只有仓库 LF/CRLF 提示。
```

本轮修改 `control.py`、`coordinator.py`、`docker_transport.py`、`tests/test_workspace_capture.py` 和本报告，新增 `tests/test_workspace_capture_transport.py`；未运行 Docker。P3 仍需薄适配新 `CommandResult` 与旧 `ExecutionRequest` 后端接口；所有编辑、文件 RPC、捕获和关闭必须共用 coordinator，当前未接入这些入口。本轮不修改 EXECUTION-STATUS 或 A/B 模块，不 commit/push，等待根会话二次定向审查。

## 7. 真实 Sphinx 捕获 → BM25 检索的首个模块演示

在基础层 `ceedd05` 与 A 模块 `f4873b5/51bac37/f05db7b` 集成后，新增薄脚本 `scripts/demo_workspace_retrieval.py`，直接调用 `CaptureProvider`、`WorkspaceIndex(IndexConfig(mode="bm25"))` 和同一个 coordinator。没有新增扫描器、身份算法或检索实现，没有修改 A 的生产代码；没有接入 runtime/factory/session 或完成循环。

从仓库根目录可用以下一条 PowerShell 命令重现；每次选择新的临时输出目录，拒绝覆盖任何已有路径，无需硬编码宿主缓存或私密目录：

```powershell
.venv/Scripts/python.exe scripts/demo_workspace_retrieval.py --output-dir (Join-Path ([System.IO.Path]::GetTempPath()) ('workspace-retrieval-' + [guid]::NewGuid().ToString('N')))
```

脚本使用已有 Sphinx digest/`desktop-linux`，总绝对期限默认 300 秒。只在新 owned 容器内新增、修改、删除 `sphinx/_workspace_retrieval_mechanism_probe.py`，不改任何基础真实文件；原树由删除后完整 `entries == baseline.entries` 再确认。所有编辑、捕获和关闭经过同一个 coordinator。输出目录保存 `result.json`、捕获清单及受控 blob；异常记录清楚的 reason，最终 `finally` 确认容器清理。

本次只运行一次真实演示，耗时 **30.958 秒**，清理占 **0.993 秒**。初始真实工作区含 **1,480 个 regular 文件 / 16,531,532 字节**，534 个 Python 文件全部进入 BM25，生成 **7,116 chunks**；正文来自实际容器及受控 store，没有使用宿主仓库或历史检索缓存。

| 阶段 | Python 文件/chunks | rebuilt / reused / deleted | 观察 |
| --- | --- | --- | --- |
| baseline | 534 / 7116 | 534 / 0 / 0 | 基础查询 `docname_join` 返回带真实 SHA 的位置；probe 尚不存在 |
| unchanged | 534 / 7116 | 0 / 534 / 0 | 新 capture_id，candidate/corpus/index 身份不变，响应关联新 capture |
| added | 535 / 7118 | 1 / 534 / 0 | `workspaceprobequasarsignal` 命中 probe 第 3 行，SHA 与当次完整 manifest/原始写入 bytes 一致 |
| modified | 535 / 7118 | 1 / 534 / 0 | `workspaceprobeorbitsignal` 命中第 6 行与新 SHA；candidate/corpus/index 均变化 |
| deleted | 534 / 7116 | 0 / 534 / 1 | 对两个 probe 查询分别检查全部 534 条文件结果，无 probe 残留；完整树及 candidate/corpus/index 恢复 baseline 身份 |

新增后再删除的 probe 不属于 base，因此最终 capture 的 base-relative delta 为空；索引的 `deleted=1` 则是相对前一次索引。两种语义都由实际接口提供，没有伪造墓碑或改写身份。所有阶段 `encoded=0`；不变阶段复用文件正文/切块，仍重建 BM25 统计，不声称没有任何索引计算。

实际 JSON 证据：[`evidence/p2-workspace/retrieval-docker-01.json`](evidence/p2-workspace/retrieval-docker-01.json)，约 17 KiB，保存五阶段 capture/candidate/manifest/corpus/index ID、有限命中、完整检查数量、耗时及清理结果，不包含正文或宿主私密路径。完整 blob/capture 仅留在本次 pytest 临时输出目录。

```powershell
$env:WORKSPACE_DOCKER_TESTS='1'
.venv/Scripts/python.exe -m pytest tests/test_workspace_retrieval_integration.py -v -rP -x
# 当时的 2 项：已有输出目录拒绝 + 唯一一次真实 Docker 演示，2 passed in 31.61s。

docker --context desktop-linux ps -a --filter label=coding-harness.workspace-v1 --format '{{.ID}} {{.Names}} {{.Status}}'
# 演示进程退出后输出为空，无 owned 容器残留。
```

另有默认不启动 Docker 的输出目录保护和启动失败报告/清理回归。真实 Docker 测试默认 opt-in，普通测试运行无 Docker 开销。最后只补跑这两项本地检查，未重复真实演示：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_workspace_retrieval_integration.py -q
# 2 passed, 1 skipped in 0.26s；skip 是默认关闭的 Docker 演示。
```

这里的 passed 仅指模块演示断言满足；**没有运行 Sphinx 公开回归验证、B 的验证服务、agent 完成循环或任何修复效果实验**，不构成 regression_only 的测试通过证据，更不代表解题。没有 GPU/模型调用、网络下载或安装，没有重跑此前 14 项 Docker 验收；EXECUTION-STATUS 和已发表的历史结果未改。

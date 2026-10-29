# P0 环境、公开验证与接口可行性核查

日期：2026-10-30（Asia/Shanghai）。代码基点：`76388a59116c415267f8923a14c28c1104a89ef6`，实查 HEAD 相同。范围依据：[纲领](HARNESS-NEXT-CHARTER.zh-CN.md)、[阶段方案第 3–6 节](HARNESS-NEXT-PLAN.zh-CN.md)。本报告不实施 A/B 功能，不代替另一份旧轨迹诊断。

## 1. 给 P1 的建议

**可以进入 P1，优先采用已有干净镜像的 Sphinx 工作区，pytest 仓库作为第二个适配样本。** 两个固定版本的解释器和少量既有公开测试均已在现有 Docker 后端实际运行，无须先下载、安装或构建环境。先交付公开回归验证及准确状态；没有公开目标复现时，不把绿色回归结果写成“目标 bug 修复”。

建议路径是：`BaselineImageBackend` 长驻编辑容器 → 新增受控候选捕获接口 → 同镜像的新临时容器应用候选 → 冻结公开检查 → 持久化验证结果 → 无 tool call 分支中的完成检查。保留现有 SWE 独立评分路径。**不通过反复调用 `run_swe_task.run_task()` 实现内部反馈循环，也不向新服务传入 `ImageGrader` 的 spec。**

P1 必须先解决三件事：候选身份不能采用当前基线后端的空快照哈希；运行中捕获必须有一致性边界；Docker 中断后只能明确拒绝/标为未知，或实现新的显式恢复协议，不能声称现有 `RunSession` 已支持透明恢复。

## 2. 检查方式与环境实测

只修改本报告。检查开始时已有 `docs/INDEX.md` 修改，未触碰。没有模型调用、修复任务、训练、镜像拉取/构建、依赖安装或 Docker 配置/服务修改。环境检查使用无宿主挂载、无网络的临时容器；公开测试只产生容器 tmpfs 中的临时文件。容器结束后清理，未写入历史运行目录。报告保留命令和观察摘要，没有另存完整 stdout/JUnit 运行产物。

已按 [AGENTS.md](../../AGENTS.md) 阅读指定参考项目的 `D:/crq/src/learn-claude-code-main/docs/zh/s01-the-agent-loop.md`、`s12-worktree-task-isolation.md`，并查看 `agents/s_full.py` 的 `agent_loop`。参考只用于确认在原循环上增加机制、工作区独立的结构，没有扩大调研或引入其执行实现。

### 2.1 宿主、Docker 与检索资产

| 实查命令/方法 | 结果 | 能证明的范围 |
| --- | --- | --- |
| `git rev-parse HEAD` | 与上列基点一致 | 本报告代码结论的版本 |
| `Get-Command python,docker,wsl`；`python --version`；`./.venv/Scripts/python.exe --version` | 默认 Python 为 `D:/crq/base/Anaconda3/python.exe`；默认与仓库 `.venv` 均为 3.13.9；Docker/WSL 命令存在 | 宿主解释器可启动；不代表适合直接运行历史项目 |
| `.venv` 中用 `importlib.metadata.version` 检查 | pytest 9.1.1，numpy 2.5.3，torch 2.14.1+cu126，sentence-transformers 5.7.0，transformers 4.57.6 | 已有宿主运行依赖；未安装任何新包 |
| `docker context ls`；`docker --context desktop-linux version --format '{{.Server.Version}}'` | 当前 context 为 `desktop-linux`；服务端 29.8.2 | Docker daemon 当前可访问 |
| `docker --context desktop-linux info --format '{{json .}}'` 后仅挑选能力字段 | `OSType=linux`，`MemoryLimit=true`，`PidsLimit=true`，`CpuCfsQuota=true`，NCPU=8 | 后端启动前所需能力存在；后续两个真实 backend.start 也通过了 profile 检查 |
| `docker --context desktop-linux image ls --digests --format ...`；对两目标镜像定向 `image inspect`，只输出 Id/Architecture/Os/Size | 两个 clean digest 均在本机，linux/amd64；另有 Python 3.12 固定 digest 镜像和 H3 测试镜像 | 无须拉取；不把 baseline/known-good/official grader 镜像当开发环境 |
| `docker --context desktop-linux ps --format '{{.Names}} {{.Image}} {{.Status}}'` | 检查开始及结束均无运行容器 | 本轮短生命周期容器已清理 |
| `torch.cuda.is_available()` / `device_count()` | True / 1 | GPU 和 torch 可见；没有加载 encoder 或执行 embedding |
| 列举 `D:/crq/cache/cr/{repos,index,vectors}`；`git --git-dir=... cat-file -t <commit>` | 12 个已有 bare repo 与对应索引；pytest/Sphinx 的目标 commit 都解析为 commit；有 Jina、CodeRank、Qwen 向量库 | 已有缓存可复用；不代表新文本的向量已存在 |
| 检查 Hugging Face 本地目录 | Jina 固定 revision `516f4baf13dec4ddddda8631e019b5737c8bc250` 的模型 snapshot 存在 | 仅为本地文件存在检查；远程代码链路和真实编码仍需小检查 |

另用 SQLite `mode=ro` 打开 pytest/Sphinx chunk 库，均有 vocab/blobs/trees 三表；Jina 向量库有 202004 行，meta 的 model/revision/max_len 与上述冻结配置相符。没有通过 VectorStore 构造器打开历史库写入元数据。使用 `archive_manifest(archive, repo, revision)` 重新校验了两份公开 archive 的路径、根提交与内容清单：pytest archive SHA256 为 `ae7b95364f7e6dfeb5d77b3458fe2a02b6446ec23cbf11a748b610cfd60fe413`，Sphinx 为 `7ddcc24c4596b2642739430f4eede6d61b851e8664e0e6dc7d7807742ea40834`；算出的 source_tree_hash 均与各自 clean record 一致。这不是对当前镜像全文件的重新隔离扫描。

一次 Go template 直接访问 `.CpuCfsQuota` 报字段不存在；改为读取 `{{json .}}` 后该字段确为 true，且后端启动成功。这是 CLI 格式接口差异，不是 Docker 不可用。

`.swebench_envs/requests-3362` 存在 Windows 环境目录，但没有在本轮校准它，也未找到已校准的 Requests clean image 路径；因此不把 Requests 列入首批。历史 `dev-tasks.yaml` 指向的 pytest-5103 `staging/original` 下配置文件已无法按原路径读取，不能依赖这些旧展开目录；下文的公开 archive、bare git 和实际镜像仍可用。

### 2.2 两条真实公开基线检查

直接调用现有 `BaselineImageBackend`，未导入 SWE runner 或 grader spec。每个任务顺序执行：`start({})` → `export_delta()` → `execute(ExecutionRequest(...))` → 读取 `/tmp/p0-public.xml` 的 testsuite 属性 → 再次 `export_delta()` → `close()`。

共同设置：`context=desktop-linux`、固定 clean digest、CPU=1（默认值）、内存 1 GiB、workspace tmpfs 256 MiB、tmpfs 64 MiB、network=none。现有 backend 自动应用非 root、只读镜像层、无宿主挂载、资源限制和 `--pull=never`。测试 request 的期限为当时 monotonic 时间 + 90 秒；输出内存上限每流 64 KiB；没有宿主 artifact 写入。显式公开环境仅为：

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTHONPATH=/workspace:/workspace/src
PYTHONDONTWRITEBYTECODE=1
```

工作目录为 `/workspace`；执行 argv 为：

```text
/usr/local/bin/python -B -m pytest testing/test_compat.py -q -p no:cacheprovider --basetemp=/tmp/p0-public --junitxml=/tmp/p0-public.xml
/usr/local/bin/python -B -m pytest tests/test_util.py -q -p no:cacheprovider --basetemp=/tmp/p0-public --junitxml=/tmp/p0-public.xml
```

| 检查 | pytest-5103 | Sphinx-8721 |
| --- | --- | --- |
| backend 启动耗时，单次观察 | 5.607 s | 7.332 s |
| 第一次空 delta 导出耗时，单次观察 | 0.187 s | 0.329 s |
| 导出后状态 | `active=True, closed=False` | `active=True, closed=False` |
| 公开测试模块 | `testing/test_compat.py` | `tests/test_util.py` |
| ExecutionResult | `succeeded, exit_code=0`，约 3124 ms | `succeeded, exit_code=0`，约 3909 ms |
| pytest 输出 | `8 passed in 0.86 seconds` | `8 passed, 7 warnings in 0.84s` |
| JUnit suite | tests=8，failures=0，errors=0，skipped=0 | tests=8，failures=0，errors=0，skipped=0 |
| 执行前后 delta | changed=0，deleted=0；skipped_files=7 | changed=0，deleted=0；skipped_files=9 |
| 最终 close | `cleanup_ok=True` | `cleanup_ok=True` |

Sphinx warnings 涉及旧版 distutils、pkg_resources、docutils API，未导致本次失败。这里没有执行完整仓库测试、改动候选、验证副本重放、失败反馈或崩溃恢复；导出性能只测了**无改动**情况，不能外推到大补丁。测试日志/JUnit 来自公开源码的正常运行，仅用于 P0 环境可用性核查；当前还没有生产验证服务的完整日志持久化证据。

## 3. 首批仓库、材料来源与准备成本

### 3.1 首选：sphinx-doc/sphinx

- 固定源码：`82ef497a8c88f0f6e50d84520e7276bfbf65025d`。公开回归模块为 [tests/test_util.py](https://github.com/sphinx-doc/sphinx/blob/82ef497a8c88f0f6e50d84520e7276bfbf65025d/tests/test_util.py)，对应版本的测试与配置位于同一公开源码树。本次也打开上述公开页面确认来源，没有查阅解决方案或 PR patch。
- 镜像：`coding-harness-a5-clean@sha256:1a6e20fb4b37194f8596111bc3658261cc40dbd85dc09a0e5be6d6690220b4e2`，本机 inspect Size=902951062 字节。实际解释器 `/usr/local/bin/python`，Python 3.9.25，安装的 pytest 8.4.2。
- 环境记录：[clean record](../sprint/clean-images/sphinx-doc__sphinx-8721.json) 的 `source_only_match`、`answer_isolation_pass` 均 true。这是已有扫描记录，本轮没有重跑全镜像私有材料扫描。
- 公开源码 archive：[source.tar.gz](../upgrade/evidence/A5-harness-preflight-12-host-sources/tasks/sphinx-doc__sphinx-8721/source.tar.gz)，实查 1473 个普通文件、解包字节 16466026、无 symlink。镜像中 `.py` 为 534 个、3970374 字节，遍历未见 symlink。
- 问题文本：[已分离的公开输入](../../runs/sprint5-main-public/sphinx-doc__sphinx-8721.json)，字段只有 task_id、issue_text、issue_sha256、repo_ref。该题已经用于旧开发/评测，只适合作开发/回归输入，不是新保留题。
- 准备成本：当前小范围公开命令已经可用，新增服务可直接使用镜像；仍需冻结完整公开测试/fixture/配置清单，选择与任务相关的更广检查，并建立基线收集记录。Sphinx 的 docs/build 测试可能需要额外系统工具或依赖，不能从 8 项工具函数测试推出全套可跑。

Sphinx 由安装的 pytest 执行，适合作为第一条 B 主线；其 archive 总体超过普通 Docker 快照 8 MiB 限制，适合利用基线镜像加 delta，A 只转运受支持源码/公开测试正文。

### 3.2 第二候选：pytest-dev/pytest

- 固定源码：`10ca84ffc56c2dd2d9dc4bd71b7b898e083500cd`。公开回归模块为 [testing/test_compat.py](https://github.com/pytest-dev/pytest/blob/10ca84ffc56c2dd2d9dc4bd71b7b898e083500cd/testing/test_compat.py)，本次打开公开页面确认来源。
- 镜像：`coding-harness-a5-clean@sha256:02c0d9e5295d53ed4df911b519c06a403ccf18efad6474e2a6d943cf84b7cd92`，本机 inspect Size=233678392 字节。实际 Python 3.9.19，`pytest.__version__=4.5.0`，源码导入位置为 `/opt/repo/src/pytest.py`（编辑工作区中对应 `/workspace/src/pytest.py`）。
- 环境记录：[clean record](../sprint/clean-images/pytest-dev__pytest-5103.json) 的 source_only_match、answer_isolation_pass 均 true，native_artifacts 为空；已有 source hash 为 `e6fb1d387364f6ae21030649f20e41802b1ca1e56608331261cb1690b9f017d9`。
- 公开源码 archive：[source.tar.gz](../upgrade/evidence/A5-harness-preflight-12-host-sources/tasks/pytest-dev__pytest-5103/source.tar.gz)，418 个普通文件、3161090 解包字节、无 symlink。镜像中 `.py` 为 199 个、1950538 字节。
- 公开问题在 `D:/crq/src/coding-agent/reports/upgrade/a5-funded-build-preparation-20260921-v12/public/a5-build-47b71eeda51b255d.json`；路径来自 [dev-tasks.yaml](../../configs/sprint/dev-tasks.yaml)。它是已有公开问题资产，不是新的 holdout。
- 准备成本：无需安装；测试框架本身就是待修源码，断言重写、pytester/子进程、测试收集及输出格式都更容易受候选影响。因此适合第二个兼容样本，不能把 Sphinx 的 pytest 8 输出解析约定直接照搬到 pytest 4.5。

第一次用 `importlib.metadata.version('pytest')` 检查该镜像时得到 PackageNotFoundError，因为这里应从仓库源码导入 pytest；显式把仓库 `src` 放入导入路径后版本可读，公开测试也实际通过。不能把缺少安装元数据误判为没有可运行 pytest。

### 3.3 这些测试的证据边界

上述两个模块均为事先选择的公开回归检查，基线本来就通过；不是依据隐藏 F2P/P2P 筛选。它们可以发现范围内的回归，**即使最终仍然全绿，也不能证明 pytest-5103 或 Sphinx-8721 所述目标 bug 被修复**。本轮没有建立两题的公开目标失败复现。

P1/P2 可以自主将覆盖标签冻结为 `regression_only`。若后续由公开 issue 和基线源码建立确实失败的公开目标检查，应记录来源、冻结时间、预期和基线结果；不能借用 grader 中的 test patch 或 F2P 列表补齐这一证据。人为 fixture 用来验收反馈机制时，单独标记，不记作真实修复收益。

## 4. 逐接口复用清单

| 路径 / 函数 | 现成能力 | 新模式必须补的部分 |
| --- | --- | --- |
| [serve.py](../../src/retrieval/serve.py)：`RetrievalService.task_index`（130）、`TaskIndex.search`（169） | 固定 repo+base_commit，BM25/向量/RRF 排序；延迟加载 encoder、查询向量缓存 | 从受控工作区视图建立/替换 index；变化检测、删除、版本发布；通用排序目前依赖 `scripts.cr_eval`，提取时保留冻结适配器和原排序 |
| [chunking.py](../../src/retrieval/chunking.py)：`chunk_source`（116）、`build_blob_record`、`Snapshot` | AST 分块、窗口 fallback、blob/token 缓存；CHUNKER/TOKENIZER 有版本 | `is_corpus_path`（39）明确排除所有 tests/test/testing 和 conftest；工作区模式需单独语料规则与缓存命名空间，不能改原 CR 语料 |
| [embed.py](../../src/retrieval/embed.py)：`VectorStore`（74）、`Encoder`（185）、`encode_missing` | 实际编码文本 SHA256；模型 revision/max_len 的库级隔离；新文本缺失检测、编码 | 当前 Encoder 硬编码 cuda/float16；新模式显式设备与缓存目录，真实 encoder 小检查尚未做；增加公开测试会产生新向量，原缓存不是完整工作区服务 |
| [retrieve.py](../../src/tools/retrieve.py)：`TaskRetrieval`、`RetrieveCodeTool.execute`（63） | query/k 校验、异步搜索、调用日志、只返回位置 | 当前固定文案称修改前源码，FileHit 也没有文件 hash；需返回 snapshot/index/file hash、材料类型，失败不冒充当前索引 |
| [factory.py](../../src/harness/factory.py)：`build_agent` | 可注入 execution_backend/retrieval；Docker 工具包装与白名单 | retrieve_code 目前是仅读 CR cache 的宿主例外，不是任意宿主工作区权限；需受控 snapshot adapter、可选验证服务/工具与完成控制接线 |
| [base.py](../../src/execution/base.py)：`ExecutionRequest`（97）、`ExecutionResult`（129） | argv、期限、取消、输出配额、产物目录、退出码和 cleanup 状态 | Protocol 只有 execute；没有通用 capture/fork-validation 方法；这些不能写成已有 API |
| [docker.py](../../src/execution/docker.py)：`start`、`execute`（313）、`close`（285） | 固定 digest、无隐式 pull、tmpfs、无挂载、资源检查、受控执行/清理；完整快照传输最多 2048 文件/8 MiB | `close(snapshot=True)` 导出后销毁，不适合运行中 A/B；`_busy` 只覆盖 execute；独立捕获和捕获/编辑互斥需新增 |
| [baseline_image_backend.py](../../src/execution/baseline_image_backend.py)：`start`（133）、`export_delta`（146）、`close`（166） | 从镜像 /opt/repo 复制工作区；运行中 export_delta 不 close，已实测；新增/修改 bytes、删除名单；200 文件/4 MiB 上限 | 没有完整候选 manifest/hash、原子捕获、hash 规则版本或副本应用公开 API；symlink 被跳过，mode 变化未记录；超限必须明确失败 |
| [command.py](../../src/tools/command.py)：`RunCommandTool.execute` | 策略、运行期限和取消、配额、结构化状态及日志路径 | 测试可有副作用，验证工具不能标成 read_only；复用 ExecutionRequest，不另开未记录的宿主 shell |
| [loop.py](../../src/runtime/loop.py)：无 tool call 分支（772） | 直接 DONE、success=True 并返回；`run`（417）统一开启/关闭 backend | 完成检查必须放在 DONE 和关闭之前；success 是运行自然结束，不能代指验证通过或解出；自动检查使用宿主操作记录，不能伪造 tool_call_id |
| [session.py](../../src/runtime/session.py)：intent/result、`_run_impl`、runtime_fingerprint | durable 意图/结果、输出 artifact、模型账本、绝对到期、配置/工作区指纹；未知工具写入不重放 | validation/完成控制事件、候选产物引用、验证预算和阶段需专门接线；tool.result reducer 会追加 tool 消息，不能直接拿它记录自动宿主检查 |
| [recovery.py](../../src/runtime/recovery.py)：`reduce_events`、`validate_resume`（135）、`reconcile_file` | 显式事件白名单、连续序号、拒绝不兼容恢复；仅原生 write_file 有按内容确认路径 | 无验证 reducer；manifest 的 workspace 身份只是 path/device/inode，不是内容版本；Docker 中断重建及验证中断状态需新协议 |
| [run_single.py](../../scripts/run_single.py) | config+factory+RunSession；Docker 要求持久 session | 没有传 retrieval，也没有公开验证配置；可由新薄入口复用，不复制 runner。旧配置默认不打开新控制 |
| [run_swe_task.py](../../scripts/run_swe_task.py)：`clean_backend`、`run_task` | 镜像环境选择、完整 agent 生命周期、delta 导出、运行后独立评分 | `prerun_check` 已调用 grader_spec；`run_task` 后半段使用隐藏 spec 并评分。因此不把这个入口整体导入/循环调用为 B 服务 |

还有两类纯能力可参考提取： [a5_image_tree.py](../../src/eval/a5_image_tree.py) 的 archive_manifest/路径与 mode 校验，以及 [grader.py](../../src/eval/grader.py) 的规范化内容哈希思路。前者支持大 archive 清单，后者调用 validate_files，受完整传输 8 MiB 限制；都不能直接等同新候选身份。现有 [image_grader.py](../../src/eval/image_grader.py) 的副本准备/应用顺序可读作实现参考，生产验证路径不调用其 evaluate、score_receipt 或隐藏输入。

## 5. P1 必须处理的具体缺口

### 5.1 运行中捕获、排除与候选 hash

`export_delta()` 是可复用入口，但 EXPORT_DELTA 会逐文件遍历两棵目录，没有冻结进程/文件视图；它不检查 `_busy`，也没有跨 execute 的统一锁。串行模型轮次并不能排除 run_command 留下的后台写进程。Docker execute 超时/取消时会杀/回收宿主 CLI，但刻意保留容器，不能据此保证容器里的测试/写进程已停。因此 P1 必须定义捕获屏障、残留进程确认或拒绝捕获的条件；证据不足时输出未知/不可用，不能发布混合版本。

当前 delta 排除：`__pycache__`、`.pytest_cache`、`build`、`*.egg-info`、`.coverage`、编译产物后缀；所有 symlink 也跳过。它没有新语料所需的完整 `.git`/虚拟环境/凭据等排除规范，也不保留 chmod，不能默认为通用 manifest。

还需校准传输上限：名义 delta 内容上限是 4 MiB，但 export_delta 的控制输出上限是 5 MiB；接近满额的内容经过 Base64 和 JSON 后可能先碰控制输出上限。本轮只测空 delta，没有声称 4 MiB 边界已通过。捕获内部已有 120 秒控制超时，复制也有固定超时；新服务仍须把这些步骤纳入共享剩余期限，不能把内部固定上限当作总预算控制。

**特别注意现有哈希占位：** BaselineImageBackend.close(snapshot=True) 在导出 candidate_delta 后设置 `snapshot={}`；AgentLoop.run 随后对 backend.snapshot 生成 execution.snapshot/candidate_hash。因此这条路径得到的是空字典的哈希，delta 内容没有进入该 session hash。SWE runner 在 session.run 完成后另存 candidate-delta.json。这适合旧 runner 的生命周期，但不能复用为 B 的版本凭据或中断恢复产物。

建议 candidate_id 绑定：实际初始镜像/源码 manifest + 完整受支持执行相关文件的增删改内容 hash + 规则版本。包括依赖/配置/公开测试及 fixture 的身份，不能只取 `.py` 或编辑列表。对生成版本文件等 archive 之外的运行文件也要在实际初始容器 manifest 中记录。检索 index_id 只绑定 Python 语料子集，不能冒充 candidate_id。

初版可自主规定拒绝新增 symlink、特殊文件、原生编译改动和执行权限变更，冻结依赖/测试配置；超限也拒绝。不要默默排除这些变化后继续颁发“已验证”状态。对两目标仓库，本轮未见初始 symlink；不表示已测过替换成 symlink 的异常场景。

### 5.2 副本与公开检查冻结

同 clean digest 创建全新 BaselineImageBackend 是现成环境种子；**安全应用候选 delta、保护测试/配置、收集结构化公开结果的服务仍需写。** ImageGrader.APPLY 的数据重放结构可以参考，但当前 `_ok` 是内部方法，且该 apply 代码本身不是新的通用路径校验契约。

建议从公开初始工作区冻结测试源码、fixture、根/递归 conftest、pytest 配置、依赖声明及必要测试支持文件；明确 allowlist 的执行 env、cwd、argv 和解析规则版本。模型可读这些材料；触碰冻结路径应显式拒绝或按声明策略恢复冻结材料，并在报告保留候选的实际改动，不能静默用删断言/skip 的版本获得通过。新增候选测试可随补丁保存，但不替换本次检查。pytest 仓库的框架实现正是修复对象，不能把整个 src/_pytest 当冻结测试支持代码。

`DockerExecutionBackend.execute` 用 env -i 清掉镜像 ENV，所以必须显式传 PYTHONPATH/禁用插件自动加载等必要参数；声明的 cwd 实际要符合该后端固定 `/workspace` 的 profile，不能假定任意 host Path 会成为容器 cwd。

测试 runner/解析器至少记录正常结束、收集数量/身份、执行结果与 skip，并检查报告完整性、ExecutionResult、deadline 和清理结果；退出 0 加一段打印摘要不够。P0 的 JUnit 读取只是环境证据，尚不是已实现的反绕过验收器。pytest 4.5 与 pytest 8 的格式/框架变化要分别覆盖。验证副本结束后销毁，任何 /tmp、cache、生成文件都不回灌编辑容器。

公开开发输入应显式允许问题文本、基线源码、基线公开测试和项目配置。隐藏边界包括 grader 的 test patch、gold patch、F2P/P2P 列表、私有绑定与独立评分诊断；它们不进入 A 语料、PublicValidationSpec、反馈摘要或求解日志。某材料后来可以在上游公开找到，也不自动撤销本次任务约定的评分隔离边界。

### 5.3 恢复、完成和总预算

- `AgentLoop.run` 直接拒绝有保存 loop 状态的 Docker 中断会话，也拒绝 execution.starting 已存在时创建新容器重放。RunSession 的本地恢复能力不能据此声称为 Docker 工作区恢复。
- `RunSession.reconcile()` 对未完成操作确认本地原生 write_file 的内容证明，否则记录 outcome_unknown；不能把验证当只读自动重跑。新服务需记录 operation/container 标识、候选/检查/环境身份、预算预留/结算、日志引用和 cleanup。
- 先提交结果再提交完成状态；恢复时同身份完整记录可复用。结果未知先确认残留进程/容器和清理；未满足显式重跑规则就停止为未知。若第一版不实现 Docker 编辑状态复原，应明确支持“安全拒绝中断恢复”，并保留可检查产物。
- 已有 Budget/Deadline/ModelBudgetLedger 和 session 绝对 expires_at 可复用；停机时间计入总期限。验证启动、捕获、副本准备、执行、清理以及反馈后的模型调用共用同一任务 deadline，不重新 build/run 一个新 agent 重置账本。
- 新增验证次数预留/结算与恢复状态。基线单独记次数但消耗总时间；候选主动验证和自动完成检查共用次数及缓存；匹配且完整的已提交结果不重复执行/扣次。保留最多 3 次候选验证、2 次失败反馈继续的建议默认；无法判定默认停止，不加隐式环境修复重试。

## 6. 最小接口提议（均非现成 API）

建议由 P1 单一负责人冻结以下输入输出，A/B 各自实现时不另造候选定义：

| 契约 | 最小内容/语义 |
| --- | --- |
| WorkspaceCapture | origin（容器/context/workspace）、base revision、image digest、实际初始 manifest；capture_id、candidate_id、规则版本、规范化 path→hash/type/mode 清单、增删、排除/拒绝记录；A 所需 Python 正文；B 的受限 delta/产物引用。捕获结束才发布，失败不产生可用新版本 |
| RetrievalView | 从同一次 capture 派生的源码+公开测试子清单；独立 index_id、file_sha256、材料类型；BM25 重建/向量复用在发布前完成 |
| PublicValidationSpec | 明确 public provenance；冻结测试/fixture/配置内容与 hash、argv/cwd、env allowlist、timeout、解析版本、覆盖标签；check_id 与 environment_id。无 test_overlay/F2P/P2P/gold/private_source_binding 字段 |
| ValidationService.validate | 输入 capture + PublicValidationSpec + operation_id + 共享 deadline/cancel/配额；全新临时副本执行；返回候选/检查/环境三重身份、通过/行为失败/无法判定/未执行、测试计数/身份、基线比较、stdout/stderr artifact、cleanup、预算结算 |
| CompletionDecision | 输入当前候选身份和完整记录、剩余预算、失败反馈计数；输出允许结束/继续反馈/停止为未知或预算耗尽，另存 run_status、development_validation、independent_solved 三个判断 |
| DurableValidationState | 主动工具和自动宿主动作共用 service；新 validation.* / completion.* 事件与 reducer，保留真实工具 call_id 或独立 host_operation_id；支持已提交结果复用，未知操作不盲重放 |

A 的 Docker 文件视图必须来自实际编辑容器的受控读取。可在开始时从容器采集 Python 语料正文和完整文件哈希清单，后续结合同一容器的 delta；若用公开 archive 加速，先核对容器文件清单/哈希及额外生成文件，不能直接把宿主 bare repo 当容器当前状态。Sphinx 完整 archive 超过 8 MiB，因此不要先强行走普通完整 seed/snapshot 再被配额卡住。

## 7. 真正阻塞项与可自主确定的默认值

| 项目 | 当前判断 | P1 动作 |
| --- | --- | --- |
| Docker/两个候选的小范围公开基线 | 已实测可用，无环境启动阻塞 | 直接复用固定 digest，不重建、不装依赖 |
| 完整仓库公开回归范围与目标行为覆盖 | 尚未校准；两模块仅证明回归子集可运行 | 明确初版 regression_only；真正目标检查只从公开需求建立并先测基线，不接隐藏 spec |
| 候选 hash 与捕获一致性 | 实质实现缺口 | 替换空快照占位语义；统一屏障、manifest、规则/配额与拒绝条件 |
| 干净验证副本与冻结配置 | 环境种子现成，服务未有 | 增加安全 delta 应用、冻结材料、公开解析与日志；先在 loop 外实现 |
| Docker 中断恢复 | 现有明确拒绝 | 冻结“显式拒绝/未知”或实现复原协议；不能默许重新开始一个新预算任务 |
| 新语料真实 embedding | 模型文件、缓存、CUDA 可见；encoder 未测 | A 的局部机制可先用确定 encoder/BM25；P2-A 验收仍需真实 encoder 小检查，不能算已通过 |
| 独立评分与反馈边界 | 旧 runner 已有独立评分，但 spec 在宿主可见 | 新开发服务不导入其生命周期/私有读取逻辑；只在求解完全结束后走原独立评分 |

可自主采用：首版 Linux/Docker + 这两个固定 Python 版本；先 Sphinx、后 pytest；network=none；只接受声明范围的常规文件和有限 delta；测试/依赖/配置冻结；查询边界串行更新；基线 + 最多 3 次候选验证 + 最多 2 次失败反馈；未知/环境错误停止并说明；新模式默认关闭，不改旧入口默认行为。本次 90 秒只是 P0 命令上限，产品 timeout 应写入每仓库配置并服从总期限。

本轮不需要用户为常规环境检查再次确认。需要主会话在 P1 明确并写入共享契约的决定只有：首版 Docker 恢复承诺、候选 manifest/拒绝范围、以及公开检查清单。若仍没有公开目标失败案例，工程阶段继续以验证可信度和回归使用性验收，暂不启动或宣称修复率收益实验。

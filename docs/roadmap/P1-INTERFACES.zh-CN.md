# P1：工作区版本、公开验证与完成控制接口

日期：2026-10-30（Asia/Shanghai）。审查代码基点：`76388a59116c415267f8923a14c28c1104a89ef6`。本文是供 P2/P3 实施的契约，**下文新增类型、协议、文件与事件均为拟议，尚未实现**；不表示 Docker 捕获、验证副本或恢复已经验收。

依据：[纲领](HARNESS-NEXT-CHARTER.zh-CN.md)、[阶段方案 §3–8](HARNESS-NEXT-PLAN.zh-CN.md)、[最终轨迹报告](P0-TRAJECTORY-REVIEW.zh-CN.md)、[最终环境报告](P0-ENVIRONMENT-REVIEW.zh-CN.md)。已阅读 AGENTS 指定参考项目的 `docs/zh/s01-the-agent-loop.md`、`s12-worktree-task-isolation.md`，采用“围绕原循环加机制、控制状态与工作区分离”的结构，不引入另一套 agent 引擎。

**结论：可以推进 P2。先由集成人落一个纯数据契约基点，再让 A/B 依赖内存 fixture 独立实现；真实 Docker 捕获适配器是一个串行基础任务，必须在 P3 接线前通过验收。首版明确拒绝 Docker 中断后继续求解，只支持已有产物的只读检查与导出。当前没有必须唤醒作者决策的范围问题。**

## 1. 首批范围与最小数据流

### 1.1 固定首批配置

| 项目 | 第一条端到端主线 | 第二适配样本 |
| --- | --- | --- |
| 仓库 | `sphinx-doc/sphinx` | `pytest-dev/pytest` |
| 源码基点 | `82ef497a8c88f0f6e50d84520e7276bfbf65025d` | `10ca84ffc56c2dd2d9dc4bd71b7b898e083500cd` |
| clean image digest | `sha256:1a6e20fb4b37194f8596111bc3658261cc40dbd85dc09a0e5be6d6690220b4e2` | `sha256:02c0d9e5295d53ed4df911b519c06a403ccf18efad6474e2a6d943cf84b7cd92` |
| 镜像引用前缀 | `coding-harness-a5-clean@` | 同左 |
| Python / pytest | 3.9.25 / 安装的 8.4.2 | 3.9.19 / 仓库中的 4.5.0 |
| 初始公开选择器 | `tests/test_util.py` | `testing/test_compat.py` |
| 覆盖声明 | `regression_only` | `regression_only`；框架自身被修改，另有解析适配门槛 |
| P0 已证实 | 8 passed、前后空 delta、容器正常清理 | 同左 |

环境采用现有 Linux/Docker profile：固定 digest、无网络、无宿主挂载、非 root、只读镜像层、编辑目录 `/workspace`。P0 的 8 项是环境证据，运行时仍须产生本次基线的 nodeid 与结果清单；不能直接写死“计数等于 8 就通过”。Sphinx 的这一模块不覆盖整个公开 issue，初版并不承诺修好该 issue。Sphinx-10614、scikit-learn、Django 暂不纳入必选：分别尚未校准本轮环境、存在原生构建/导出边界、需要不同 runner。

普通入口拟为 `scripts/run_workspace.py`，只组装公开配置、已有 factory、RunSession 和新服务。产品首版仅支持 `react` + 持久 session + Docker 基线镜像；本地目录只作为可控 fixture/模块开发输入，不声明通用本地求解支持。新模式默认关闭，原 SWE/CR/A5/A6 入口和冻结语义不变。

```text
公开问题 + 固定镜像 + 公开配置
  → 启动编辑容器；捕获真实初始树；冻结公开检查并做基线
  → 共享 CaptureProvider
       ├─ A：Python 子视图 → 构建/更新索引 → 带版本的位置
       └─ B：候选 base+delta → 新容器 → 冻结检查 → 验证记录
  → 原模型/工具循环；显式 validate_patch 或宿主完成检查
  → 封存候选与记录 → 清理容器 → 完成状态及产物
  → 可选独立后验 grader（单独路径，不回传求解）
```

A 不依赖验证服务、完成控制、隐藏评分；B 不依赖 encoder、索引排序或 A 的完成。两者只共享不可变捕获契约。运行控制不推断测试正文；它消费 B 的结构化记录及预算。共享捕获不得从宿主 grader 目录读文件。

## 2. 共享类型及身份规则

### 2.1 纯数据模块基点

拟新增 `src/workspace/contracts.py`，仅使用标准库 dataclass/Enum/typing/hashlib/json；无 Docker、模型、数据库、numpy 或 grader 导入。包初始化文件保持空。由集成人一次落地以下类型、规范序列化和最小 fixture，A/B 不各造一套 hash。

```python
# 拟议接口；ID 均为带域前缀的 sha256，字段不可变。
@dataclass(frozen=True)
class FileEntry:
    path: str                  # 规范的 POSIX 仓库相对路径
    kind: str                  # v1 只接收 "regular"
    mode: int                  # stat.S_IMODE；不含 uid/gid/mtime
    size: int
    sha256: str                 # 原始 bytes，不转换换行/编码

@dataclass(frozen=True)
class ArtifactRef:
    sha256: str
    size: int
    key: str                   # 受控产物库内的键，不是任意主机路径

@dataclass(frozen=True)
class WorkspaceCapture:
    schema_version: str        # "workspace-v1"
    capture_id: str            # 每次捕获 UUID；不是内容身份
    origin: dict               # run/context/container ID/workspace；审计用
    base_revision: str
    image_digest: str
    base_manifest_id: str      # 实际启动后 /workspace 的基础清单
    manifest_id: str           # 当前完整受支持树清单
    candidate_id: str
    rules_id: str
    entries: tuple[FileEntry, ...]
    changed: tuple[tuple[str, ArtifactRef], ...]
    deleted: tuple[str, ...]
    corpus_blobs: tuple[tuple[str, ArtifactRef], ...]
    excluded: tuple[dict, ...] # 明确规则/数量；不偷偷吞掉未知文件类型
    admissible: bool           # 身份可计算不等于可以验证
    violations: tuple[dict, ...]
    captured_at: str
    barrier_receipt: ArtifactRef
```

`changed` 是相对 **base_manifest** 的全部字节变化/新增，`deleted` 为明确墓碑；不是相对上次查询。非空补丁、原树无修改、捕获失败分别表达：`changed/deleted` 非空、二者为空且身份有效、没有成功 capture。失败绝不伪造成空 delta。

产物库用 `BlobReader.read(ref) -> bytes` 验证长度/hash，A/B 不接收可随意读取的宿主根目录。初始 Python 正文由真实容器采集；随后可用已核验 blob 重用。使用公开 archive 加速必须先逐路径验证其与真实基础清单相符，补上实际生成的文件，不能以 archive hash 代替实际工作区身份。

### 2.2 哈希规则（拟议 `workspace-v1`）

规范 JSON：UTF-8、`ensure_ascii=False`、对象键排序、分隔符 `(',', ':')`、禁止 NaN/浮点身份字段；集合显式排序，数组语义有序。内容身份使用 `sha256(domain + b'\0' + canonical_json)`。文件 bytes 自身直接 SHA256。路径保留大小写，不做 Unicode 自动正规化；只接收有效 UTF-8、非空 POSIX 相对路径，拒绝绝对路径、盘符、反斜杠、NUL、`.`/`..` 段、重复规范路径及大小写折叠冲突。Linux mode 按整数记录；更换 mode 在首版属于不支持修改。

```text
manifest_id = H("tree-v1", 按 path UTF-8 字节序排列的 FileEntry)
candidate_id = H("candidate-v1", {
  base_revision, image_digest, base_manifest_id, manifest_id, rules_id
})
corpus_id = H("corpus-v1", {corpus_rules_id, 排序后的 Python path/hash/kind})
index_id = H("index-v1", {corpus_id, chunker/tokenizer版本,
  encoder模型+revision+预处理+max_len+精度, BM25/RRF配置, mode})
validation_key = H("validation-v1", {candidate_id, check_id, environment_id})
```

时间戳、容器名、产物存储位置、capture_id 不进入内容 hash。重命名先按删+增；同样 bytes 的不同路径身份不同。delta 序列化摘要另存为传输校验，不能代替候选身份。`materialize(base, delta)` 必须重新得出完全相同的 `manifest_id` 才能执行测试。

`environment_id` 不等同镜像名：包含镜像 digest/实际 image ID、架构、Python 路径及版本、已声明依赖指纹、环境变量白名单的实际非秘密值、cwd、执行 profile/资源限制、隔离与初始化规则版本。容器 ID 和耗时只审计。禁止依赖镜像 ENV，因为现有 `execute` 使用 `env -i`。配置、依赖或环境变化要生成新身份/新基线，首版不允许在一次任务中热切换。

`rules_id` 是完整候选覆盖/排除/拒绝/路径/mode/限额规则配置的 hash；`corpus_rules_id` 是语料选择、编码/大文件处理规则的 hash，不能只是任意手填版本字符串。纯模块应统一导出 `canonical_json`、`tree_id`、`candidate_id`、`ArtifactRef`、`FileEntry`、`WorkspaceCapture`、`BlobReader` 协议；具体 I/O 与产物发布另放基础层。检查规则变更也改变 check_id，旧记录只能作为历史。

### 2.3 候选覆盖、可修改范围与检索子集

基础/候选清单覆盖 `/workspace` 下所有正常开发文件：源码、测试、fixture、文本/二进制资源、项目配置、依赖声明、实际生成的版本文件。**不是仅哈希 `.py`，也不是仅哈希已知编辑列表。** v1 以固定排除规则跳过 `.git`、`__pycache__`、`.pytest_cache`、`.coverage` 和 `.pyc/.pyo` 缓存；在新验证副本中清除这些缓存并禁止由候选 delta 携入。排除只定义验证视图，不声称编辑容器的任意缓存/`/tmp` 状态也被重现。

不沿用旧导出的广泛排除来掩盖风险：`build`、`*.egg-info`、`.so/.pyd/.o/.dylib`、虚拟环境和依赖配置需能被检测；v1 不支持新增/修改它们。初始树中若有原生链接/特殊文件则 profile 预检拒绝，不能悄悄跳过再认证。首批两环境 P0 未发现初始 symlink，适合该限制。凭据/私有材料路径（`.env*`、`.ssh`、`.aws`、`.grader_private`、`.harness_artifacts` 等）不进语料或模型产物，发现时拒绝当前 profile，而不是传播正文。

初版 Sphinx 可改 `sphinx/**/*.py` 中的普通文件；其他基础文件都冻结。全树任何位置新增/修改/删除 `conftest.py`、`sitecustomize.py`、`usercustomize.py`、pytest/项目配置、依赖声明或 `.pth` 均不支持。新增候选测试可先保存为候选产物，但触及冻结 `tests/**` 的候选在 v1 **拒绝认证**，不悄悄删掉其改动后给原候选颁发通过。若需要支持候选新增测试而保持原检查，另升规则版本。pytest 适配样本拟允许修改 `src/**/*.py`，仍保护测试/配置；框架源码不是冻结测试材料。

正常文件的未支持改动：可计算真实 `candidate_id`、保存 delta，`admissible=False`；B 返回 `inconclusive/unsupported_change`。symlink、特殊文件、路径非法、超限或内容捕获不完整：没有可认证的 capture/candidate_id，只存拒绝原因和安全元数据，不输出伪完整补丁。

A 的子集是公开 `.py` 源码及测试/fixture/conftest Python 文件；记录 `source/test/test_support`。单文件超过 1 MiB、无法按 Python 编码声明解码或 AST 失败的处理显式记录：解码失败排除；AST 失败可按既有窗口切块。A 的排除不改变 B 的完整清单。仅改变非 Python 资源可使 `candidate_id` 变化而 `corpus_id/index_id` 不变；同树无变化重查可复用索引，但响应仍带当前 capture/candidate 关联。

## 3. 真实容器捕获：串行基础层的最小实现

拟议协议：`await CaptureProvider.capture(control) -> WorkspaceCapture`；`control` 包含宿主 operation_id、共享 deadline、cancel、输出/产物配额。失败抛有稳定 reason 的 `CaptureError`；不返回半成品。`MemoryCaptureProvider` 只用于 fixture。真实适配器由集成人独占，A/B 不分别实现文件扫描。

### 3.1 首版采用静止工作区，不承诺后台任务快照

1. 新模式中所有工具执行（包括 container file RPC、命令、捕获和关闭）经过同一个 `WorkspaceCoordinator` 锁；host 端 `_busy` 不是这个锁。禁止额外宿主进程对该任务容器执行命令；对同一容器的外部操作不在受支持范围。
2. 捕获只在已完成工具批次后启动。容器内可信 helper 使用绝对 Python 路径、`-I -S`、宿主固定脚本，直接读取 `/proc`，核对 PID/starttime/线程；只允许创建时记录的 PID 1 睡眠进程与当前捕获 helper。不能只按进程名或 uid 白名单放行。
3. 发现其他任务/后台进程、线程、无法枚举的 PID、工具状态未知、超时/取消后容器内部进程未确认退出，立即拒绝捕获，reason=`workspace_not_quiescent`。首版不等待后台任务自行完成，不自动杀掉后继续假装工作区正常；记录残留身份并终止此编辑会话，清理整个 owned 容器。可交付的仍只是此前已封存候选。
4. 在这个“没有其他可执行写入者”的前提下，同一 helper 遍历完整清单并读取需要转运的 bytes；lstat/open/fstat 校验，拒绝链接/文件类型变化。扫描前后再次核对进程与清单；异常、不一致或读取失败均拒绝。双清单/hash 仅检测异常，**不能独自证明没有后台写入**。
5. helper 的输出经宿主完整解析、限额、hash 校验和原子产物发布后，才发布 capture。此时释放锁。初版串行查询/编辑，A 建完索引后再发布新 index；模型看不到正在构建的一半结果。

这里的边界是受控容器中普通开发命令的静止点，不是对蓄意规避进程枚举/篡改测试解释器的恶意程序提供形式化证明。适配器验收必须有后台持续写、派生子进程、命令 CLI 结束但容器子进程仍活跃的用例；如果无法可靠确认静止点，必须 fail closed，不能降级为仅比较 mtime/hash。以后若产品确需后台服务/构建并行，再讨论进程监督或真正文件系统快照，不把它塞入本轮。

不要未经验证改用 `docker pause` + `docker cp`：当前 `/workspace` 是 tmpfs，Docker 官方文档明确列出 `cp` 对 tmpfs 的限制，暂停的容器也不能执行 `exec`。这种替代没有在 P0 证实，不作为接口前提。[Docker cp](https://docs.docker.com/reference/cli/docker/container/cp/)、[Docker exec](https://docs.docker.com/reference/cli/docker/container/exec/)。本轮只核对文档，没有重复 Docker 小测。

### 3.2 数据量与已有 export_delta 的边界

现有 `export_delta()` 证实可在运行中调用、不会关闭容器，但它忽略 mode/symlink，且没有上述屏障。它可以作为受静止屏障保护后的 bytes 差异传输复用点；外层必须另有完整清单/政策检查，并验证 materialize 后身份。不能把其 `skipped_files` 数量当覆盖证明。若修改旧方法会影响冻结入口，优先在新适配器中新增 helper，旧方法保持原样。

v1 默认：候选增删不超过 200 文件，变更原始 bytes 不超过 4 MiB；完整 manifest 最多 10,000 项，基础受检 bytes 最多 64 MiB，Python 转运正文最多 8 MiB；每项超限显式拒绝。manifest 与 blob 分块传输，不通过普通 Docker `validate_files` 的 8 MiB 全树 seed 路径。Sphinx 原 archive 约 16 MiB，因此副本从同镜像 seed，再应用 delta。

旧导出 5 MiB 控制输出上限不能容纳所有接近 4 MiB 的 Base64+JSON delta；新适配器需分块或采用显式足够的 wire quota，验收临界值。每个内部控制调用以 `min(内部上限, 共享剩余时间)` 执行，120 秒导出/180 秒 copy 等历史固定上限不能突破新任务期限。失败产物可检查，不能部分应用。

## 4. A 的模块接口

拟新增 `src/retrieval/workspace.py`；只依赖纯数据契约和注入的 BlobReader/encoder。

```python
class WorkspaceIndex:
    def update(self, capture: WorkspaceCapture) -> IndexReceipt: ...
    def search(self, query: str, k: int) -> RetrievalResponse: ...
# IndexReceipt: corpus_id, index_id, capture_id, candidate_id,
#               files/chunks, rebuilt/reused/encoded/deleted计数, mode, exclusions
# RetrievalResponse: 同上版本 + hits(path, file_sha256, material_kind,
#                                    symbol, start_line, end_line)
```

更新先在临时状态重建 BM25 统计和所需向量，再原子交换当前索引；失败返回 `unavailable`，不把旧索引当新结果。删除撤销全部索引项；重命名删+增；常规写工具和 `run_command` 同样在下一次查询前捕获。时间戳只加速，内容 hash 判定复用。结果位置属于该 capture，后续读取改变需提示版本已变，不能承诺永远有效。

新缓存命名空间 `workspace-v1`，按实际编码文本 hash + 模型 revision + 预处理/长度/精度隔离，明确 device 和 cache root。无 encoder 可用则报错；只有显式 `mode=bm25` 配置才运行 BM25，输出不得仍称 hybrid。P2-A 局部测试用确定 encoder；真实 encoder 的小检查另行排期，不等同模型修复实验。

初版复用 chunk_source 等现成纯函数；新的排序/编码适配可放新文件。P2 不必抢先提取/改写 `scripts/cr_eval.py` 或 `serve.py`，避免改变冻结排序。提取公共能力如确有需要，由集成人在兼容用例下串行实施。P3 用新 workspace tool adapter 调用此模块，保留原 frozen tool 的文案、结果类型和默认行为。

## 5. B 的公开检查、执行副本与判定

### 5.1 冻结对象

拟议 `PublicValidationSpec` 在启动前由公开配置创建，禁止模型传命令/选择器/环境作为 `validate_patch` 参数。字段如下：

| 字段 | v1 约定 |
| --- | --- |
| `schema_version/spec_id/check_id` | `public-check-v1`；check_id 由规范配置、冻结文件清单、runner/parser/策略版本和本次基线清单形成 |
| `provenance` | 仓库/commit、公开路径、问题来源、冻结时点；不得含 private spec 引用 |
| `protected_manifest` | 整个 `tests/**`（pytest 为 `testing/**`）含 fixture/数据，根及递归 conftest、pytest 配置、项目配置、依赖声明及选中测试的支持材料 |
| `selectors/cwd/argv_template` | 冻结选择器；cwd=`/workspace`；只能宿主填 operation 产物路径，不能拼任意 shell |
| `env` | 显式白名单：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`PYTHONPATH=/workspace:/workspace/src`、`PYTHONDONTWRITEBYTECODE=1`，及 profile 固定 PATH/LANG/HOME/TMPDIR；不继承 PYTEST_ADDOPTS 等宿主值 |
| `runner_id/parser_id` | 可信 runner/plugin 字节 hash + 协议版本；`pytest8-v1` 首发、`pytest45-v1` 第二适配；缺适配时不运行/不判通过 |
| `coverage` | `regression_only`、范围说明、`target_evidence=null`；人工 fixture 明确 `fixture` |
| `baseline` | 完整 collection nodeids、每节点 phase/outcome/skip原因、已知失败的公开分类；不默默接受未分类失败 |
| `limits/policy` | 单次测试期限、输出限额、允许的基线 skip/xfail、collection 一致性、清理要求；全部参与 check_id |

先以未最终绑定 baseline 的 spec 建立基线 receipt，再由配置 hash + 基线 nodeids/分类生成最终 check_id，避免循环 hash。v1 的两个真实 profile 要求基线非空且通过；环境/收集失败或未分类失败则预检不启动求解。纯机制 fixture 可以显式标注公开目标失败，验证“已知失败→修复”；未修复时仍是失败，不能因基线也失败就算通过。真实目标检查的新增是新 spec/新基线，不从隐藏评分补充。

### 5.2 验证副本

拟议 `ValidationExecutor` 用同 clean digest 创建新的 `BaselineImageBackend`；测试数据不进入编辑容器之外的任意宿主 shell。顺序固定：

1. 校验 capture 的全部引用/hash、admissible、共享预算和 frozen spec；记录意图并预留额度。
2. 创建有唯一 operation label/容器 ID 的新副本，核对实际基础树与 base_manifest_id，清除规则指定的生成缓存。
3. 安全应用 changed/deleted；拒绝越界、symlink、特殊文件、文件/目录祖先冲突、重复或 changed/deleted 重叠。写入后重新计算 manifest，必须等于 capture.manifest_id；不能只检查 patch 命令 exit 0。
4. 核对冻结材料与运行环境；不采用候选改过的测试、conftest、配置。v1 对这类差异直接拒绝，不构造“偷偷恢复过测试”的不同候选。
5. 通过 `ExecutionRequest(argv=..., shell_command=None)` 调用固定 runner，保存结构化报告和 stdout/stderr。测试期间不接受编辑工具；副本中新增文件、缓存与 `/tmp` 不回灌主容器。
6. 核对报告完整性/节点集合、冻结文件及候选输入前后 hash，保存证据，销毁副本并确认清理；提交唯一 record。输入被测试自改则 `inconclusive/input_mutated`，不能认证另一个执行状态。

runner 用宿主固定脚本及 pytest plugin 收集 `collection_finish`、每 nodeid 的 setup/call/teardown、sessionfinish/exitstatus；结构化产物带 operation_id、protocol、完整结束标志，宿主进行 schema/大小/数量校验。JUnit 可作辅证，不以屏幕摘要替代。Sphinx 模式还核对 pytest 从镜像安装位置加载，目标 sphinx 从 `/workspace` 加载，防止全绿但跑了另一份包；路径信息进入 receipt。

runner/plugin 存于独立控制路径，候选 delta 不得触及；传输/执行脚本和收集产物不导入 grader。冻结文件至少执行前后 hash 检查，不把同 uid 下的 chmod 只读宣称成不可篡改安全边界。此设计阻止普通的改测试/改配置/丢测试获得绿色结果；不能证明任意恶意 Python 候选无法在同解释器内伪造 hooks。pytest 仓库尤其要保留 `runner_is_candidate=true`，完成对应 parser/篡改检测用例前不能宣称与 Sphinx 有同等适配保证。更强抗恶意评分属于独立 evaluator 后续范围。

### 5.3 结果契约与通过条件

```python
@dataclass(frozen=True)
class ValidationRecord:
    record_id: str
    operation_id: str
    trigger: str                # baseline / explicit_tool / natural_end / hard_stop
    candidate_id: str
    check_id: str
    environment_id: str
    status: str                 # passed / behavior_failed / inconclusive / not_run
    reason: str
    execution_status: str       # 原 ExecutionStatus，不覆盖为测试状态
    exit_code: int | None
    counts: dict                # collected, passed, failed, errors, skipped, xfailed, xpassed
    node_report: ArtifactRef | None
    evidence: tuple[ArtifactRef, ...]
    complete: bool
    cleanup_state: str
    started_at: str | None
    finished_at: str
    budget_receipt: dict
    coverage: dict
```

所有命名字段均序列化；没有执行或捕获失败的结束报告允许 identity 为 null，不能为满足类型编造 hash。上述 `ValidationRecord` 只用于已知候选；无候选的拒绝使用独立 `ValidationAttempt`（operation/trigger/reason/预算/evidence，可空 identity），交付报告将其解释为 `not_run`。记录不可变，所谓 stale 是拿它与当前 key 比较的派生状态，不改旧记录。

`passed` 必须同时满足：真实 pytest 进程正常退出、exit=0、可信协议完整、收集集合符合冻结 baseline、每个要求执行节点具有完整 phase 记录且通过、至少一个测试体通过、没有新增 skip/xfail/deselect/missing、没有失败/错误/异常 xpass、输入身份一致、证据已持久化、验证副本清理确认。两个首批 profile 的基线 skip=0，任何新 skip 都不能通过。未来允许基线已有 skip/xfail 时必须冻结具体 nodeid 与预期，不只是容许一个总数。

| 观察 | status / reason |
| --- | --- |
| 完整运行中的断言失败，节点/输入身份有效 | `behavior_failed/test_failure`；允许有限模型反馈 |
| collection/import/setup 环境问题、未知错误 | `inconclusive/collection_error` 或 `environment_error`；不冒称目标行为失败 |
| 零测试、全 skip、缺失节点、选择器减少、额外 skip/xfail | `inconclusive/no_effective_tests` 或 `coverage_changed` |
| 超时、取消、输出/报告不完整、结构化报告与进程状态矛盾 | `inconclusive/timeout|cancelled|evidence_incomplete|report_mismatch` |
| 输入变动、protected 改动、不支持的 mode/依赖修改 | `inconclusive/input_mutated|protected_change|unsupported_change` |
| 没有开始执行且预算/预检/导出阻塞 | `not_run/<明确原因>` |

失败后因 teardown/报告中断导致部分证据缺失可保存已观察 failure，但最终 status 仍为 inconclusive；完整性优先。shell 管道 `pytest | tail` 的 0 不用于认证，模型自己的 run_command 仅普通观察；只有上述服务产生有效 record。展示型 `--fixtures`/打印探针不是测试通过，首版不把其输出纳入 check。

## 6. 验证服务、预算及主动工具

拟议模块接口（B 可先在循环外实现）：

```python
class ValidationService:
    async def validate(self, capture, spec, environment, *,
                       trigger, operation_id, control) -> ValidationRecord: ...
# control = deadline + cancel + RunOutputMeter + artifact quota + ValidationLedger
# Executor/BlobReader/EventSink 通过构造器注入；不创建新 AgentLoop/ModelBudgetLedger。

class CompletionController:
    async def check(self, trigger, *, allow_feedback, control) -> CompletionDecision: ...
# decision: action(end/continue/stop), candidate_ref, validation_ref,
#           feedback(有界文本或null), reason, delivery_state
```

服务先查同 `validation_key` 的已提交完整 record，匹配可复用 passed/behavior_failed；执行次数不再扣，复用事件记录 source_record_id。inconclusive/未知执行默认不自动重试，not_run 不缓存为有效执行。重复 operation_id 必须返回同一结果或显式 pending/unknown，不启动第二个容器。主动工具与自动 gate 共用 ledger/cache。

默认额度：基线 1 次单列、候选验证最多 3 次、自动失败反馈最多 2 次；全部消耗同一任务时间/输出/产物上限。前 2 个候选槽可主动使用，最后 1 个留给终止结算；显式工具在只剩保留槽时返回 `not_run/final_slot_reserved`。实际执行已启动的失败/未知也扣次；未开始副本的拒绝只记 attempt，不把循环拒绝变成无限重试。

自然结束的检查优先使用普通槽；使用最后保留槽时本次决定只能结束，不能再反馈后允许编辑出一个无法复验的版本。已使用普通槽的 gate 只有在仍留有后续验证槽和完整时间预留时才可继续。纯 capture/索引不扣测试次数，但仍计入共享时间/产物配额。

必须在启动前预留最终结算的 **执行时间和产物空间**。拟议默认上限：capture 20s、创建副本 20s、应用/核对 10s、测试 90s、取证 10s、清理 12s，即 `finalization_reserve_s=162`，写入配置并服从绝对总期限。此值是首版保守策略，不是 P0 性能保证。模型/普通工具工作使用 `agent_deadline = run_deadline - reserve`；每步的实际 deadline 取 agent_deadline/step limit 较早者。进入预留窗口即停止新的模型/编辑工作，切换宿主结算。基线与初始化也计总时间；剩余不足预留则预检拒绝启动求解。

预留量不是额外时间：单次阶段超时立即终止，不能借“还有总时间”不断续跑。输出/artifact ledger 同样预留一份最终 delta、manifest、结构化报告和有限日志空间，普通工具不得耗完；旧固定配额保持原样，新模式独立配置真实字节预算。无法保留完整 stdout/stderr 时明确 truncated，v1 保守记 `inconclusive/evidence_incomplete`；结构化结果丢失/截断也不判通过。宣称完整日志必须确实保全，否则报告证据限制。

总 deadline 或取消后只允许既有运行机制的有界清理宽限（拟议最多 12s，所有 owned 容器合计），不借宽限导出新候选、执行测试或调用模型；实际超出求解期限的清理耗时单列。若进程或容器仍无法确认移除，记录 cleanup_failed/残留 ID，不能无限等待，也不能算正常完成。

模型调用/token/费用/重试仍共用原 ModelBudgetLedger。首版不保证保留足够模型调用来修复；只在 gate 失败后检查尚有可用调用、可容纳反馈的上下文、反馈槽、执行时间时继续。没有模型余额或已 context_overflow 时，宿主仍可在执行预留内验证、记录、导出，但**不能凭空产生下一轮修复**。不得重新 run 一个新 agent、重置 step/call/token 上限或仅重设 expires_at。

`validate_patch` 工具参数为 `{}`，side_effect=`NON_IDEMPOTENT`。它创建验证副本/产物，即使不改编辑树也不是 read_only。主动调用保留真实 tool_call_id；服务记录独立 operation_id。有效 record（包括 behavior_failed）映射 `ToolResult.status=SUCCEEDED`，表示验证服务完成，output 明确写测试 status、身份、计数、覆盖和 record_ref；基础设施失败/取消/超时才映射相应 ToolStatus。工具 success 不能用于判断测试通过。首版无需修改旧 ToolResult dataclass；结构化字段在专用验证事件/record，output 用稳定紧凑 JSON/文本引用。

## 7. loop/run/finally 接线与结束状态

当前无 tool call 分支直接 DONE；`run()` 负责 backend 生命周期，`_close_backend()` 导出/销毁；`_result()` 在无 backend 时还可直接 finish。P3 只在启用新 mode 的 Docker 路径接线，保留旧分支。必须拦住正常结束和所有硬停止，不仅修改无 tool call 的那一处。

```python
# 拟议：仍是一个模型循环；以下省略现有统计/错误分支。
async def run():
    try:
        await start_and_public_baseline()
        provisional = await original_loop_with_natural_end_gate()
        # max calls、context overflow、step limit、parse error 等也汇聚这里
        await finalize_once(provisional)  # seal editing; capture/validate if eligible
    except CancelledOrDeadline as stop:
        record_stop_without_new_validation(stop)
    finally:
        # finally 不调用模型、不无条件启动测试；仅结算已有操作/清理
        await bounded_cleanup_owned_containers()
    await commit_delivery_and_run_finished_once()

async def on_no_tool_calls(response):
    decision = await controller.check("natural_end", allow_feedback=True)
    if decision.action == "continue":
        persist_completion_feedback_once(decision)
        append_host_observation_to_messages(decision.feedback)
        return CONTINUE_EXISTING_LOOP  # 不递归调用 run()
    return provisional_end(response, decision)
```

自然结束 gate 在 DONE/plan.mark_done/run_end 之前；有失败反馈时保留自然回答作为草稿，追加宿主观察继续原循环。自动观察使用明确来源的普通消息（如 `role=user`、正文 `[Harness public validation observation]`），不伪造 tool_call_id，不把测试输出提升为系统指令。主动工具结果提交时把该 record 标为已投递观察；自动结束发现同一失败 record 时直接按失败结束，不重复反馈。自动 gate 的新失败记录也只投递一次，随后同候选重复结束不再获得修复轮次。

| 结束触发 | 完成控制动作 |
| --- | --- |
| 自然结束、当前 key 已通过 | 封存对应 capture；不重跑；准确结束 |
| 自然结束、明确失败且仍可继续 | 有界反馈，最多 2 次，仍在原循环/账本中；未修改同候选重复结束不再反馈 |
| 自然结束、无法判定或无反馈余量 | 结束并保留真实验证状态；不隐式修环境 |
| 模型调用/token/费用耗尽、context_overflow、step limit、model parse error | 未取消且编辑容器可信、剩余执行时间充足时捕获并验证；只结算，不再调用模型；保留原硬停止原因 |
| agent_deadline 到达预留窗口 | 不再编辑，完成一次最终候选结算；不是延长 run_deadline |
| 用户取消、async cancellation、总 deadline 到达 | 不开始新捕获/测试/模型调用；终止已启动验证并清理；已有持久候选可导出，当前未捕获变化标未知 |
| 单步/命令超时、未知后台进程 | 容器静止性未证明则拒绝 capture/验证；不能只因宿主 CLI 被杀就判安全；清理编辑容器 |
| 候选导出/产物提交失败 | `delivery=unavailable`；旧通过 record 只作历史，不认证最终候选 |
| 清理失败 | 保留已观察测试结果，但交付状态不完整/运行清理故障；不报全部成功 |

一个 gate 完成并 seal 后没有新的编辑，outer finalizer 可直接引用该 capture；若 gate 反馈继续过或其后执行过任何可能写操作，必须重捕获/重新比较身份。最终选中的封存 capture 就是交付物，不在 close 时偷偷换成另一份后端快照。

新模式关闭 backend 用 `snapshot=False`，已封存候选由新接口交付；这避免 finally 再做一个超时导出覆盖候选。原模式的 `snapshot={}`、`execution.snapshot`、`candidate_hash` 及旧 runner 处理保留，不给旧占位字段重新赋义。新报告把 `workspace_v1.candidate_id`/record 引用放独立字段，且新模式不再使用旧 candidate_hash 作为证据。

具体兼容点不能漏：`run()` 现有的“snapshot is None 且 success ⇒ container_terminated_before_snapshot”判断、旧 `execution.snapshot` 发布和 candidate_hash 生成，仅走旧模式；workspace-v1 改检查已提交 capture/交付状态，不用伪造 `{}` 绕过旧判断。`_run_loop` 的 `run_end` 事件及 finally 中 `event_bus.close()` 在新模式延至宿主结算/清理后，避免用户先看到成功后又失败，或 finalizer 无法发事件。durable `run.finished` 仍由最外层最终提交一次。

最终机器报告拟分四组：

运行 `status` 采用 `completed/budget_exhausted/context_exhausted/cancelled/deadline/execution_failed/interrupted`，具体原枚举/错误保留在 `reason`；交付采用 `exported/no_changes/unavailable/incomplete`。新字段只用于 workspace-v1 报告，不重命名旧 `AgentRunResult.success/failure_reason`；旧 success 仍是旧运行语义，不升级为测试或解题认证。

```json
{
  "run": {"status": "budget_exhausted", "reason": "max_model_calls"},
  "delivery": {"status": "exported", "candidate_id": "candidate-v1:...", "delta_ref": "..."},
  "development_validation": {"status": "passed", "coverage": "regression_only", "record_id": "..."},
  "independent_evaluation": {"status": "not_evaluated", "solved": null}
}
```

运行正常结束、公开检查通过、独立解出互不替代。空 delta 可以是有效基础候选且检查通过，但报告 `delivery=no_changes`，不得称已形成修复。没有最后一次可靠捕获时只列 `last_captured_candidate`，明确 `final_candidate_unknown`；不能把上次版本改名为最终版。

## 8. 会话事件与首版恢复承诺

复用现有 RunSession/ArtifactStore 的原子提交、事件序号和引用，不另建日志数据库。现有 reducer 遇未知事件会拒绝，必须由集成人同时增加事件与 reducer；自动验证不能借 `tool.result`，因为该事件会自动追加 tool 消息。

| 拟议事件 | 最少内容与顺序 |
| --- | --- |
| `workspace.capture.started` | operation_id、container身份、规则、预留/期限 |
| `workspace.capture.committed` / `.failed` | 完整 manifest/delta/blob 引用及 candidate_id，或失败/残留证据；提交前不可见 |
| `validation.reserved` | operation/key/trigger、attempt序号、预算预留；先于容器副作用 |
| `validation.started` | 副本名字/ID、环境、runner/spec引用；启动名字须在 create 前持久化 |
| `validation.committed` | 唯一完整 record_ref、结算账本、cleanup；失败/取消/未知也显式记录 |
| `validation.reused` / `.not_run` | source_record_id 或原因，不消耗执行槽 |
| `completion.feedback` | record_id、投递序号、消息正文引用和新 feedback_count；同记录最多一次 |
| `completion.sealed` | 原终止原因、选定 capture/record、交付字段；无新工具/模型工作 |
| `run.finished` | 清理后最终报告；最后事件，至多一次 |

record/blob 先完整落盘，再提交引用事件；预算结算和 record 引用在同一事件内提交，避免结果在但额度又重扣。`completion.feedback` reducer 必须恰好追加一次普通宿主消息，不靠重复恢复代码拼接。对于主动验证，`tool.intent/result` 只表示模型工具交互，`validation.*` 是同 operation 的子操作；两个层次不能重复扣次。中断 reconcile 不得把未完成 validate 工具当 read_only 重放。

**MVP 恢复定义：Docker 中断会话的继续求解明确拒绝。** 维持现有 `AgentLoop.run` 的拒绝，不能创建新容器后“接着跑”。P2 的服务可验证持久记录幂等，P3 的恢复验收是安全拒绝/只读可检查，而非透明恢复。

| 崩溃位置 | 可检查/导出的内容 | 首版动作 |
| --- | --- | --- |
| capture 提交前 | 已有历史 capture、失败/意图事件；可能有未引用临时 blob | 当前候选未知；不拼接半份 delta |
| capture 已提交、validation 未启动 | 该版本完整 delta/manifest、基点和限制 | 可只读导出该封存版本；不自动验证或恢复编辑 |
| validation 已 reserved/started、无 committed | 候选、操作/容器ID、已保存日志；结果未知，额度按占用记账 | 不判通过，不重新执行；显式诊断残留 |
| record 已提交、未 sealed/finished | 完整验证记录、绑定候选与预算 | 只读报告“验证已提交、运行中断”；不重复执行/调用模型 |
| run.finished 已提交 | 完整最终报告/候选/日志 | 原终态 replay 可复用，仍不启动后端 |

新增只读 inspect/export 不依赖运行期限仍有效，因此即使现有 `resume` 因 expires_at 拒绝，也应可通过 replay/artifact 校验查看。导出到用户指定输出目录，仅复制已提交 hash 验证过的产物；不会重新访问遗留容器恢复“最后未保存修改”。清理工具可按 run label+精确容器 ID 检查/清理 owned 残留，不能扫杀其他任务容器；只读检查不会自动执行清理或测试。未知执行/清理不通过重放事件伪装成完成。

后续真正续跑需显式协议：重建已封存树、核对材料/环境/工具上下文、确认旧进程及清理、恢复绝对期限和全部模型/验证账本、处理 pending model/tool/feedback、用户可理解的数据丢失边界。那是额外范围，不以已有 snapshot 字段或 session JSON 冒充。当前不需要先实现它才做 A/B。

## 9. 两条端到端序列及共同 fixture

共同小仓库（人工 fixture，不是新修复实验）：`pkg/calc.py`、`tests/test_calc.py`、`pytest.ini`、`data/rules.json`；基础测试断言 `add(1, 2) == 3`。基础 calc 故意返回 0，spec 明确公开目标 fixture；测试/config/data 全冻结，可改 `pkg/*.py`。初始 capture `C0` 与两个候选 `C1/C2` 由同一纯契约 fixture builder 产生，预期 hash 用独立序列化向量检验，不靠生产 hash 函数给自身生成期望。

**序列一：主动验证成功再结束。** 模型修改 calc 返回 `a+b` → capture C1 → A 更新对应源码位置 → `validate_patch` 真实 call_id → B 对 C1 新副本执行冻结测试、清理并提交 V1 → 工具返回 passed/fixture → 模型无 tool call → gate 捕获仍为 C1，复用 V1，不扣第 2 次 → seal C1 → 关闭编辑容器 → run.finished。审查者能沿 report→V1→C1→delta/测试清单打开证据。

**序列二：失败反馈、修改后结束。** 模型改为 `a-b` 并自然结束 → gate capture C1、执行 V1 behavior_failed → 记录唯一 feedback、继续同一循环 → 模型改为 `a+b`（C2）→ 再自然结束 → V1 key 与 C2 不同，执行 V2 passed → seal C2 → 清理并结束。若 V1 后模型预算已耗尽，则结束为原预算原因+C1失败，不产生 C2；若最后一次检查期间取消，则结算 inconclusive/cancelled，不自动再跑。

A/B 可共用相同捕获 fixture，但 B 不引用 A 的 index_id 判身份。改 `data/rules.json` 的 fixture 应改变 candidate_id 而不改变 corpus_id，同时触发 unsupported_change；用于检查“检索没变≠可执行候选没变”。

## 10. 文件所有权与 P2/P3 接线

| 所有者 | 可独立新增文件 | 不得自行修改 |
| --- | --- | --- |
| 集成人：P2 前最小基点 | `src/workspace/{__init__,contracts}.py`；`tests/test_workspace_contracts.py`；共同 fixture JSON/blob | 不为这个基点修改任何冻结入口 |
| 集成人：串行基础层，可与模块开发重叠 | `src/workspace/{capture,baseline_capture}.py`；静止检查/捕获 helper；`tests/test_workspace_capture.py` | A/B 不绕过它新增第二种 candidate 定义 |
| P2-A（Sol） | `src/retrieval/workspace.py`、`workspace_encoder.py`、必要新排序适配；`tests/test_workspace_retrieval.py` | `serve.py/chunking.py/embed.py` 的冻结语义、全局缓存、factory、tool接线 |
| P2-B（Sol） | `src/validation/{__init__,contracts,service,pytest_runner,parser,replica,ledger}.py`；`tests/test_public_validation_*.py` | loop/session/recovery/factory、旧 execution、隐藏 grader/config；生产 capture 由基础层提供 |
| P2-C（验收） | `tests/test_workspace_acceptance.py`、`tests/fixtures/workspace_acceptance/**`、`scripts/inspect_workspace_run.py` | 不修改 A/B 实现来配合测试，不和模块局部测试重名 |
| P3 集成人 | `src/runtime/completion.py`、`src/tools/workspace_retrieve.py`、`src/tools/validate_patch.py`、`scripts/run_workspace.py`、新公开 profile 配置 | 所有共享改动串行审查 |

拟议共用接口变更由集成人统一版本化，不能由 A/B 自行改 contracts。B 的纯状态/记录 schema 在其 `contracts.py`，如 P2-C 先开工则使用本文字段和共享 fixture，不临时复制一套生产实现。P2-C 没有真实接线时只交付可运行的模块验收和明确待 P3 的场景，不将 skip 全绿算端到端完成。

P3 需要触及的已有共享文件，保持 opt-in：

- `src/harness/factory.py`：注入 capture/index/validation/controller；Docker 工具白名单新增两个受控 adapter；旧 retrieval 文案和权限保留。
- `src/config/schema.py`：可选 workspace/validation 配置、公开 profile、预算预留；默认关闭。
- `src/runtime/loop.py`：自然结束 gate、软执行截止、所有硬停止的统一 finalizer、cleanup 前 seal、新结果可选字段；旧流程不额外执行工作。
- `src/runtime/session.py`、`recovery.py`：事件/reducer、fingerprint纳入 spec/rules/环境和预算、只读检查、Docker续跑拒绝；旧 schema/旧事件兼容。
- `src/execution/docker.py`、`baseline_image_backend.py`：只有适配器确需公开受控控制调用/统一互斥时由集成人最小扩展；不默认改变旧 export/close 语义。优先新包装/子类，不让两个实现者竞争修改。
- `src/tools/base.py` 原则上不需要改；`scripts/run_single.py` 原则上保留，薄入口复用现有构建/会话；`run_swe_task.py` 和隐藏评分路径不接入 B 内循环。

独立 worktree/分支由主会话调度；GPU、Docker 真实验收串行排期。纯 fixture 不调用付费模型。公共能力提取若引起旧排序/默认预算差异，先停在集成层修兼容，不追改冻结报告以适应新行为。

## 11. 最小验收矩阵

| 阶段 / 场景 | 必须观察到的结果 |
| --- | --- |
| 契约：增删改/重命名、顺序变化、换行、mode、非 Python 资源 | 独立 hash 向量正确；序列化顺序不影响身份；内容/mode改变身份；index 与 candidate 可独立变化 |
| 捕获：命令写文件、后台 writer、残留子进程、采集中读失败 | 命令写被看见；后台/未知拒绝认证且不发布混合版本；已有候选不被覆盖 |
| 捕获：symlink 替换、特殊文件、路径穿越、delta 200/4MiB 边界、Base64膨胀 | 明确拒绝或完整传输；无静默遗漏、无部分应用；失败不等于空候选 |
| A：新增/修改/删除/重命名/行号移动/不变重查 | 新结果版本正确、删除不残留；编码/缓存计数可解释；更新失败不返回伪新版本 |
| A：encoder 不可用、显式 BM25、冻结检索兼容 | 不静默降级；新模式标明模式；旧固定输入结果/文案不变 |
| B：原候选失败、修改通过、修改后旧记录失效 | 记录可追踪 candidate/check/env；不同身份不复用；同身份复用不扣次 |
| B：改测试删除断言、加 conftest skip、改 pytest.ini/依赖、删测试 | 不得通过；理由和候选改动保留，冻结集合不被替换 |
| B：打印“passed”但未跑、管道末端0、零测试、全skip、少收集节点、收集/环境错误 | 不得判 passed；原进程状态/结构化证据/覆盖区分 |
| B：验证副本生成文件、修改输入、超时、输出截断、cleanup失败 | 编辑树不污染；输入自改/不完整/清理未知均不给完整通过凭据 |
| P3：正常完成、失败后继续、同失败重复结束 | 同循环；反馈最多2次且同record一次；正常结束先gate再DONE |
| P3：max_calls/context_overflow/step limit | 无额外模型额度；有执行预留则验证，否则明确not_run；保留硬停止原因 |
| P3：用户取消/总deadline/异步取消 | 不启动新的检查；终止在途执行、有限清理；历史版本与最终未知分开 |
| P3：final-slot/时间/输出预留、重复结束 | 普通工作不侵占保留额度；最终不重扣、不重测；绝对期限不延长 |
| P3：capture/intent/result/seal/finish各崩溃窗口 | 已提交产物可校验导出；未知不认通过；Docker续跑显式拒绝，无新容器/模型调用 |
| P3：新模式关闭、旧SWE/CR入口 | 无新增检索/验证/模型调用；原事件与冻结检索行为兼容 |

P2-B 可以先用新受控 fixture 容器验证服务，不需等待真实编辑容器 capture；真实 Sphinx 验收必须经同镜像 base+delta 和共享捕获层。pytest 4.5 不得因 pytest8 parser 的 fixture 测试通过就算已适配。真实 encoder 与 Docker 集成检查是 P2/P3 的待办验收，本 P1 没有执行它们。

## 12. 对原方案的修正与推进条件

可直接实施：A 查询边界更新/版本标记、新语料独立命名；B 公开检查冻结、独立副本、主动工具与自动检查共用服务；三类结果分离；沿用 session 与执行配额；最多 3 次候选验证/2 次反馈的默认；隐藏 grader 仅后验。

必须修正：

1. “自然结束前 gate”改为“自然结束 gate + 全退出路径结算 + 预算预留”。P0 中 3 个非空未检查候选和 1 个仅展示探针都以预算/上下文耗尽结束；只拦正常 completed 会漏掉已知需求。
2. “snapshot/hash 已有”改为新 candidate-v1 身份。BaselineImageBackend 的空 snapshot 占位不是候选 bytes；不追认旧日志、不改旧字段含义。
3. “运行中导出可用”不等于一致快照。首版仅接收可信静止点，后台/未知拒绝；不把缓存/原生/config排除后继续认证。
4. “测试命令通过”改为固定材料、真实进程、完整节点报告及覆盖要求；未解决目标的回归绿色不等于 solved。
5. “可恢复”收窄为持久记录幂等、已提交产物可检查、Docker中断续跑安全拒绝。透明恢复是以后单独范围，不以 P3 名称暗示已实现。
6. 首批按最终环境报告采用 Sphinx-8721 的小回归主线，pytest-5103 第二适配；不继续沿尚未核实的 Sphinx-10614/scikit-learn 环境做保证。

P0 已观察到失败后的修改/复测；旧候选绑定未知不等于已证验证错误。本轮验收首先证明未来验证与交付可以核查，不承诺修复率提升，也不启动同期付费对照。

**现在可并行：** 纯契约基点完成后，A 的索引/编码适配、B 的记录/parser/副本/预算服务、C 的黑盒 fixture。**必须串行：** 纯契约及共同 fixture、静止捕获/真实候选材料化基础层、factory/loop/session/recovery/config 接线。捕获与公开报告完整性尚未实现是 P3 放行的实质门槛，不是阻止 A/B 模块开工的理由。

**交作者讨论的条件性取舍：** 若需要后台长期服务、原生/依赖升级或修改冻结测试，需扩大受支持 profile；若要求中断后继续求解，需另建恢复协议；若要把 regression_only 改为“目标修复”或效果实验，先建立公开目标检查/独立证据并明确预算。这些需求目前都不是首版必需。若静止捕获或测试完整性无法通过边界验收，主会话应先缩小可运行 profile/暂停真实接线，不自行把承诺降成概率性通过。

本次仅新增本文件；未改 src/tests/config、旧实验或运行产物，未 commit/push，未调用付费模型或运行新修复实验。

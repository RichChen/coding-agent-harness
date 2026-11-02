# P2-A：工作区代码检索模块结果

日期：2026-10-30（Asia/Shanghai）。工作基点：`d0bdf6f`。分支：`codex/p2-workspace-retrieval`。实现及测试提交：`38efd6c69e09627dcfd1ea9f24b0c2f7e60a5caa`；原报告提交 `89159d3`。GPU smoke 在该提交上完成，新增专属 fixture runner、JSON 与本报告修订另行提交，最终提交号由会话返回。

工作区仅为 `<worktree-A>`。依据 P1 §2/4/9–11 和共享 contracts/fixture。没有修改主工作区、共享契约、旧检索实现/缓存或运行入口。

## 1. 已实现范围

[workspace.py](../../src/retrieval/workspace.py) 只消费 `WorkspaceCapture` 和注入的 `BlobReader`，复用共享的 `corpus_rules_id/corpus_id/index_id/read_verified_blob`。不实现自己的宿主/Docker 扫描，不重新定义 candidate 身份，不把索引 ID 当候选 ID。

更新在锁内构建完整的新 BM25 统计和所需向量，最后发布完整状态。更新失败返回当前 capture 对应的 unavailable，后续 search 不返回旧索引命中；旧已核验正文缓存只用于未来重建。并发 search 与 update 串行，不能观察半份新索引。真实 capture 失败时，P3 调用方必须直接返回不可用，不能跳过捕获后查询旧状态。

新增/修改/删除均由当前完整 capture 的 Python 子集决定；重命名是删除加新增。编码文本包含 path/symbol header，因此重命名不错误复用原路径的向量。纯行号移动时，函数编码文本不变可复用向量，但位置和原始文件 SHA 来自当前 capture。非 Python 资源变化可使 candidate_id 变化，corpus/index 保持不变，响应仍关联新 capture/candidate。

复用旧纯函数 `chunk_source/chunk_header`、tokenizer、`from_token_lists`、`max_by_file/order_by_score/ranks_from_order/rrf`，没有调用冻结 CR 缓存的构造器。新排序为声明的 chunk BM25 → file max；hybrid 分别对文件和 chunk 的 BM25/embedding 全排名做 RRF，再按文件输出最多两个 chunk。路径/符号/行号确定性打破平局。没有以“新模式”名义改变 CR 原排序。

## 2. API 与消费规则

```python
from src.retrieval.workspace import IndexConfig, WorkspaceIndex

index = WorkspaceIndex(blob_reader, config=IndexConfig(mode="bm25"))
receipt = index.update(capture)
response = index.search("relative path handling", k=5)
```

- `IndexConfig.mode` 必须显式为 `bm25` 或 `hybrid`。hybrid 必须注入 `WorkspaceEncoder`，BM25 拒绝接受一个不会使用的 encoder。不静默降级。
- `update(capture) -> IndexReceipt`：含 status、capture/candidate/corpus/index ID、mode、files/chunks、rebuilt/reused/encoded/vectors_reused/deleted、exclusions/warnings、reason。失败时 corpus/index 为 None，不能将旧 ID 冒充新 ID。
- `search(query, k=5) -> RetrievalResponse`：`k` 指文件数；`.receipt` 为版本与计数，另有直接访问的 `.capture_id/.candidate_id/.corpus_id/.index_id`。`.hits` 含 path、原始 file_sha256、material_kind、score、chunks；chunk 含 symbol/start_line/end_line。
- 消费者必须检查 `response.status`。查询编码失败时，receipt 仍描述已经成功构建的索引，而 response 为 unavailable、hits 为空；不回退到词法结果。
- rebuilt/reused 按当前可索引文件计数；deleted 是上次成功索引中消失的路径数；encoded/vectors_reused 按本次文档批次中不同的实际编码文本计数。查询编码不混入更新计数。空语料是 ready + 空命中，不是编码器自动降级。
- 接受捕获层已生成的 inadmissible capture 做公开语料定位；这不表示该候选可验证或认证，B 仍需独立检查 admissible/violations。

初次更新需要各纳入 Python 文件的受控 blob。后续缺省正文仅在该实例已经核验过相同 SHA 时可复用；新内容缺 blob 导致 unavailable。即便内容已缓存，新 capture 显式提供的 blob 仍验证长度/hash，损坏不会被缓存命中掩盖。

## 3. 语料政策

`CorpusRules.to_dict()` 的完整配置经共享 helper 形成身份：单文件默认上限 1 MiB；非 Python 不入语料；`.git`、缓存、虚拟环境、build/dist、已声明私有路径等排除，Python 排除逐路径留 reason。候选完整清单仍由共享 capture 负责，A 的排除不改变候选身份或验证政策。

Python 按 PEP 263 编码声明严格解码，BOM/换行处理有固定版本；解码失败明确 exclude，不使用替换字符伪造源码。AST 失败使用旧窗口切块，并记录 `ast_window_fallback`。材料分类是可审查的路径政策：

- `conftest.py` 与 fixture/fixtures/roots/testdata 目录内 Python 为 test_support。
- `test_*.py`、`*_test.py` 为 test。
- test/tests/testing 内其他 Python 为 test_support。
- 其余入选 Python 为 source。

该分类便于解释结果，不能证明代码是否具有测试行为。路径/常规文件/链接/特殊文件的捕获安全性属于基础层契约；A 不调用文件内容、不追踪链接或自行访问主机根目录。

## 4. 编码器与缓存

[workspace_encoder.py](../../src/retrieval/workspace_encoder.py) 的 `WorkspaceEncoder` 接受不可变 `EncoderConfig` 和惰性 backend_factory。确定性测试注入内存 backend，导入本模块不会导入 torch 或 sentence_transformers。

缓存空间仅为显式 cache_root 下 `workspace-v1/vectors/<encoder-identity>.sqlite`；未传 cache_root 时仅内存缓存。身份包含 model、固定 40 位 revision、device、precision、max_length、query/document prefix、预处理版本、adapter 版本、输出/缓存精度、运行库版本。键是加 prefix 后实际输入字符串的 UTF-8 SHA256；不会直接沿用 raw chunk hash。缓存 float32 little-endian 单位向量，逐记录校验长度、SHA、维数及有限/单位范数。损坏或模型输出错误显式失败，不静默改写或降级；单个失败编码批次不提交新向量。

缓存可保留成功编码但尚未发布索引的文本，这些只是按内容寻址的可复用向量，不表示新索引已经发布。索引及正文缓存当前仅在进程内，没有建立新的跨会话恢复协议或磁盘索引格式。

### 4.1 为什么先支持 Qwen

真实适配器 `LocalSentenceTransformerBackend/local_qwen_encoder` **只支持 Qwen，不是沿用 CR 最终选择的 Jina**。这是初版加载路径的主动简化：已有 Qwen spec 为 `trust_remote_code=False`；冻结 Jina 依赖另一个固定 revision 的自定义模型代码仓库，需额外校验离线代码加载链路。没有为完成本模块而扩大到该链路，也没有修改/替换 CR 冻结模型、向量库或比较其质量。

Qwen 的模型及 query prefix 取自既有 `embed.MODELS['qwen3-0.6b']`：

| 项目 | 本次只读实查 |
| --- | --- |
| 模型 | `Qwen/Qwen3-Embedding-0.6B` |
| revision | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| 本地 snapshot | `C:/Users/Administrator/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B/snapshots/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，实际存在 |
| 模型文件 | `model.safetensors` 存在，1,191,586,416 bytes；`modules.json`、`config.json`、`tokenizer_config.json`、`tokenizer.json` 均存在 |
| 本地提示配置 | config_sentence_transformers.json 的 default_prompt_name=null；wrapper 显式使用既有 code-retrieval query prefix，文档 prefix 为空 |
| 运行依赖 metadata | numpy 2.5.3；torch 2.14.1+cu126；sentence-transformers 5.7.0；transformers 4.57.6；tokenizers 0.22.2 |

加载器要求 snapshot 目录名等于固定 revision，显式 local_files_only=True、trust_remote_code=False、device、dtype、max_seq_length，并校验当前库版本与 encoder 身份一致。首次提交仅核对文件存在、大小与配置；随后另获授权，已完成第 6 节真实加载/编码。没有重新哈希整个模型；本地 snapshot 的供应/内容可信性仍属已准备模型资产的前提。该适配器拒绝不存在路径，不回退到 Hub 名称下载。

## 5. 验证结果

最终确定性与兼容检查 **243 passed**（3.96s），包含新增索引/编码器测试、共享契约测试和旧纯函数、缓存隔离与 CR-2 serve 测试。该 pytest 检查没有新增 skip、没有运行原实验数据集或真实模型。后续 GPU smoke 没有修改生产模块，因此未无变化重复这 243 项。

```powershell
Set-Location <worktree-A>
# 第一次先确保 pytest 临时目录的父目录存在。
New-Item -ItemType Directory -Path .pytest_cache -Force | Out-Null
& D:/crq/src/coding-harness/.venv/Scripts/python.exe -m pytest tests/test_workspace_retrieval.py tests/test_workspace_encoder.py tests/test_workspace_contracts.py tests/test_retrieval_bm25.py tests/test_retrieval_chunking.py tests/test_retrieval_tokenize.py tests/test_retrieval_fusion.py tests/test_retrieval_embed.py tests/test_retrieval_isolation.py tests/test_cr2_serve.py -q --basetemp=.pytest_cache/p2-a-tmp-5 --tb=short
```

语义覆盖包括共享 C0/C1/C2、非 Python 变化、新增/修改/删除/重命名、纯行号移动复用、BM25 新旧 DF 重建等价、RRF 独立期望值、文件类型/编码/大小边界、AST fallback、损坏/缺失 blob、编码维度/NaN/零向量、完整配置缓存隔离、SQLite 重开与损坏拒绝、并发发布、更新失败不返回旧命中、查询失败不词法降级、真实加载器参数的纯 stub 检查，以及旧冻结 serve 排序/文案兼容。

首次 pytest 因 `.pytest_cache` 父目录尚不存在而出现 tmp_path setup errors；补建本 worktree 内目录后消除。随后一项向量复用测试把新增空行后的模块文本误当作不变，修正为两个实际输入变化，并补纯行号移动时函数文本复用的断言。最终以上命令全部通过，没有更改共享测试或旧测试来适应实现。

已验证实际解释器为 `D:/crq/src/coding-harness/.venv/Scripts/python.exe`，两个新模块的 `__file__` 均位于本 worktree；新模块独立导入检查时 torch/sentence_transformers 均不在 sys.modules。最初只读检查了已安装 SentenceTransformer 源码中的 local_files_only/model_kwargs/encode 参数；后续 GPU smoke 则实际调用该构造器。

## 6. 已授权的真实 GPU 离线 smoke

主会话收到首次结果后，明确授权一次现有本地 Qwen GPU 检查，并允许注入 WorkspaceIndex 检查共享 C0/C1。已执行一次，exit 0；实际时间为 2026-10-30 02:04:39–02:07:50（Asia/Shanghai；JSON 保留 UTC 时间）。未下载/安装，没有 solver API、Docker 或旧 CR 缓存访问。

小证据为 [qwen-gpu-smoke-01.json](evidence/p2-a/qwen-gpu-smoke-01.json)；可复现 runner 为 [gpu_smoke.py](../../tests/fixtures/workspace_retrieval/gpu_smoke.py)。runner 不会被 pytest 自动收集，且在导入模型栈前要求两个 offline 标记；缓存、输出限定在当前 worktree。JSON 仅保存配置、输入 hash、计时、范数和两版检索记录，未存巨大权重/向量缓存或绝对主机路径。执行时 runner 的原始字节 SHA256 为 `5960499b44a5ca85bcba83b8c447e276dcccfedcbe48d1956532db0f57c3e2f7`；生产代码基点与 runner hash 同记在 JSON。

| 实际观察 | 结果 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 2080 Ti，compute capability 7.5，显存 11,810,701,312 bytes |
| 模型/精度 | 上列固定 Qwen revision；实际参数均为 torch.float16，device=cuda，max_length=512；wrapper 输出/缓存为 float32 |
| 加载耗时 | 148.386332 s，含首次 SentenceTransformer 加载及权重到 GPU；不含最外层 torch 导入 |
| 第一次文档编码 | 2 inputs，1024 维；backend 编码 41.861899 s；含加载的整个 encode 调用 190.438549 s |
| 后续 backend 编码 | 单独查询 0.077332 s；C0 的 4 个 chunk 0.120791 s；索引查询 0.076771 s；C1 的 1 个新 chunk 0.077879 s |
| finite/unit | 独立文档、查询及 C1 的 4×1024 矩阵全部 finite，归一化范数通过 atol=1e-5；具体范数见 JSON |
| 内存及磁盘复用 | 两文档内存重查 encoded=0/reused=2，0.000559 s；关闭后重开 SQLite，同样 0/2，0.001238 s，向量逐元素完全一致且禁止加载 backend 的工厂未触发 |
| GPU 峰值 | allocated 1,217,067,008 bytes，reserved 1,235,222,528 bytes；仅本次单进程测量 |
| 警告/修正 | 出现 torch_dtype 参数弃用提示，执行成功；没有为此修改生产模块或扩大支持模型 |

首次编码有明显冷启动开销；只有一次小运行，不能将后续小批次耗时写成稳定吞吐、服务延迟承诺或性能对照。

真实 encoder 注入 hybrid WorkspaceIndex 后：C0 是两文件/四 chunk，encoded=4；C1 修改 calc 的函数正文，rebuilt=1/reused=1、encoded=1/vectors_reused=3。两次响应均 ready，capture_id/candidate_id 与相应共享 fixture 一致，所有 hit.file_sha256 匹配该版清单；candidate/corpus/index 三种内容身份均随该修改变化。再次提交同一 C1 则 rebuilt=0/reused=2、encoded=0/vectors_reused=4，查询也命中缓存，没有额外 backend 调用。

这两版是人工 C0/C1，C1 返回 a-b 而非成功修复。此处没有执行其测试，也没有给候选颁发验证通过；这里只验证版本关联与真实向量缓存更新。JSON 中的命中顺序/相似分数不能作为排名提升证据。

实际命令如下。缓存目录当次为空；再次复现必须换新的 cache-root 编号，不能复用 01 后仍期待首次 encoded=2。

```powershell
Set-Location <worktree-A>
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:HF_HOME = "$PWD/.pytest_cache/p2-a-hf"
$env:TORCH_HOME = "$PWD/.pytest_cache/p2-a-torch"
& D:/crq/src/coding-harness/.venv/Scripts/python.exe tests/fixtures/workspace_retrieval/gpu_smoke.py --snapshot C:/Users/Administrator/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B/snapshots/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3 --cache-root .pytest_cache/p2-a-gpu-smoke-01 --output docs/roadmap/evidence/p2-a/qwen-gpu-smoke-01.json
```

离线标记从进程启动前设置，loader 仍显式 local_files_only。snapshot 读取使用已存在的绝对目录，工作缓存写入 `.pytest_cache`，不写入历史 CR 向量库。进程已退出并释放模型；没有留下常驻 GPU 服务。

## 7. 集成前未验证项

真实模型在一台 GPU 上的单次小输入加载/编码、共享 fixture 索引关联已证实；稳定性能、规模资源、真实容器捕获、完整副作用隔离、入口/factory/工具接线、运行中查询前捕获和失败处理、容器路径身份、P3 完成控制与恢复仍未验收。基线测试/隐藏 grader 不属于 A。没有真实 CaptureProvider 或 agent E2E；未做检索质量比较，不能把新 hybrid 排名或 Qwen 选择写成效果提升。Qwen 与冻结 CR Jina 的模型差异仍保留。

主会话可按实现提交加本报告提交集成；无需修改共享 contracts。全新工作区实例的首份 capture 应提供完整入选 Python blobs；持续查询必须由共享捕获层取得新 capture 后调用 update。共享 BlobReader 的权限/资产生命周期、捕获静止性与真实文件完整性，由基础层负责。本分支未 merge/push。

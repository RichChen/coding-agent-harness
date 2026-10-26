# CR-1 合同：SWE-bench Verified 离线代码检索

日期：2026-10-18。读者：Cursor 执行模型。总体背景见 [PLAN](PLAN.zh-CN.md)，先读第 2 节的边界。

本轮不调用任何付费模型 API，不跑 agent，不用 Docker。只用本地 CPU/GPU 和公开 git 仓库。

## 0. 开工前

- 当前分支 `main`。每完成一步提交一次，提交信息以 `CR-1:` 开头。
- 不修改：`src/runtime/`、`src/tools/`、`src/harness/`、`scripts/run_*.py`、`docs/sprint/` 以及 A5/A6 相关文件。新代码只放在下面列出的位置。
- 开工前的全量 pytest（`515a7e8`，约 25 分钟）是 654 passed、24 skipped、5 failed。这 5 个与本轮无关，不要修，检查点里与这份基线对照，只报告新增失败：
  - `tests/test_command.py::TestRunCommandToolArgv::test_shell_path_still_works`
  - `tests/test_compact.py::TestEventBus::test_context_compacted_event_in_agent_loop`
  - `tests/test_dataset_oracle.py::test_dataset_oracle_script_passes_for_new_tasks`
  - `tests/test_runner.py::TestEnhancedJudges::test_command_success`
  - `tests/test_runner.py::TestEnhancedJudges::test_command_output_requires_exit_zero_by_default`
- 日常验证只跑 `pytest tests/test_retrieval_*.py`；全量测试每个检查点跑一次。
- 每个检查点写完后**停下**，等审计，不要继续下一步。

## 1. 文件布局

```text
src/retrieval/
  __init__.py
  dataset.py      # 读 Verified parquet；产出 QueryView 与 LabelView 两种视图
  repo_cache.py   # bare clone；按 commit 列文件、读 blob
  chunking.py     # AST 切块与按行窗口退回
  tokenize.py     # 代码感知 tokenizer
  bm25.py
  embed.py        # 只在这里 import torch / sentence_transformers
  fusion.py       # RRF 与文件级聚合
  labels.py       # 唯一允许读 patch 的模块
  metrics.py
scripts/
  cr_prepare.py   # clone、核对 commit、划分、语料统计
  cr_index.py     # 切块、tokenize、向量的缓存构建
  cr_eval.py      # 跑检索器、算指标、写结果
tests/
  test_retrieval_*.py
```

缓存目录用环境变量 `CR_CACHE_DIR`，默认 `D:/crq/cache/cr`，放在仓库外。运行产物写到 `runs/cr/`（已被 gitignore）。要提交的只有 `docs/retrieval/` 下的清单、冻结文件、结果和检查点。

## 2. 数据与答案隔离

数据源固定为本机缓存：

```text
C:/Users/Administrator/.cache/huggingface/hub/datasets--princeton-nlp--SWE-bench_Verified/snapshots/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet
```

用 pyarrow 直接读，不走 `datasets.load_dataset`，不联网下载。把文件的 sha256 写进划分清单。

`dataset.py` 产出两种视图：

- `QueryView`：只有 `instance_id`、`repo`、`base_commit`、`problem_statement`。检索器和 `cr_eval.py` 的检索部分只能接收它。
- `LabelView`：由 `labels.py` 从 `patch` 计算，只给 `metrics.py` 用。

`hints_text`、`test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS` 本轮不使用。

必须有的隔离测试：

1. `QueryView` 的字段集合恰好是上面四个。
2. 除 `dataset.py`、`labels.py` 外，`src/retrieval/` 和 `scripts/cr_*.py` 的源码里不出现带引号的字段名 `"patch"`、`"test_patch"`、`"hints_text"`、`"FAIL_TO_PASS"`、`"PASS_TO_PASS"`（单双引号都查；不要用裸子串，`dispatch` 之类会误报）。用源码文本扫描实现，`metrics.py` 只接收 `LabelView` 对象，不接触原始字段名。
3. 检索函数的签名里没有 LabelView 参数。

## 3. 仓库缓存

- 对 12 个仓库做完整的 `git clone --bare` 到 `$CR_CACHE_DIR/repos/<owner>__<name>.git`。不要用 `--filter=blob:none`，按需取 blob 会很慢。
- 用 `git ls-tree -r <commit>` 列文件，`git cat-file --batch` 批量读 blob，不 checkout 工作区。
- 核对 499 个 base_commit 全部能解析。解析不了的写进检查点，不要自行换 commit。

## 4. 语料、切块与标签

**语料**：base_commit 下所有 `.py` 文件，排除测试文件。测试文件的判定写成一个函数并单测：路径的某一段是 `tests`、`test`、`testing`，或文件名匹配 `test_*.py` / `*_test.py` / `conftest.py`。仓库特有的例外不要加，有疑问写进检查点。

**切块**：

- 用 `ast` 解析。每个函数/方法一块，`symbol` 为带类名的限定名，如 `QuerySet.filter`。类体中不属于方法的部分一块，模块顶层代码一块。
- 嵌套函数并入外层函数，不单独成块。
- 解析失败（如 Python 2 语法）退回 60 行窗口、步长 40。统计并报告失败率。
- 块文本前加一行 `# path: <path>  symbol: <symbol>`，BM25 和 embedding 都用这份文本。
- 块记录：`repo, commit, path, symbol, kind, start_line, end_line, blob_sha, text`。

**缓存**：切块结果、token 计数、向量都以 `blob_sha`（加切块/模型版本号）为键，同一仓库不同 commit 间复用。

**标签**（`labels.py`）：

- 文件级：gold patch 里改动的、属于语料的文件。
- 新建文件、非 `.py` 文件、被判定为测试的文件不进入标签，分别计数。一道题没有任何语料内标签时，标为 `unretrievable`，在分母里单独报告，不静默丢弃。
- 函数级：取每个 hunk 在**旧文件**侧的改动行（删除行；纯新增时取插入点前一行），映射到 base 文件中包住该行的最内层函数或类块；都不在时记为模块块。

## 5. 检索器

**tokenizer**：按非字母数字切分；标识符再按 snake_case 与 camelCase 拆开，同时保留完整标识符；转小写；去掉长度 1 的 token。不用停用词表。单测覆盖 `QuerySet.filter`、`get_FOO_display`、`HTTPResponse`、异常栈行。

**BM25**：自己实现，只依赖 numpy。默认 `k1=1.2, b=0.75`。每道题在它自己的 base_commit 语料上建索引。

**embedding**：

- `torch`（CUDA 版）和 `sentence-transformers` 放进 `pyproject.toml` 的可选依赖组 `retrieval`，写确切版本。`numpy` 也写进这个组。不装进核心依赖。
- 先跑一个 GPU 自检：`torch.cuda.is_available()`、设备名、fp16 小矩阵乘。失败就写检查点停下。
- 候选最多三个本地模型，例如 `Qwen/Qwen3-Embedding-0.6B`、`jinaai/jina-embeddings-v2-base-code`、`nomic-ai/CodeRankEmbed`。先核对模型卡、许可证和 query/document 前缀要求；某个模型不存在或跑不动，就换掉并写明原因。
- 块和 query 都截到 512 token，报告被截断的比例。fp16，batch 大小自定。
- 相似度用归一化向量的点积，numpy 暴力计算，不引入向量数据库。

**融合**：RRF，`score = Σ w_i / (k + rank_i)`。

**文件级聚合**：块分数取文件内最大值。dev 上可以对比"整文件作为一个文档"的 BM25。

**参照**：

- 随机排序（固定种子）。
- 路径提及：issue 文本里出现的文件路径，或可以对应到文件的点分模块名，排在最前面，其余按 BM25 排序。这个参照衡量有多少题"文本里已经写了答案位置"。

**并列**：分数相同时按 `path` 字典序，保证结果确定。

## 6. 划分

- "保留题"取 `docs/sprint/clean-images/*.json` 的文件名与 `configs/sprint/main-targets.yaml` 的 id 的并集，再与 Verified 求交集。规划时核对过：72 个镜像里有 60 个在 Verified 中（另外 12 个是早期非 Verified 题）；main-targets 的 60 道都在 Verified 中；并集共 62 道。保留题全部放进 test，数目不是 62 就写进检查点。
- 剩下的题按 repo 分层，种子 `20261018`，抽 100 道作为 dev，其余进 test。
- 写 `docs/retrieval/cr1-split.json`：parquet 的 sha256、种子、dev/test 的 id 列表、每个仓库的计数，以及保留题的 id。

## 7. 指标

对每道可检索的题，每个检索器都算：

- 文件级：hit@1、hit@5、hit@10（至少一个 gold 文件进入前 k），recall@10（gold 文件进入前 10 的比例），MRR（第一个 gold 文件排名的倒数；前 100 都没有时记 0）。
- 函数级：hit@5、hit@10（块排名）。
- 汇总三个层面：全部题平均、按仓库平均、仓库宏平均（每个仓库等权）。

比较用按题的 paired bootstrap（10000 次，种子 `20261018`）报告差值和 95% 区间。另报一个按仓库重抽的 cluster bootstrap 作为稳健性参照。

## 8. 步骤与检查点

### CR-1a：数据、语料、BM25（只在 dev 上）

1. 写 `dataset.py`、`repo_cache.py`、`chunking.py`、`tokenize.py`、`labels.py`、`bm25.py`、`metrics.py` 和对应单测。单测用 `tmp_path` 里现建的小 git 仓库，不联网。
2. `cr_prepare.py`：clone、核对 commit、划分、语料统计。
3. `cr_index.py`：建切块和 token 缓存（全部 500 题；不建向量）。
4. `cr_eval.py`：在 dev 上跑随机、路径提及、BM25（块聚合）、BM25（整文件）。

写 `docs/retrieval/CHECKPOINT-CR-1a.md`，然后停下。内容：

- 提交号；全量 pytest 结果（照实写那个已知失败）。
- 仓库缓存体积、commit 解析结果、建缓存耗时。
- 语料统计：每题文件数和块数的分布、AST 失败率、blob 复用率。
- 标签统计：unretrievable 题数及原因，gold 文件数分布，函数级标签落到模块块的比例。
- dev 指标表（四个检索器）。
- 抽 3 道 dev 题，列出 issue 前 300 字、gold 文件、BM25 前 5 名。
- 遇到的问题和你做的取舍。

### CR-1b：embedding 与融合（只在 dev 上），然后冻结

1. GPU 自检。
2. 在 dev 上比较候选 embedding 模型：MRR、hit@5、编码耗时、显存峰值。
3. 网格最多 24 组：RRF `k ∈ {10, 30, 60}`、embedding 权重 `∈ {0.5, 1, 2}`（BM25 权重固定 1）、块级融合还是文件级融合、BM25 用块聚合还是整文件。按 dev 的文件级 MRR 选，持平时选更简单的那组。
4. 写 `docs/retrieval/CR-1-FREEZE.md`：三种冻结配置（BM25、embedding、hybrid）的全部参数，模型名与 revision，依赖确切版本，代码提交号。打 tag `cr1-freeze`。

写 `CHECKPOINT-CR-1b.md`，然后停下。内容：模型比较表、网格全表（不只写最优）、冻结配置、为 test 估计的耗时。

### CR-1c：test 只跑一次

审计通过后才开始。

1. 在 `cr1-freeze` 上为 test 建向量缓存，跑随机、路径提及和三种冻结配置。中途出错可以修基础设施问题后重跑，但不能改冻结参数；修改要写进检查点。
2. 写 `docs/retrieval/CR-1-RESULTS.zh-CN.md`，以及逐题结果 `docs/retrieval/cr1-test-per-instance.jsonl`（每题每检索器一行：id、repo、各指标、前 10 个文件）。

结果文件要有：

- 预先确定的主比较：hybrid 对 BM25 的文件级 hit@5 和 MRR，按题 paired bootstrap 差值与区间。
- 次要比较：embedding 对 BM25，hybrid 对 embedding；函数级指标；cluster bootstrap。
- 按仓库的表，以及仓库宏平均。django 占一半，必须单独看。
- 62 道保留题子集的表（之后 CR-2 从这里选题）。
- 路径提及参照命中的题有多少；在没有提及路径的题上，三种配置各自表现如何。
- 三个 hybrid 赢的例子和三个输的例子，选法写清楚（例如按 MRR 差值排序取两端）。
- 局限：gold 文件不等于全部需要读的文件；embedding 模型和 BM25 都可能在训练中见过这些仓库；只覆盖 Python 文件。

写 `CHECKPOINT-CR-1c.md`，然后停下。

## 9. 表述规则

- 区间跨 0 就写"未检出差异"，不写"略好"。
- 不用"显著提升""大幅超过"等词，直接给数值和区间。
- 不把 dev 上的数字当结果报告。
- 某个检索器在某个仓库上更差，照实写进表里。

## 10. 启动文字（发给 Cursor）

> 请阅读 `D:/crq/src/coding-harness/docs/retrieval/PLAN.zh-CN.md` 第 2 节，然后完整阅读同目录的 `CR-1-CONTRACT.zh-CN.md`，执行其中的 CR-1a。只做 CR-1a，写完 `docs/retrieval/CHECKPOINT-CR-1a.md` 并提交后停下，不要开始 CR-1b。不要修改合同第 0 节列出的禁改路径。遇到合同没覆盖、需要做取舍的情况，选保守的做法并写进检查点；会改变答案隔离、划分或主比较的问题，先停下来问。

## 11. CR-1a 审计后的补充（2026-10-26，适用于 CR-1b 和 CR-1c）

依据 [AUDIT-CR-1a](AUDIT-CR-1a.zh-CN.md)。只做细化，不改第 2、6、7 节。

### 11.1 冻结的 BM25

冻结的 BM25 配置取**块聚合**（`bm25_chunk`，`k1=1.2, b=0.75`），审计时已定，不再按 dev 结果改。整文件 BM25 仍可作为 hybrid 网格的一种成分。

### 11.2 embedding

- **缓存键** = sha256(实际送进模型的文本，含块头和前缀) + 模型名 + revision + 最大长度。不能只用 blob sha。
- 模型下载时记录 commit hash，加载时用 `revision=<hash>` 固定。需要 `trust_remote_code` 的模型必须固定 revision，并在检查点里写明。
- 2080 Ti 不支持 bf16，一律 fp16。每批检查向量里没有 NaN 和零范数，出现就停下。
- torch 写确切版本和 CUDA 变体，例如 `torch==X.Y.Z+cu12N`，写进 `retrieval` 组，安装命令写进检查点。
- **先做吞吐试跑**：每个候选模型取 5 道 dev 题，测编码速度和显存峰值，估算 dev 全量耗时。估算超过 3 小时，就把最大长度降到 256 或者放弃这个模型，写明原因。
- embedding 模型按它**单独检索**时的 dev 文件级 MRR 选，不按它进入 hybrid 后的分数选。

### 11.3 hybrid 选择规则与第四种冻结配置

- 网格按 dev 文件级 MRR 排序。与最好一组差距在 0.01 以内的配置中，按以下顺序选最简单的：`k=60` 优先，embedding 权重 1 优先，块级融合优先，BM25 块聚合优先。
- 新增第四种冻结配置 `hybrid_mention`：对冻结的 hybrid 排序做"提及优先"重排，与 CR-1a 的路径提及参照用同一个 `mention_first`，不引入新参数。
- CR-1c 的主比较不变（hybrid 对 BM25）。增加两项次要比较：`hybrid_mention` 对路径提及参照，`hybrid_mention` 对 hybrid。

### 11.4 隔离测试

在 `tests/test_retrieval_isolation.py` 中加一条：除 `dataset.py` 和 `labels.py` 外，`src/retrieval/`、`scripts/cr_*.py` 的源码不出现 `load_gold_diffs`。

### 11.5 测试基线说明

`test_dataset_oracle` 和 `test_runner` 的两个用例会调用 PATH 上的 `python`，是否通过取决于终端有没有激活 `.venv`。检查点里写明运行全量测试时 venv 是否激活。

### 11.6 CR-1b 启动文字

> CR-1a 审计通过，审计记录在 `D:/crq/src/coding-harness/docs/retrieval/AUDIT-CR-1a.zh-CN.md`。请先读这份审计，再读 `CR-1-CONTRACT.zh-CN.md` 第 5、8 节中 CR-1b 的部分和新增的第 11 节，然后执行 CR-1b。第 11 节与前文冲突时以第 11 节为准。只用 dev，不要对 test 运行任何检索或打分。写完 `docs/retrieval/CR-1-FREEZE.md`、打 tag `cr1-freeze`、写完 `docs/retrieval/CHECKPOINT-CR-1b.md` 并提交后停下，不要开始 CR-1c。吞吐试跑之后如果估算的总耗时超过 6 小时，先写一份简短的检查点停下来问。其他规则与 CR-1a 相同：不改禁改路径，不调用付费 API，取舍写进检查点，涉及答案隔离、划分或主比较的问题先停下来问。torch 和 sentence-transformers 只装进 `D:/crq/src/coding-harness/.venv`，不要修改任何 conda 环境。下载慢的话，可以只在 pip 命令上加 `--proxy http://127.0.0.1:7890`，不要改全局配置。


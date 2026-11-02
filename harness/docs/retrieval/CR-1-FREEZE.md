# CR-1 冻结配置

日期：2026-10-26。依据：[CR-1-CONTRACT.zh-CN.md](CR-1-CONTRACT.zh-CN.md) 第 5、8 节和第 11 节。机器可读版本：[cr1-freeze.json](cr1-freeze.json)（`scripts/cr_eval.py frozen` 直接读取它）。

所有选择只用了 dev（100 题）。test 没有跑过任何检索或打分。

## 代码与数据

| 项 | 值 |
|---|---|
| 代码提交 | `f1d1b929175504850bfb11995751da985dbb1192`（embed-select、grid、write-freeze 都在这个提交上运行，检索代码无未提交改动） |
| tag | `cr1-freeze`，指向在上述代码提交之后、只新增冻结文档的提交 |
| 数据 | SWE-bench Verified parquet，sha256 `a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd` |
| 划分 | [cr1-split.json](cr1-split.json)（dev 100 / test 400，其中保留题 62 道全在 test） |
| 语料 | base_commit 下的 `.py` 文件，去掉测试文件；切块缓存版本 `chunk1-tok1`，tokenizer 版本 `1` |
| 检索深度 | 每个检索器输出前 100 个文件和前 100 个块 |
| 文件聚合 | 文件分数 = 文件内块分数的最大值 |
| 并列 | 文件按 `path` 字典序；块按 `(path, start_line)` |

## 四种冻结配置

### 1. BM25

- 检索器 `bm25_chunk`：块作为文档，`k1=1.2, b=0.75`，Lucene 式 idf，query 词频不计；文件排名取块分数最大值。
- 合同 11.1 规定，不按 dev 结果再改。

### 2. embedding

| 项 | 值 |
|---|---|
| 模型 | `jinaai/jina-embeddings-v2-base-code` |
| revision | `516f4baf13dec4ddddda8631e019b5737c8bc250` |
| 远程代码 | `trust_remote_code=True`；建模代码在另一个仓库 `jinaai/jina-bert-v2-qk-post-norm`，固定在 `3baf9e3ac750e76e8edd3019170176884695fb94` |
| 许可证 | Apache-2.0 |
| 前缀 | query 和块都不加前缀（模型卡没有要求） |
| 最大长度 | 512 token（块和 query 都截断） |
| 精度 | fp16（加载后强制 `half()` 并检查所有参数都是 fp16） |
| 池化 | 模型自带的 sentence-transformers 配置：mean pooling |
| 相似度 | 归一化向量点积，numpy 暴力计算 |
| 向量缓存键 | sha256(实际送进模型的文本，含块头) + 模型名 + revision + 最大长度 |

加载方式（`src/retrieval/embed.py` 的 `Encoder`）：从固定 revision 的本地快照目录加载，按 `modules.json` 手工组装模块，并把 `code_revision` 同时传给 config、model、processor。加载后检查模型类所在模块路径里含有 `3baf9e3a…`，不符合就报错。原因见 CHECKPOINT-CR-1b 第 6 节。

选择依据（合同 11.2）：dev 上单独检索时的文件级 MRR 最高。jina-code 0.692，CodeRankEmbed 0.683，Qwen3-Embedding-0.6B 0.656。

### 3. hybrid

| 参数 | 值 |
|---|---|
| 名称 | `hybrid_k60_w2_filefuse_bm25chunk` |
| RRF | `score = Σ w_i / (k + rank_i)`，`k = 60` |
| 权重 | BM25 1，embedding 2 |
| 融合层级 | 文件级：文件排名 = RRF(BM25 块聚合的文件排名, embedding 块聚合的文件排名) |
| BM25 成分 | 块聚合（`bm25_chunk` 的文件排名） |
| 块排名 | 块级 RRF(BM25 块排名, embedding 块排名)，同样 `k=60`、embedding 权重 2 |
| embedding 成分 | 上面冻结的 jina-code |

选择依据（合同 11.3）：24 组网格按 dev 文件级 MRR 排序，最好一组 MRR 0.75173（`k=10, w=2`, 文件级, BM25 块聚合）。差距在 0.01 以内（MRR ≥ 0.74173）的只有两组；按"`k=60` 优先"选中 `k=60, w=2` 这一组（MRR 0.74190）。

### 4. hybrid_mention

对冻结的 hybrid 排序做"提及优先"重排：issue 里出现的文件路径或可对应到文件的点分模块名排到最前，其余保持 hybrid 顺序。与路径提及参照用同一个函数 `fusion.mention_first`，块排名同样把被提及文件的块排到最前。没有新参数。

## 依赖（`.venv`，Python 3.13.9）

`pyproject.toml` 的可选组 `retrieval`：

| 包 | 版本 |
|---|---|
| numpy | 2.5.3 |
| pyarrow | 25.0.1 |
| torch | 2.14.1+cu126 |
| sentence-transformers | 5.7.0 |
| transformers | 4.57.6 |
| tokenizers | 0.22.2 |
| safetensors | 0.8.0 |
| huggingface-hub | 0.36.2 |
| einops | 0.8.2 |

安装命令（只装进 `D:/crq/src/coding-harness/.venv`）：

```
.venv\Scripts\python.exe -m pip install "torch==2.14.1+cu126" --index-url https://download.pytorch.org/whl/cu126 --extra-index-url https://pypi.org/simple --proxy http://127.0.0.1:7890
.venv\Scripts\python.exe -m pip install "sentence-transformers==5.7.0" "transformers==4.57.6" einops==0.8.2 --proxy http://127.0.0.1:7890 --no-cache-dir
```

GPU：RTX 2080 Ti（sm_75），CUDA 12.6 构建。运行时设 `HF_HUB_OFFLINE=1`，所有权重和代码都从本地快照读取。

## CR-1c 怎么跑

```
set HF_HUB_OFFLINE=1
.venv\Scripts\python.exe scripts/cr_index.py --embed jina-code --split test --allow-test
.venv\Scripts\python.exe scripts/cr_eval.py frozen --split test --allow-test
```

两条命令在不带 `--allow-test` 时都拒绝 test。`frozen` 在检索代码与冻结提交不一致时会打印警告。

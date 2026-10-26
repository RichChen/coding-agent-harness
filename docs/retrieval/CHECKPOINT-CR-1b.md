# CHECKPOINT CR-1b：embedding 与融合（只在 dev 上），然后冻结

日期：2026-10-26。执行：Cursor。依据：[AUDIT-CR-1a](AUDIT-CR-1a.zh-CN.md)、[合同](CR-1-CONTRACT.zh-CN.md) 第 5、8 节的 CR-1b 部分和第 11 节。冻结配置见 [CR-1-FREEZE.md](CR-1-FREEZE.md)。

本轮没有调用付费 API，没有跑 agent，没有用 Docker。**test 没有跑任何检索或打分**；对 test 只做过一次语料文本计数（不加载模型、不检索、不打分），用来估算 CR-1c 耗时，见第 7 节。下面所有数字都来自 dev（100 题），只用于选择，不是结果。

## 1. 提交、tag 与测试

| 提交 | 内容 |
|---|---|
| `732f9e5` | `src/retrieval/dataset.py` 的 parquet 路径改为读 `HF_HUB_CACHE`（见第 6 节第 9 条：开工时就在工作区里，不是本轮写的，单独提交） |
| `aabe0bd` | `src/retrieval/embed.py`（向量缓存、模型加载）、`fusion.py` 的 RRF、`cr_index.py --embed/--count-texts`、`cr_eval.py` 的 embed-select / grid / write-freeze / frozen，`pyproject.toml` 依赖，单测 |
| `ba47ec9` | 向量缓存一次顺序读入内存（缓存在机械硬盘上，见第 6 节第 7 条） |
| `f1d1b92` | `cr_index.py --embed` 对 test 需要 `--allow-test`（CR-1c 用）。**冻结的代码提交** |
| `4faaa53` | `CR-1-FREEZE.md`、`cr1-freeze.json`、`cr1b-dev-summary.json`。**tag `cr1-freeze`** |

本检查点和 `frozen` 的 dev 结果在 `4faaa53` 之后单独提交。禁改路径（`src/runtime/`、`src/tools/`、`src/harness/`、`scripts/run_*.py`、`docs/sprint/`、A5/A6 文件）没有改动。

**全量 pytest（`4faaa53`）：771 passed、24 skipped、0 failed，85 秒。**

- 运行方式：`.venv\Scripts\python.exe -m pytest -q`，**没有激活 venv**：终端 PATH 上的 `python` 是 `D:\crq\base\Anaconda3\python.exe`，`VIRTUAL_ENV` 为空（合同 11.5）。合同提到的 `test_dataset_oracle` 和 `test_runner` 的两个用例这次也通过了。
- 771 = CR-1a 的 741 + 30 个新增 retrieval 用例；`pytest -k retrieval` 为 112 passed。24 个 skipped 与 CR-1a 相同（需要 Docker 或 Linux 进程组）。
- 新增测试：向量缓存往返、键随模型名 / revision / 最大长度变化、meta 不符报错、`preload` 与逐条读取结果一致、`encode_missing` 按长度降序且跳过已缓存；`rrf` 与 `ranks_from_order`；`fast_order` 与 `order_by_score` 在大量并列时结果相同；embedding、hybrid（24 组都输出完整排列；权重趋近 0 / 无穷时分别等于 BM25 / embedding 排名）、hybrid_mention；选择规则；冻结配置的组装。
- 隔离测试新增（合同 11.4）：除 `dataset.py`、`labels.py` 外，`src/retrieval/*.py` 与 `scripts/cr_*.py` 不出现 `load_gold_diffs`；扫描范围包含新文件 `embed.py`；`CR1B_RETRIEVERS`、`EmbeddingView`、`RankMaterial` 等以及 `embed.py` 全部函数的签名里没有 LabelView，三个新检索器的第一个参数都是 `QueryView`。

## 2. GPU 自检与依赖

- torch `2.14.1+cu126`，`torch.cuda.is_available()` 为 True，设备 RTX 2080 Ti，compute capability (7, 5)；fp16 矩阵乘与 fp32 结果的最大相对误差 2.8e-4，约 33.4 TFLOPS（`runs/cr/gpu-selfcheck.txt`）。torch 报告 bf16 可用，但按合同 11.2 一律用 fp16。
- 全部只装进 `D:/crq/src/coding-harness/.venv`（`include-system-site-packages = false`），没有动任何 conda 环境，代理只加在单条 pip 命令上。
- 最终版本（`pyproject.toml` 的 `retrieval` 组）：numpy 2.5.3、pyarrow 25.0.1、torch 2.14.1+cu126、sentence-transformers 5.7.0、transformers 4.57.6、tokenizers 0.22.2、safetensors 0.8.0、huggingface-hub 0.36.2、einops 0.8.2。
- 安装命令：

```
.venv\Scripts\python.exe -m pip install "torch==2.14.1+cu126" --index-url https://download.pytorch.org/whl/cu126 --extra-index-url https://pypi.org/simple --proxy http://127.0.0.1:7890
.venv\Scripts\python.exe -m pip install "sentence-transformers==5.7.0" "transformers==4.57.6" einops==0.8.2 --proxy http://127.0.0.1:7890 --no-cache-dir
```

最初装的是 sentence-transformers 6.2.0 + transformers 5.20.2，后来降到 4.x 系列，原因见第 6 节第 2 条。

## 3. 候选模型

| 键 | 模型 | revision | 许可证 | 远程代码 | query 前缀 | 块前缀 |
|---|---|---|---|---|---|---|
| `qwen3-0.6b` | `Qwen/Qwen3-Embedding-0.6B` | `97b0c614…` | Apache-2.0 | 否 | `Instruct: Given a GitHub issue, retrieve the source code that must be modified to resolve it\nQuery:` | 无 |
| `jina-code` | `jinaai/jina-embeddings-v2-base-code` | `516f4baf…` | Apache-2.0 | 是，建模代码在 `jinaai/jina-bert-v2-qk-post-norm@3baf9e3a…` | 无 | 无 |
| `coderank` | `nomic-ai/CodeRankEmbed` | `3c4b6080…` | MIT | 是，代码在同一仓库 | `Represent this query for searching relevant code: ` | 无 |

- 前缀按模型卡：Qwen3 要求 query 带 `Instruct: …\nQuery:`，文档不加；CodeRankEmbed 要求 query 带固定前缀，代码不加；jina-v2-code 不要求前缀。Qwen 的指令句是开跑前按模型卡格式写的，没有在 dev 上调。
- 完整 revision 写在 `src/retrieval/embed.py` 的 `MODELS` 和 `cr1-freeze.json`。
- 所有权重和代码文件都下载到 HF 缓存的对应 revision 快照目录；运行时设 `HF_HUB_OFFLINE=1`。

## 4. 吞吐试跑与 dev 模型比较

### 4.1 吞吐试跑（每个模型 5 道 dev 题，种子 20261018 抽样，最终依赖版本）

5 道题去重后 55,672 个块文本，最大长度 512，batch 32，按 token 长度降序分组编码。

| 模型 | 编码耗时 | 吞吐 (token/s) | 显存峰值 | 估算的 dev 剩余耗时 | 实际 dev 剩余耗时 |
|---|---|---|---|---|---|
| qwen3-0.6b | 416 秒 | 19,554 | 3,393 MiB | 993 秒（90,693 条 / 1,942 万 token） | 992 秒 |
| jina-code | 135 秒 | 64,769 | 1,051 MiB | 316 秒 | 366 秒 |
| coderank | 143 秒 | 68,381 | 856 MiB | 323 秒 | 442 秒 |

三个模型的估算都远低于合同 11.2 的 3 小时，没有降最大长度，也没有放弃模型。CR-1b 总估算约 1–1.5 小时，低于 6 小时，所以没有中途停下。（第一次试跑在旧依赖版本上做过 Qwen：402 秒、20,247 token/s、显存峰值 1,617 MiB；那批向量已经弃用，见第 6 节第 2 条。）

### 4.2 dev 比较（每个模型单独检索，100 题）

dev 去重后 146,365 个块文本，加 100 条 query。

| 模型 | MRR | hit@1 | hit@5 | hit@10 | recall@10 | fn_hit@5 | fn_hit@10 | 仓库宏平均 MRR | 对 BM25 的 MRR 差 [95%] | 编码总耗时 | 显存峰值 | 块截断 | query 截断 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3-0.6b | 0.656 | 0.51 | 0.82 | 0.94 | 0.913 | 0.57 | 0.66 | 0.701 | +0.087 [-0.004, +0.178] | 1,408 秒 | 3,393 MiB | 11.8% | 38/100 |
| **jina-code** | **0.692** | 0.58 | 0.82 | 0.93 | 0.899 | 0.53 | 0.61 | 0.643 | +0.123 [+0.027, +0.216] | 501 秒 | 1,051 MiB | 12.8% | 40/100 |
| coderank | 0.683 | 0.56 | 0.83 | 0.89 | 0.873 | 0.53 | 0.62 | 0.707 | +0.114 [+0.018, +0.209] | 584 秒 | 856 MiB | 14.9% | 43/100 |
| bm25_chunk（参照） | 0.570 | 0.46 | 0.67 | 0.80 | 0.769 | 0.47 | 0.59 | 0.484 | — | — | — | — | — |

- 编码总耗时 = 试跑 + 剩余部分，不含模型加载（10–100 秒）和收集文本（60–90 秒）。块截断 = 超过 512 token 的块文本比例；query 截断 = 超过 512 token 的 issue 数。
- **按合同 11.2 选 jina-code**（全部题平均的文件级 MRR 最高）。它比 coderank 只高 0.009，没有做模型之间的配对比较，这个差距在 100 题上说明不了什么。
- **需要审计注意**：选择用的是"全部题平均"这一层的 MRR（合同第 7 节三个层面的第一个，也是所有汇总表的默认口径）。如果改用仓库宏平均，顺序会变成 coderank 0.707 > qwen 0.701 > jina 0.643。合同没有写明用哪一层，我取了默认的全部题平均，没有因为这个停下。
- bm25_chunk 的数字与 CR-1a 完全一致（MRR 0.570），说明新的检索路径没有改变 BM25。

## 5. hybrid 网格（jina-code，dev，24 组）

固定：BM25 权重 1。块级融合 = 块排名做 RRF，文件分数取融合后块分数的最大值。文件级融合 = 文件排名 RRF(BM25 文件排名, embedding 文件排名)，块排名仍用同样 k、w 的块级 RRF。"块聚合"BM25 = `bm25_chunk` 的文件排名；"整文件"BM25 = 整文件作为文档的 BM25。

| # | k | w_emb | 融合 | BM25 成分 | MRR | hit@1 | hit@5 | hit@10 | recall@10 | fn_hit@5 | fn_hit@10 | 仓库宏平均 MRR | 距最好 ≤0.01 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 10 | 2 | 文件级 | 块聚合 | 0.7517 | 0.64 | 0.91 | 0.94 | 0.911 | 0.60 | 0.70 | 0.742 | 是 |
| 2 | 60 | 2 | 文件级 | 块聚合 | **0.7419（选中）** | 0.63 | 0.90 | 0.94 | 0.916 | 0.63 | 0.74 | 0.766 | 是 |
| 3 | 30 | 2 | 文件级 | 块聚合 | 0.7406 | 0.62 | 0.91 | 0.93 | 0.901 | 0.64 | 0.72 | 0.726 |  |
| 4 | 10 | 1 | 文件级 | 块聚合 | 0.7294 | 0.60 | 0.91 | 0.95 | 0.921 | 0.65 | 0.71 | 0.717 |  |
| 5 | 30 | 1 | 文件级 | 块聚合 | 0.7253 | 0.60 | 0.90 | 0.94 | 0.911 | 0.64 | 0.75 | 0.741 |  |
| 6 | 60 | 1 | 文件级 | 块聚合 | 0.7215 | 0.60 | 0.89 | 0.94 | 0.911 | 0.64 | 0.74 | 0.738 |  |
| 7 | 30 | 1 | 文件级 | 整文件 | 0.7201 | 0.61 | 0.87 | 0.96 | 0.929 | 0.64 | 0.75 | 0.711 |  |
| 8 | 60 | 2 | 块级 | 块聚合 | 0.7187 | 0.61 | 0.88 | 0.96 | 0.934 | 0.63 | 0.74 | 0.720 |  |
| 9 | 30 | 2 | 块级 | 块聚合 | 0.7154 | 0.61 | 0.87 | 0.94 | 0.914 | 0.64 | 0.72 | 0.711 |  |
| 10 | 60 | 1 | 块级 | 块聚合 | 0.7152 | 0.61 | 0.86 | 0.97 | 0.944 | 0.64 | 0.74 | 0.760 |  |
| 11 | 60 | 0.5 | 文件级 | 块聚合 | 0.7131 | 0.61 | 0.87 | 0.91 | 0.881 | 0.63 | 0.69 | 0.719 |  |
| 12 | 60 | 1 | 文件级 | 整文件 | 0.7131 | 0.60 | 0.86 | 0.96 | 0.924 | 0.64 | 0.74 | 0.703 |  |
| 13 | 10 | 2 | 块级 | 块聚合 | 0.7111 | 0.61 | 0.85 | 0.94 | 0.916 | 0.60 | 0.70 | 0.686 |  |
| 14 | 30 | 1 | 块级 | 块聚合 | 0.7097 | 0.60 | 0.87 | 0.94 | 0.909 | 0.64 | 0.75 | 0.756 |  |
| 15 | 30 | 2 | 文件级 | 整文件 | 0.7074 | 0.58 | 0.90 | 0.97 | 0.946 | 0.64 | 0.72 | 0.693 |  |
| 16 | 30 | 0.5 | 文件级 | 块聚合 | 0.7045 | 0.60 | 0.86 | 0.91 | 0.881 | 0.61 | 0.68 | 0.711 |  |
| 17 | 60 | 2 | 文件级 | 整文件 | 0.7031 | 0.58 | 0.90 | 0.98 | 0.954 | 0.63 | 0.74 | 0.689 |  |
| 18 | 10 | 1 | 块级 | 块聚合 | 0.6993 | 0.59 | 0.82 | 0.91 | 0.879 | 0.65 | 0.71 | 0.674 |  |
| 19 | 60 | 0.5 | 文件级 | 整文件 | 0.6966 | 0.58 | 0.83 | 0.93 | 0.901 | 0.63 | 0.69 | 0.720 |  |
| 20 | 30 | 0.5 | 文件级 | 整文件 | 0.6960 | 0.58 | 0.83 | 0.92 | 0.891 | 0.61 | 0.68 | 0.720 |  |
| 21 | 10 | 0.5 | 文件级 | 块聚合 | 0.6937 | 0.59 | 0.86 | 0.90 | 0.869 | 0.59 | 0.66 | 0.683 |  |
| 22 | 60 | 0.5 | 块级 | 块聚合 | 0.6732 | 0.55 | 0.85 | 0.90 | 0.869 | 0.63 | 0.69 | 0.685 |  |
| 23 | 30 | 0.5 | 块级 | 块聚合 | 0.6640 | 0.55 | 0.81 | 0.87 | 0.839 | 0.61 | 0.68 | 0.661 |  |
| 24 | 10 | 0.5 | 块级 | 块聚合 | 0.6451 | 0.53 | 0.79 | 0.88 | 0.849 | 0.59 | 0.66 | 0.619 |  |

选择（合同 11.3）：最好一组 MRR 0.75173，门槛 0.74173。门槛内只有 #1 和 #2，按"`k=60` 优先"选 #2（`k=60, w=2`, 文件级, 块聚合），它在门槛内只多出 0.00017。规则是机械执行的，但离边界很近：如果 #2 的 MRR 低 0.0002，就会选 #1（`k=10`）。

## 6. 遇到的问题和取舍

1. **HF 下载很慢**。直连 huggingface.co 不通；走本机代理约 40–600 KB/s，xet 下载产生 0 字节的 `.incomplete` 文件；hf-mirror.com 直连 TLS 失败。处理：小文件用 `HF_HUB_DISABLE_XET=1` 走代理下载；Qwen 和 jina 的 `model.safetensors`、Qwen 的 `tokenizer.json` 从 ModelScope 直连下载（约 2–3 MB/s），CodeRankEmbed（ModelScope 上没有）走代理分 16 段并行下载。**每个大文件都核对了 HF 上对应 revision 的 LFS sha256 和大小**，一致后才放进 HF 缓存的 `snapshots/<revision>/` 目录。三个权重：Qwen 1,136 MiB（382 秒）、jina 307 MiB（184 秒）、CodeRankEmbed 522 MiB（864 秒）。下载脚本在 `runs/cr/fetch_weights.py`（不入库）。
2. **jina 和 CodeRankEmbed 的远程代码不能在 transformers 5.x 上运行**。jina 的 `modeling_bert.py` 导入 `transformers.pytorch_utils.find_pruneable_heads_and_indices`（5.x 已删除）；CodeRankEmbed 的前向调用 `get_extended_attention_mask`（5.x 已删除）。sentence-transformers 6.x 要求 transformers ≥ 5。我选的做法：把整套依赖降到 sentence-transformers 5.7.0 + transformers 4.57.6（pip 自动配 huggingface-hub 0.36.2、tokenizers 0.22.2），三个模型在同一套环境里跑。理由：保留合同列出的三个候选，不改实验设计；这两份远程代码就是为 4.x 写的。没有选的做法：给 transformers 打补丁恢复被删的函数（改库行为，正确性没法保证）；换掉两个候选模型（要另选模型、再下载，等于改候选集）。**Qwen 的 dev 向量在新环境里全部重算**；旧环境的向量另存为 `…L512.tf5-old.sqlite`，146,465 条共同键上新旧向量的余弦最小 0.99997、中位 0.999998，两套环境对 Qwen 几乎没有影响。
3. **固定远程代码的 revision 比预期麻烦**。(a) sentence-transformers 会把 `model_kwargs["code_revision"]` 拿去给它自己的模块类用，不传给 `AutoModel`，所以 jina 的建模代码（在另一个仓库）会解析到那个仓库的 `main`：离线时报错，联网时会悄悄用上未固定的代码。处理：对有 `code_revision` 的模型，按 `modules.json` 手工组装模块，把 `code_revision` 同时传给 config、model、processor。(b) CodeRankEmbed 的远程代码自己下载权重，调用时不带 revision。处理：需要远程代码的模型一律从固定 revision 的本地快照目录加载。(c) 加载后检查模型类所在模块路径含有固定的 commit hash，不含就报错，jina 和 CodeRankEmbed 都通过了这个检查。(d) CodeRankEmbed 的加载代码忽略 `torch_dtype`，权重是 fp32；加载后统一 `half()`，并检查所有参数都是 fp16。
4. **新增依赖 einops 0.8.2**：CodeRankEmbed 的远程代码需要。
5. **网格只有 24 组**：四个维度名义上 3×3×2×2 = 36 组，但"块级融合 + 整文件 BM25"没有定义（整文件 BM25 没有块分数），有效组合是 27 组。为了满足"最多 24 组"，去掉了"整文件 BM25 + k=10"的 3 组。这是开跑前定的，写在 `cr_eval.py` 的 `GRID` 里。
6. **选择规则的细节**：门槛内按 (k≠60, w≠1, 非块级融合, 非块聚合 BM25) 依次比较，再按 MRR 高者、名称。合同没说 k=10 和 k=30 谁更"简单"、w=0.5 和 w=2 谁更简单，这次门槛内只有两组，k 已经分出先后，这个空白没有起作用。
7. **向量缓存在机械硬盘上**。D: 是 WD 机械盘，按主键逐条随机读向量非常慢：第一次跑 embed-select，几分钟里两个进程 CPU 时间只有几秒，还没跑完第一个仓库就停掉了。处理：`VectorStore.preload()` 一次顺序扫描整表读进内存（dev 三个模型约 750 MB），之后从内存取。修好后 embed-select 3.5 分钟，grid 1.3 分钟。
8. **NaN 和零范数检查的粒度**：每次 `encode` 调用（2,048 条文本，按 32 条一批跑完）检查一次，出现就抛错停下，结果不写入缓存。合同写"每批检查"，我的粒度粗一些，但任何一条出问题都会让整组不入库、整个任务停止。三个模型全程没有触发。
9. **`dataset.py` 的工作区改动**：开工时 `src/retrieval/dataset.py` 已有未提交改动（parquet 路径改为读 `HF_HUB_CACHE`，默认值不变），不是本轮写的。本机没设 `HF_HUB_CACHE`，解析出的路径和 parquet sha256（`a45b1fe4…`，与 `cr1-split.json` 一致）都没变。冻结要求检索代码没有未提交改动，所以我把它单独提交为 `732f9e5`，提交说明写了来源，需要的话可以单独回退。
10. **冻结前又改了一次代码**：先在 `ba47ec9` 上跑了 embed-select 和 grid；写冻结文档时发现 CR-1c 需要 `cr_index.py --embed --split test`，原来的代码只能拒绝。为了不在冻结后改代码，加了 `--allow-test` 开关（`f1d1b92`），并在这个提交上重跑 embed-select、grid、write-freeze，结果逐位相同。
11. **query 截断比例高**：38–43% 的 issue 超过 512 token，被截掉的是尾部（多为报错栈或复现代码）。合同固定 512，没有改。
12. **为 CR-1c 估算对 test 做了文本计数**：`cr_index.py --count-texts --split test --minus-split dev` 只读切块缓存、统计去重后的块文本数和字符数，不加载模型、不检索、不打分。我认为这不算"对 test 运行检索或打分"，但它确实读了 test 题的语料，写在这里供审计判断。
13. **旧向量文件**：`D:/crq/cache/cr/vectors/` 下保留了旧环境的 Qwen 向量（585 MiB），只用于第 2 条的对照，可以删除。现有缓存：Qwen 585 MiB、jina 298 MiB、CodeRankEmbed 298 MiB（各 146,465 条）。

## 7. 冻结配置在 dev 上（`scripts/cr_eval.py frozen --split dev`）

| 检索器 | MRR | hit@1 | hit@5 | hit@10 | recall@10 | fn_hit@5 | fn_hit@10 | 仓库宏平均 MRR | 仓库宏平均 hit@5 |
|---|---|---|---|---|---|---|---|---|---|
| random | 0.019 | 0.00 | 0.02 | 0.06 | 0.055 | 0.00 | 0.00 | 0.053 | 0.064 |
| path_mention | 0.623 | 0.49 | 0.76 | 0.86 | 0.834 | 0.47 | 0.60 | 0.648 | 0.821 |
| bm25 | 0.570 | 0.46 | 0.67 | 0.80 | 0.769 | 0.47 | 0.59 | 0.484 | 0.674 |
| embedding | 0.692 | 0.58 | 0.82 | 0.93 | 0.899 | 0.53 | 0.61 | 0.643 | 0.738 |
| hybrid | 0.742 | 0.63 | 0.90 | 0.94 | 0.916 | 0.63 | 0.74 | 0.766 | 0.853 |
| hybrid_mention | 0.730 | 0.61 | 0.92 | 0.95 | 0.926 | 0.59 | 0.72 | 0.806 | 0.917 |

dev 上的配对 bootstrap（10000 次，种子 20261018）。hybrid 和 embedding 都是在这 100 题上选出来的，这些差值偏乐观，不能当结果：

| 比较 | hit@5 差 [95%] | MRR 差 [95%] |
|---|---|---|
| hybrid − bm25 | +0.230 [+0.150, +0.320] | +0.172 [+0.100, +0.244] |
| embedding − bm25 | +0.150 [+0.040, +0.260] | +0.123 [+0.027, +0.216] |
| hybrid − embedding | +0.080 [+0.010, +0.150] | +0.050 [−0.009, +0.108]（未检出差异） |
| hybrid_mention − path_mention | +0.160 [+0.090, +0.230] | +0.107 [+0.051, +0.165] |
| hybrid_mention − hybrid | +0.020 [+0.000, +0.050] | −0.011 [−0.067, +0.044]（未检出差异） |

路径提及：100 题里 58 题的 issue 提到了语料中的文件，其中 39 题提到的文件包含 gold 文件。

按仓库的 MRR（dev；除 django、sympy 外每个仓库只有 1–8 题）：

| 仓库 | n | random | path_mention | bm25 | embedding | hybrid | hybrid_mention |
|---|---|---|---|---|---|---|---|
| astropy | 5 | 0.025 | 0.710 | 0.710 | 0.800 | 0.867 | 0.850 |
| django | 50 | 0.004 | 0.673 | 0.647 | 0.688 | 0.737 | 0.742 |
| matplotlib | 8 | 0.012 | 0.588 | 0.469 | 0.729 | 0.737 | 0.595 |
| seaborn | 1 | 0.014 | 1.000 | 0.333 | 0.167 | 1.000 | 1.000 |
| requests | 2 | 0.069 | 0.750 | 0.158 | 0.279 | 0.538 | 1.000 |
| xarray | 5 | 0.053 | 0.469 | 0.163 | 0.640 | 0.532 | 0.550 |
| pylint | 2 | 0.258 | 0.292 | 0.225 | 0.550 | 0.667 | 1.000 |
| pytest | 2 | 0.107 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| scikit-learn | 4 | 0.021 | 0.875 | 0.792 | 0.833 | 1.000 | 0.875 |
| sphinx | 7 | 0.016 | 0.194 | 0.194 | 0.684 | 0.545 | 0.543 |
| sympy | 14 | 0.008 | 0.579 | 0.634 | 0.703 | 0.802 | 0.707 |

完整汇总（含按仓库的全部指标）在 [cr1b-dev-summary.json](cr1b-dev-summary.json) 的 `frozen_dev` 一节。`frozen` 在 dev 上每题中位 0.70 秒（含现建两个 BM25 索引和取向量），100 题共 79 秒。

## 8. 为 test 估计的耗时（CR-1c）

- 切块缓存：CR-1a 已经为全部 500 题建好，不用重建。
- 向量：test 400 题去重后 195,985 个块文本，其中 55,139 个不在 dev 里（约 9,600 万字符），只需要为冻结的 jina-code 编码这部分。按 dev 上 jina 每条约 200 token、约 56,000 token/s 估算，约 1,100 万 token，编码约 200–250 秒；加上收集文本（400 题约 4–6 分钟）和模型加载，约 10 分钟。
- 检索：dev 上每题约 0.7 秒，test 400 题约 5 分钟；预读向量（约 20 万条，约 300 MB）不到 1 分钟。
- 标签与打分：1 分钟以内。
- **合计约 20–30 分钟**，按 2 倍余量计不超过 1 小时。

## 9. 运行记录

| 步骤 | 耗时 |
|---|---|
| 权重下载与校验（三个模型，含 jina 远程代码仓库） | 约 25 分钟（串行） |
| 依赖降级（pip，代理） | 约 4 分钟 |
| 吞吐试跑（三个模型各 5 题，最终环境） | 约 27 分钟（含模型加载、收集文本和 dev 剩余量的 token 统计） |
| dev 全量编码（三个模型） | 约 48 分钟墙钟（编码本身 992 + 366 + 442 秒） |
| `cr_index.py --count-texts --split test --minus-split dev` | 345 秒（与 Qwen 编码同时跑） |
| `cr_eval.py embed-select`（含预读三个模型的向量） | 约 3.5 分钟 |
| `cr_eval.py grid` | 约 1.3 分钟 |
| `cr_eval.py frozen --split dev` | 79 秒 |

产物：`docs/retrieval/cr1-freeze.json`、`CR-1-FREEZE.md`、`cr1b-dev-summary.json`（embed_select、grid、frozen_dev 三节）。逐题结果与排名在 `runs/cr/`（不入库）：`dev-embed-select-*`、`dev-grid-*`、`dev-frozen-*`、`embed-*-L512-dev*.json`、`count-texts-test.json`、`qwen-stack-compare.json`。

下一步是 CR-1c，等审计通过后再开始。

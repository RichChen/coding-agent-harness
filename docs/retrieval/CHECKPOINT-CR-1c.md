# CHECKPOINT CR-1c：test 只跑一次

日期：2026-10-26。执行：Cursor。依据：[AUDIT-CR-1b](AUDIT-CR-1b.zh-CN.md)、[合同](CR-1-CONTRACT.zh-CN.md) 第 8 节 CR-1c 部分、第 9 节、第 11.3 节和第 12 节。结果见 [CR-1-RESULTS.zh-CN.md](CR-1-RESULTS.zh-CN.md)。

本轮没有调用付费 API，没有跑 agent，没有用 Docker。冻结配置在 test 上只跑了一次，没有重跑；冻结后没有修改 `src/retrieval/`、`scripts/cr_index.py`、`scripts/cr_eval.py`。

## 1. 提交与测试

| 提交 | 内容 |
|---|---|
| 本提交 | `scripts/cr_report.py`、`CR-1-RESULTS.zh-CN.md`、`cr1-test-per-instance.jsonl`、`cr1-test-summary.json`、本检查点 |

运行时 HEAD 为 `1273804`（CR-1b 检查点）。它与 tag `cr1-freeze`（`4faaa53`）之间只差两个 docs 文件，检索代码与冻结代码提交 `f1d1b92` 相同（见第 2 节）。禁改路径（`src/runtime/`、`src/tools/`、`src/harness/`、`scripts/run_*.py`、`docs/sprint/`、A5/A6 文件）没有改动。

**全量 pytest：773 passed、24 skipped、0 failed，83 秒。**

- 运行方式：`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`，**没有激活 venv**：`VIRTUAL_ENV` 为空，PATH 上的 `python` 是 `D:\crq\base\Anaconda3\python.exe`（合同 11.5）。
- 773 = CR-1b 的 771 + 2。多出的 2 个是隔离测试对新文件 `scripts/cr_report.py` 自动生成的参数化用例：`test_no_quoted_gold_field_names[cr_report.py]` 和 `test_no_gold_diff_loader_outside_allowed[cr_report.py]`，都通过。本轮没有新增测试文件。24 个 skipped 与前两轮相同。

## 2. 开跑前检查（合同 12.1）

| 项 | 检查 | 结果 |
|---|---|---|
| parquet | `HF_HUB_CACHE` 是否设置；`dataset.parquet_sha256()` | 未设置；sha256 = `a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd`，与合同一致 |
| 冻结代码 | `git diff --stat f1d1b92 -- src/retrieval scripts/cr_index.py scripts/cr_eval.py`；`git status` | diff 为空；这三个路径没有未提交改动。工作区里只有 `docs/` 下的改动（合同第 12 节、AUDIT-CR-1b，不是我写的，见第 6 节第 7 条） |
| 冻结代码（运行时） | `cr_eval.py frozen` 的日志里查 `WARNING: retrieval code differs` | 没有出现 |
| 离线 | `HF_HUB_OFFLINE` | 每条命令前都设了 `$env:HF_HUB_OFFLINE='1'` |

三项都满足，开跑。跑完后又核对一次：`git diff --stat f1d1b92 -- src/retrieval scripts/cr_index.py scripts/cr_eval.py` 仍为空。

## 3. 运行记录

每条命令只跑了一次，没有因为基础设施问题重跑。

| 步骤 | 命令 | 墙钟 | 说明 |
|---|---|---|---|
| test 向量 | `cr_index.py --embed jina-code --split test --allow-test` | 1064 秒 | 收集文本 109 秒，加载模型 76 秒，编码 261 秒；其余时间未细分，大部分应在机械硬盘上的向量库读写 |
| 冻结配置 | `cr_eval.py frozen --split test --allow-test` | 346 秒 | 向量读入内存 8 秒（202,004 条），检索 + 打分 330 秒 |
| 统计 | `cr_report.py` | 约 7 秒 | 不检索；跑了两次，第二次是修了渲染问题之后，见第 5 节 |

CR-1b 检查点估算的 CR-1c 耗时是 20–30 分钟，实际约 24 分钟。

test 向量（`runs/cr/embed-jina-code-L512-test.json`）：

- 400 题去重后 195,985 个块文本，其中 140,846 个在 dev 时已经编码过（同一仓库同一 blob 的块文本相同，按缓存键复用），新编码 55,139 个，共 14,385,312 token；400 个 issue 另编码。
- 截断：块文本 30,262 / 195,985（15.4%）超过 512 token；issue 118 / 400（29.5%）超过 512 token。
- 显存峰值 1,051 MiB；吞吐约 55,800 token/秒。
- 日志里有 `optimum is not installed` 和 `torch_dtype is deprecated` 两条警告，dev 上也有，不影响结果。没有出现 NaN 或零范数报错。
- 模型与 revision 与 `cr1-freeze.json` 一致：`jinaai/jina-embeddings-v2-base-code` @`516f4baf13dec4ddddda8631e019b5737c8bc250`，最大长度 512，batch 32，fp16。

冻结运行的输出（`runs/cr/`，不入库）：`test-frozen-rankings.jsonl`（23 MB）、`test-frozen-per-instance.jsonl`、`test-frozen-summary.json`、`frozen-test.log`、`embed-jina-code-test.log`、`report.log`、`cr1-test-tables.md`。

## 4. 结果摘要

详见 [CR-1-RESULTS](CR-1-RESULTS.zh-CN.md)。test 400 题，没有 unretrievable 题。

- 主比较，hybrid − BM25：hit@5 +0.182 [+0.140, +0.225]（cluster [+0.138, +0.298]）；MRR +0.165 [+0.129, +0.199]（cluster [+0.128, +0.251]）。
- hybrid − embedding：文件级 hit@5 和 MRR 都未检出差异；函数级 fn_hit@5 +0.075 [+0.037, +0.115]。
- hybrid_mention − hybrid：hit@5 −0.025 [−0.045, −0.005]；MRR 未检出差异。
- 按仓库宏平均 MRR，hybrid（0.605）低于 embedding（0.614）；按仓库看，hybrid 在 astropy、matplotlib、pylint、flask 上的 MRR 低于 embedding。

## 5. `scripts/cr_report.py`

在跑 test 之前写好，包括例子的挑选规则（按 hybrid MRR − BM25 MRR 排序取两端各 3 题，并列按 instance_id）。看到输出之后改过两处，都只影响制表，不影响统计和挑选：

1. 渲染时把次要比较和函数级比较合进了同一个字典，键相同的两组（embedding−bm25、hybrid−embedding）的文件级行被函数级行覆盖，表里缺了这 4 行。`cr1-test-summary.json` 里的数据本来就是对的；改为分两节渲染。
2. 只有一个仓库的子集（django）上，按仓库的 cluster bootstrap 只能重抽同一个簇，区间退化成一个点。改为在子集只有一个仓库时记 `null`，表里写"单仓库，不适用"。

做什么：检查运行覆盖的 id 恰好等于 `cr1-split.json` 的 test id；写逐题 jsonl；算全部题 / 按仓库 / 宏平均表、主比较、次要比较（含合同 11.3 的两项）、函数级比较、子集（django、非 django、62 道保留题、有提及、无提及）、路径提及计数、issue 长度分组、例子。bootstrap 直接调用冻结的 `metrics.paired_bootstrap` 和 `metrics.cluster_bootstrap`（10000 次，种子 `20261018`）。

## 6. 遇到的问题和取舍

1. **`cr_report.py` 读的文件超出合同 12.2 列出的三个。** 12.2 说它只读 `test-frozen-per-instance.jsonl`、排序文件和 `cr1-split.json`。另外读了四样，都只读、不检索、不改变任何排序或指标：
   - `cr1-freeze.json`：取冻结的 embedding 模型名和最大长度，用来定位向量库；
   - 冻结模型的向量库里每个 issue 的 `n_tokens`（编码时记下的截断前 token 数）：12.3 要求按 issue 是否超过 512 token 分组，这个信息不在那三个文件里；
   - `QueryView.problem_statement`：算 issue 文本的缓存键以查 `n_tokens`，以及在例子里显示 issue 前 200 字；
   - `runs/cr/labels.jsonl` 的 `gold_files`：只用于在例子里列出 gold 文件。这些标签在 `cr_eval.py frozen` 打分时已经用过，且只在排序落盘之后读取。

   我的理解是 12.2 的目的是"不重新检索"，12.3 的截断分组又必须用到 token 数，所以选了上面这个做法，没有停下来问。没有选的做法：用 BM25 分词器或字符数近似 512 token（与 embedding 实际截断不一致）；在例子里不列 gold 文件（例子没法读）。隔离测试照常扫描 `cr_report.py`，通过。
2. **逐题 jsonl 的字段。** 合同要求 id、repo、各指标、前 10 个文件。另加了 `retriever`、`holdout`（是否保留题，CR-2 选题用）和 `unretrievable`。按 (instance_id, 检索器顺序) 排序，LF 换行，2400 行，1.47 MB，入库。
3. **多提交了 `cr1-test-summary.json`。** 合同没要求，但结果文件里的全部数字都来自它，入库便于审计核对。
4. **单仓库子集的 cluster bootstrap 记为不适用**，见第 5 节第 2 条。
5. **issue 长度的口径。** 用冻结 jina tokenizer 的 token 数（含特殊 token），`> 512` 算截断，与编码日志的 `query_truncated = 118` 一致。CR-1b 记录的 dev 比例（38–43%）是三个候选模型各自 tokenizer 的数字，口径不同，结果文件里并列写出，没有比较。
6. **结果文件里没有区间的地方。** 按仓库的数字、宏平均、保留题上 hybrid 对 embedding 都只给点值。合同只要求主比较、次要比较和 cluster bootstrap 给区间；test 结果出来后再加比较项有挑结果之嫌（12.2 不许改主比较），所以没有加。
7. **合同第 12 节和 AUDIT-CR-1b 没有由我提交。** 开工时它们是工作区里未提交的 docs 改动，不是我写的。前两轮的审计和合同修改都是你单独提交的（例如 `6af8b03`），这次也留给你提交，本提交只包含我写的文件。检查点和结果文件里引用了这两个文件。
8. **运行时没有 checkout tag。** 直接在 HEAD `1273804` 上运行。`git diff 4faaa53 1273804` 只有 `CHECKPOINT-CR-1b.md` 和 `cr1b-dev-summary.json` 两个 docs 文件，检索代码与 `f1d1b92` 相同（12.1 的 diff 检查和运行时的 WARNING 检查都通过）。
9. **"路径提及参照命中"的定义。** 用 `cr_eval.py` 已有的 `mention_has_gold`：issue 中提到的、在语料中存在的文件里至少有一个是 gold 文件。400 题中 213 题有提及，122 题命中。
10. **结果文件局限一节里 BM25 的"训练接触"。** 合同要求写"embedding 模型和 BM25 都可能见过这些仓库"。BM25 没有训练参数，我照实写了 BM25 没有在这些数据上训练，只把"设计时了解这些公开仓库"作为它这一侧的局限。

## 7. 留给审计

- AUDIT-CR-1b 提到审计方会在 test 上独立重算指标。可以从 `cr1-test-per-instance.jsonl` 的 `top10_files` 和 `runs/cr/test-frozen-rankings.jsonl`（完整排名）出发，配合 `runs/cr/labels.jsonl` 重算。
- 本轮到此停下，没有开始 CR-2。

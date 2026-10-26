# CHECKPOINT CR-1a：数据、语料、BM25（只在 dev 上）

日期：2026-10-26。执行：Cursor。合同：[CR-1-CONTRACT.zh-CN.md](CR-1-CONTRACT.zh-CN.md)（含 `7b4227c` 对第 0 节测试基线的更新）。

本轮没有调用付费模型 API，没有跑 agent，没有用 Docker。下面的 dev 数字只用于后续选择，不是结果。

## 1. 提交与测试

| 提交 | 内容 |
|---|---|
| `90750f3` | `src/retrieval/` 核心模块与离线单测，`pyproject.toml` 的 `retrieval` 可选依赖组 |
| `eb605f3` | `scripts/cr_prepare.py`，`docs/retrieval/cr1-split.json` |
| `fdb5dac` | `scripts/cr_index.py` |
| `ccbbef4` | `scripts/cr_eval.py`，隔离测试与端到端测试，`docs/retrieval/cr1a-dev-summary.json` |

检查点本身在 `ccbbef4` 之后单独提交。禁改路径（`src/runtime/`、`src/tools/`、`src/harness/`、`scripts/run_*.py`、`docs/sprint/`、A5/A6 文件）没有改动；`docs/sprint/clean-images/` 和 `configs/sprint/main-targets.yaml` 只读取。

**全量 pytest（`ccbbef4`，`.venv` 的 Python 3.13.9，pytest 9.1.1）：741 passed、24 skipped、0 failed，62 秒。**

- 与合同基线（`515a7e8`：654 passed、24 skipped、5 failed）对照：741 = 659 个原有用例 + 82 个新增 `test_retrieval_*`。**没有新增失败。**
- 基线里的 5 个失败用例在本机这次运行中全部通过（单独再跑一遍也是 5 passed）。我没有修改它们或相关代码，照实记录；差异应来自运行环境（基线约 25 分钟，本机 1 分钟，环境明显不同）。
- 24 个 skipped 都是需要 Docker 或 Linux 进程组的用例，与基线数目一致。
- 日常验证：`pytest tests/test_retrieval_*.py` 为 82 passed。

## 2. 仓库缓存

- 缓存目录 `D:/crq/cache/cr`（仓库外）。12 个仓库完整 `git clone --bare`，没有用 `--filter`。
- **commit 解析：499/499 全部解析，无缺失。**（django 231 题共用 230 个 base_commit。）
- 体积：bare 仓库合计 1.65 GiB（matplotlib 493 MiB、django 265 MiB、sympy 206 MiB、astropy 196 MiB、scikit-learn 194 MiB、sphinx 111 MiB，其余都在 60 MiB 以下）；切块/token 的 sqlite 缓存合计 430 MiB（sympy 143 MiB、django 100 MiB）。

耗时：

| 步骤 | 耗时 |
|---|---|
| clone 第一轮（12 个并发） | 7 个小仓库 55–294 秒完成；5 个大仓库约 870 秒后失败，见第 7 节第 1 条 |
| clone 重试（5 个大仓库并发，走本机代理） | 331–563 秒，墙钟 564 秒 |
| `cr_prepare.py`（parquet sha、commit 核对、划分、500 题 `ls-tree`） | 约 50 秒 |
| `cr_index.py`（全部 500 题的切块和 token 缓存，6 进程，从空缓存开始） | 墙钟 270.5 秒；最慢的 sympy 270 秒，django 109 秒 |
| `cr_eval.py labels`（500 题标签） | 约 10 秒 |
| `cr_eval.py run --split dev`（4 个检索器，100 题） | 56 秒，每题中位 0.57 秒（含每题现建两个 BM25 索引） |

## 3. 语料统计

语料 = base_commit 下的 `.py` 文件，去掉测试文件（判定函数 `chunking.is_test_path`）。

每题的语料规模（500 题；dev 100 题的分布基本相同）：

| | 最小 | p10 | 中位 | 均值 | p90 | 最大 |
|---|---|---|---|---|---|---|
| 文件数 | 21 | 115 | 820 | 650 | 861 | 1008 |
| 块数 | 293 | 2811 | 9881 | 9945 | 17765 | 29579 |

分仓库：

| 仓库 | commit | 唯一 blob | 文件实例 | blob 复用率 | 每题文件数中位 | 每题块数中位 |
|---|---|---|---|---|---|---|
| django | 230 | 6353 | 194611 | 0.967 | 853 | 10196 |
| sympy | 75 | 9274 | 57811 | 0.840 | 768 | 21821 |
| sphinx | 44 | 1959 | 8141 | 0.759 | 186 | 4970 |
| matplotlib | 34 | 4661 | 26305 | 0.823 | 772 | 8340 |
| scikit-learn | 32 | 3816 | 17481 | 0.782 | 535 | 4287 |
| astropy | 22 | 2874 | 10544 | 0.727 | 503 | 8603 |
| xarray | 22 | 1087 | 2280 | 0.523 | 102 | 2243 |
| pytest | 19 | 719 | 1504 | 0.522 | 80 | 1778 |
| pylint | 10 | 1929 | 4712 | 0.591 | 381 | 2574 |
| requests | 8 | 279 | 500 | 0.442 | 75 | 622 |
| seaborn | 2 | 125 | 223 | 0.439 | 112 | 1007 |
| flask | 1 | 33 | 33 | 0 | 33 | 437 |

- **blob 复用率**（1 − 唯一 blob 数 / 各 commit 文件实例总数）：总体 **0.898**（33109 个唯一 blob 对 324145 个文件实例）。
- **AST 失败率**：唯一 blob 的 **0.015%**（5/33109），文件实例的 0.0015%。失败的 5 个文件：`requests/packages/urllib3/contrib/ntlmpool.py`（Python 2 语法），以及 pylint 的 4 个 `doc/data/messages/.../good.py|bad.py` 示例文件（其中一个本来就是语法错误示例）。它们退回 60 行窗口、步长 40。这 5 个文件都不是任何题的 gold 文件。

## 4. 标签统计

| | 全部 500 题 | dev 100 题 |
|---|---|---|
| unretrievable | **0** | 0 |
| 被排除的 gold 文件条目 | 新建文件 1（astropy-13398）、非 `.py` 1（pylint-4661）、测试 0、base 中不存在 0 | 无 |
| gold 文件数分布 | 1 个：430；2：48；3：13；4：6；5：1；6：1；21：1（sympy-13091） | 1 个：85；2：11；3：3；5：1 |
| 函数级 gold 块总数 | 1061（每题中位 1，均值 2.12，最大 56） | 205（中位 1，均值 2.05，最大 16） |
| 落到模块块的比例 | **11.3%**（120/1061）；101 题至少有一个模块块 | 13.2%（27/205）；21 题 |
| 按 hunk 字面读法会得到不同函数级标签的题 | 10 | 2 |

astropy-13398 和 pylint-4661 除被排除的那一个文件外还有其他语料内 gold 文件，所以仍然可检索。最后一行的含义见第 7 节第 5 条。

## 5. dev 指标（100 题，全部可检索）

全部题平均：

| 检索器 | hit@1 | hit@5 | hit@10 | recall@10 | MRR | 函数 hit@5 | 函数 hit@10 |
|---|---|---|---|---|---|---|---|
| 随机 | 0.000 | 0.020 | 0.060 | 0.055 | 0.019 | 0.000 | 0.000 |
| 路径提及 | 0.490 | 0.760 | 0.860 | 0.834 | 0.623 | 0.470 | 0.600 |
| BM25（块聚合） | 0.460 | 0.670 | 0.800 | 0.769 | 0.570 | 0.470 | 0.590 |
| BM25（整文件） | 0.430 | 0.720 | 0.840 | 0.804 | 0.566 | — | — |

仓库宏平均（11 个仓库等权；flask 在 dev 中没有题）：

| 检索器 | hit@1 | hit@5 | hit@10 | recall@10 | MRR | 函数 hit@5 | 函数 hit@10 |
|---|---|---|---|---|---|---|---|
| 随机 | 0.000 | 0.064 | 0.179 | 0.176 | 0.053 | 0.000 | 0.000 |
| 路径提及 | 0.517 | 0.821 | 0.867 | 0.823 | 0.648 | 0.484 | 0.629 |
| BM25（块聚合） | 0.355 | 0.674 | 0.772 | 0.683 | 0.484 | 0.373 | 0.481 |
| BM25（整文件） | 0.434 | 0.735 | 0.851 | 0.802 | 0.578 | — | — |

分仓库（hit@5 / MRR）：

| 仓库 | n | 随机 | 路径提及 | BM25 块 | BM25 整文件 |
|---|---|---|---|---|---|
| django | 50 | 0.00 / 0.004 | 0.76 / 0.673 | 0.74 / 0.647 | 0.82 / 0.610 |
| sympy | 14 | 0.00 / 0.008 | 0.86 / 0.579 | 0.79 / 0.634 | 0.71 / 0.664 |
| matplotlib | 8 | 0.00 / 0.012 | 0.88 / 0.588 | 0.50 / 0.469 | 0.38 / 0.335 |
| sphinx | 7 | 0.00 / 0.016 | 0.14 / 0.194 | 0.14 / 0.194 | 0.43 / 0.272 |
| astropy | 5 | 0.00 / 0.025 | 0.80 / 0.710 | 0.80 / 0.710 | 0.80 / 0.720 |
| xarray | 5 | 0.20 / 0.053 | 0.60 / 0.469 | 0.20 / 0.163 | 0.20 / 0.172 |
| scikit-learn | 4 | 0.00 / 0.021 | 1.00 / 0.875 | 0.75 / 0.792 | 0.75 / 0.792 |
| requests | 2 | 0.00 / 0.069 | 1.00 / 0.750 | 0.50 / 0.158 | 1.00 / 0.375 |
| pylint | 2 | 0.50 / 0.258 | 1.00 / 0.292 | 1.00 / 0.225 | 1.00 / 0.667 |
| pytest | 2 | 0.00 / 0.107 | 1.00 / 1.000 | 1.00 / 1.000 | 1.00 / 0.750 |
| seaborn | 1 | 0.00 / 0.014 | 1.00 / 1.000 | 1.00 / 0.333 | 1.00 / 1.000 |

补充（只供 CR-1b 选择参考）：

- 路径提及：100 题里 58 题的 issue 文本命中了至少一个语料文件，其中 39 题命中的文件里包含 gold 文件。
- BM25 块聚合减整文件，按题 paired bootstrap（10000 次，种子 20261018）：hit@5 −0.05 [−0.12, 0.02]，MRR +0.004 [−0.058, 0.065]。两者区间都跨 0，未检出差异。
- 随机基线的 6 次 hit@10 都出现在小语料题上（requests 21 个文件、pytest 86 个、xarray 116 个、pylint 124 个等），不是泄漏。

## 6. 三道 dev 题

选法：`random.Random(20261018).sample(排序后的可检索 dev id, 3)`，写在 `cr_eval.py` 里。

**django__django-13195**

```text
HttpResponse.delete_cookie() should preserve cookie's samesite.
Description

We noticed we were getting this warning message from Firefox:
'Cookie “messages” will be soon rejected because it has the “sameSite” attribute set to “none” or an invalid value, without the “secure” attribute. To know more
```

- gold：`django/contrib/messages/storage/cookie.py`、`django/contrib/sessions/middleware.py`、`django/http/response.py`
- BM25（块聚合）前 5：`django/http/response.py`、`django/contrib/sessions/middleware.py`、`django/contrib/messages/storage/cookie.py`、`django/views/i18n.py`、`django/middleware/csrf.py`。三个 gold 文件排第 1–3；排第一的块是 `HttpResponseBase.delete_cookie`。

**django__django-15814**

```text
QuerySet.only() after select_related() crash on proxy models.
Description

When I optimize a query using select_related() and only() methods from the proxy model I encounter an error:
Windows 10; Python 3.10; Django 4.0.5
Traceback (most recent call last):
 File "D:\study\django_college\manage.py",
```

- gold：`django/db/models/sql/query.py`
- BM25（块聚合）前 5：`django/db/models/query.py`、`django/core/management/base.py`、`django/core/management/__init__.py`、`django/db/models/sql/compiler.py`、`django/db/models/deletion.py`。gold 排第 6（整文件 BM25 排第 7）。异常栈里提到的 4 个文件都被路径提及参照识别出来，gold 在路径提及排序中排第 4。

**sympy__sympy-23413**

```text
bug with HNF removing rows
I expect
`np.flip (hermite_normal_form (Matrix (np.flip (np.array ([[5, 8, 12], [0, 0, 1]]))).T).T))`
to give
`[[5,  8, 0], [0,  0, 1]]`
but instead I get
`[[5,  8, 0]]`
It seems to be falsely identifying my matrix as rank-deficient and removing the row when I try to
```

- gold：`sympy/polys/matrices/normalforms.py`
- BM25（块聚合）前 5：`sympy/matrices/normalforms.py`、`sympy/polys/matrices/normalforms.py`、`sympy/polys/numberfields/modules.py`、`sympy/plotting/plot.py`、`sympy/matrices/decompositions.py`。gold 排第 2，第 1 名是同名的公开包装模块（整文件 BM25 把 gold 排第 1）。

（issue 原文的 `\r\n` 在上面显示为换行。）

## 7. 遇到的问题和取舍

1. **clone 网络失败。** 12 个仓库直接并发 clone 时，5 个大仓库在约 870 秒后失败（git 退出码 128，单独重试 django 报 `Recv failure: Connection was reset`）。本机系统代理是 `127.0.0.1:7890`，git 原本没有走它。重试时只在命令行加了 `git -c http.proxy=http://127.0.0.1:7890 clone --bare`，**没有改全局 git 配置**。clone 是在后台 PowerShell 里做的；`cr_prepare.py` 运行时发现 12 个仓库都已存在，跳过了 clone。`repo_cache.ensure_clone` 本身不带代理参数，换机器重跑时可能要同样处理。
2. **测试基线。** 我开工后，合同第 0 节在 `7b4227c` 中被更新为 5 个基线失败。本机这 5 个都通过，见第 1 节。
3. **缓存键与块头。** 合同要求块文本前加 `# path: <path>  symbol: <symbol>`，这行依赖路径，而缓存键是 blob sha。我按 blob sha（加 `chunk1-tok1` 版本号）缓存块结构、正文和正文 token，块头的 token 在组装快照时按 (path, symbol) 现算再相加。BM25 的结果与"整段带头文本一起 tokenize"完全相同。**CR-1b 要注意**：向量是对带头文本编码的，同一 blob 换路径后向量会不同，缓存键需要加上 path（或者合同另定）。
4. **切块细节。** 只有模块体和类体的直接子节点会成为函数块或类块；写在顶层 `if`/`try` 里的函数留在模块块中。函数内部的类并入函数。类里的嵌套类单独成块，symbol 为 `Outer.Inner`。函数范围从第一个装饰器开始。每个文件都输出一个模块块（`<module>`，空文件也有），保证每个语料文件至少有一块，块聚合排序能覆盖全部文件。窗口块的 symbol 为 `<window a-b>`。
5. **函数级标签的读法（偏离字面）。** 合同写的是"取每个 hunk 在旧文件侧的改动行（删除行；纯新增时取插入点前一行）"。一个 hunk 里可能有几段分开的改动，比如前一段有删除、后一段是纯新增。我按 hunk 内每一段连续的 `-`/`+` 计算：有删除的段取删除行，纯新增的段取插入点前一行。按字面"整个 hunk 有删除就只取删除行"来读，会漏掉同一 hunk 里其他函数中的纯新增。两种读法只在 10/500 题（dev 2 题）上不同。`LabelView` 里两份都存了（`gold_chunks` 和 `gold_chunks_per_hunk`），指标用前者。这只影响函数级这个次要指标，如果审计认为应按字面读，改一个参数即可。在文件开头插入时（前一行是第 0 行）记为模块块。窗口切块的文件取第一个包住该行的窗口；实际没有 gold 文件落在窗口切块的文件上。
6. **测试文件规则。** 按合同只看目录段（`tests`/`test`/`testing`）和文件名模式，没有加仓库特例。会顺带排除 `sympy/testing/` 这个真实的包；文件名叫 `testing.py` 或 `tests.py` 的不排除。实际没有任何 gold 文件被判为测试（排除计数为 0），这条规则对标签没有影响。
7. **BM25 细节。** idf = log(1 + (N − df + 0.5)/(df + 0.5))。查询里同一个词只算一次，不计查询词频。块文档 = 块头 + 正文；整文件文档 = `# path: <path>` + 整个文件。issue 里不在语料词表中的词直接忽略。
8. **路径提及参照的定义。** 路径：把 issue 中 `\` 换成 `/`，抽取以 `.py` 结尾的路径片段，片段等于某个语料路径或以 `/<语料路径>` 结尾即算提及（能接住异常栈里的绝对路径）。点分模块名：取能对应到语料文件的最长前缀，至少两段（单独的 `django` 不算）；仓库根下的 `src/`、`lib/` 会去掉后再匹配（pytest、matplotlib 的布局需要）。提及的文件排最前，组内和其余部分都按 BM25（块聚合）排序；函数级排序同理，提及文件中的块排最前。
9. **随机参照**：每题种子为 `sha256("20261018:<instance_id>:files")` 的前 16 位十六进制（块排序用 `:chunks`）。
10. **其他读取细节**：`ls-tree` 跳过符号链接（mode 120000）和子模块。源码按 UTF-8 解码（非法字节替换），去掉 BOM，CRLF 换成 LF；单独的 CR 没有处理。
11. **划分的分层分配方法**（合同只写了"按 repo 分层"）：对去掉 62 道保留题后的 438 题，按仓库比例用最大余数法分配 100 个 dev 名额（余数相同时按仓库名），每个仓库在排好序的 id 上用 `random.Random(20261018).sample` 抽取，仓库按名字顺序访问。flask 只有 1 题，没有分到 dev。方法原文写在 `cr1-split.json` 的 `method` 字段里。保留题核对：72 个镜像中 60 个在 Verified，main-targets 60 个都在 Verified，两者的 id 并集有 74 个，与 Verified 的交集正好 62 个，与合同一致。
12. **依赖**：`pyproject.toml` 新增可选组 `retrieval = ["numpy==2.5.3", "pyarrow==25.0.1"]`。合同没有提到 pyarrow，但 `dataset.py` 需要它，所以一并锁定版本。torch 和 sentence-transformers 留到 CR-1b 再加。
13. **合同布局外的放置**：随机和路径提及两个参照没有新建模块，放在 `fusion.py`（它们本质上是"对已有排序做重排"）。快照组装和 blob 缓存放在 `chunking.py`。RRF 没有写，留给 CR-1b。
14. **防误用**：`cr_eval.py run --split test` 必须显式加 `--allow-test` 才会执行，留给 CR-1c。检索结果先写入 `runs/cr/dev-rankings.jsonl`，之后才加载 `LabelView` 打分。
15. **额外提交的文件**：`docs/retrieval/cr1a-dev-summary.json`（dev 汇总、分仓库指标、3 个示例），供审计核对，不是结果文件。逐题文件和排序结果在 `runs/cr/`，没有提交。

## 8. 交接文件 sha256（按 HANDOFF-CONVENTIONS，换行归一化为 LF）

| 文件 | sha256 |
|---|---|
| `docs/retrieval/cr1-split.json` | `3b1f90369a47dcd0b40eef0f614a6d4cf93c112da33f4fd14476d5e84f4c0b0f` |
| `docs/retrieval/cr1a-dev-summary.json` | `b054a94833cbde3afabf03afdff9ef4cb939642aee15628cd71ad690c0f98e1a` |
| parquet（原始字节，写在划分清单里） | 见 `cr1-split.json` 的 `parquet_sha256` |

## 9. 复现命令

```powershell
.\.venv\Scripts\python.exe scripts/cr_prepare.py
.\.venv\Scripts\python.exe scripts/cr_index.py --workers 6
.\.venv\Scripts\python.exe scripts/cr_eval.py labels
.\.venv\Scripts\python.exe scripts/cr_eval.py run --split dev
.\.venv\Scripts\python.exe -m pytest tests/test_retrieval_*.py
```

CR-1a 到此为止，等审计，未开始 CR-1b。

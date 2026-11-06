# 离线检索：查询写法和检索器哪个更重要，第 0 步检索能不能帮到慢运行

日期：2026-11-06。只用本地模型和已有数据，没有调用付费模型。脚本在 `scripts/analysis/retrieval_queries/`，结果在 `docs/analysis/retrieval-queries-results.json`、`retrieval-queries-per-query.jsonl` 和 `retrieval-queries-posthoc.json`。

## 结论

- **问题 1：在效果最好的检索器上，issue 全文已经是最好的查询，换检索器比改写查询影响大。** 在 CR-1 的 test 400 上，把全文换成标题、首段或规则抽取的标识符，只对 BM25 有帮助（Hit@5 +4 到 +6 个百分点），改写后的 BM25 仍比全文 hybrid 低 12 到 14 个点；对 embedding 和 hybrid，三种改写都没有提高，规则抽取的标识符让 hybrid 降了 10.2 个点。模型在 CR-2 里自己写的搜索模式拿来当检索查询更差：每次运行的第一条搜索查询配 hybrid，比全文 hybrid 低 21.7 个点。
- **问题 2：能，但只有一半左右。** CR-2 N 组首次触碰 gold 文件晚于第 4 步的运行有 34 次（13 道题）。冻结的配置（全文 + hybrid，也就是 CR-2 P 组的预取）在第 0 步就把 gold 文件排进前 5 的，占 19 / 34 次运行（9 / 13 道题）。
- **事后核对（没有预注册）：命中时确实省步数，未命中时反而拖慢。** 在这 9 道预取命中的慢题上，P 组平均第 1.33 步碰到 gold，对照的 T 组是 9.29 步，P − T = −7.96 步，95% 区间 [−13.6, −2.8]，9 道里 8 道更快、0 道更慢。另外 4 道预取没命中的慢题，P 比 T 慢 3.45 步，区间 [0.2, 6.7]。两边抵消，这是 CR-2 总体上看不出 P − N 差异的一个原因。
- **判定：按预先写定的规则，D3 成立，结论是“值得接入实验”；但我的判断是关闭“改写查询、更换检索器”这条线。** 原因是 D3 的 50% 阈值定低了：参照配置（全文 + hybrid）自己就有 55.9%，已经过线；另外 5 个过线的配置，在 test 400 上没有一个比全文 hybrid 好。D1、D2 都不成立。真正值得接着做的，是第 0 步预取在没命中时的代价问题（见第 7 节）。这是看过结果之后才提出的假设，要另做预注册。

## 1. 方法

**数据和冻结配置**（全部只读）：
- 检索沿用 CR-1 冻结配置：`cr1-freeze` 标签，`docs/retrieval/cr1-freeze.json`；
- 语料是 base_commit 下的非测试 `.py` 文件，AST 切块，缓存版本 `chunk1-tok1`；
- 题目用 CR-1 test 400（`docs/retrieval/cr1-split.json`）；
- CR-2 数据来自 `docs/retrieval/cr2-runs.json`、`cr2-analysis.json`、`cr2-prefetch.json`，以及 `runs/cr2-main/` 下 N、T 两组 560 次运行的 `events.jsonl`；
- CR-2e 数据来自 `docs/retrieval/cr2e-runs-round1.json`、`round2.json` 和对应运行的事件日志。

**只读保证**：
- 切块索引和 jina 向量缓存都用 sqlite 的 `mode=ro` 打开；包装类遇到缓存未命中会直接报错，不会补写。
- 新查询的向量写到 `runs/retrieval-queries/vectors/`。
- 检索前后记录了冻结缓存文件的大小和修改时间，两次一致（`frozen_cache_unchanged: true`）。

**检索器**（与 CR-1 冻结配置相同）：
- `bm25`：块级 BM25（k1=1.2，b=0.75），文件分数取块的最大值；
- `embedding`：jina-embeddings-v2-base-code @516f4baf，最长 512 token，fp16；
- `hybrid`：文件级 RRF，k=60，embedding 权重 2。

新查询的向量用同一个编码器现场编码。用同样配置重新编码 16 条 issue 全文，和冻结向量的最小余弦相似度是 0.999999。1644 条新查询文本里有 8 条超过 512 token 被截断。

**查询类型**（看结果前写定，规则在 `rq_queries.py`）：

| 名称 | 内容 | 适用 |
|---|---|---|
| full | issue 全文（CR-1 原样） | test 400 |
| title | 第一行非空行 | test 400 |
| first_para | 从第一行非空行到第一个空行 | test 400 |
| identifiers | 规则抽取：文件路径、traceback 帧（路径和函数名）、带点的名字、camelCase、snake_case、`名字(` 形式的调用、反引号片段；去掉 URL，按出现顺序去重。抽不到时退回标题（5 道题） | test 400 |
| model_first | 运行里第一条 `search_code` 的 pattern，或第一条 grep/rg/git grep 的模式（只算管道最左边的命令） | CR-2 N、T 组 |
| model_first3 | 前 3 次模型调用里所有这样的模式，去重后拼在一起 | CR-2 N、T 组 |
| retrieve_code | CR-2e 里模型调用 `retrieve_code` 时写的查询，每条单独评估 | CR-2e，只描述 |

- 构造查询时没有用到 gold 文件。gold 只在打分阶段读入。
- `search_code` 的 `path`、`glob` 参数没有并入查询文本。
- CR-2 的 56 道题全部在 CR-1 test 集里，也全部属于 CR-1 holdout 62。所以它们的 full、title、first_para、identifiers 查询和 test 400 里的完全相同。
- 模型查询只取 N、T 两组：P、O 两组的首条消息里有预取或正确文件，模型写的查询会受影响。T 组的 `retrieve_code` 在 CR-2 里一次也没被调用，所以 `retrieve_code` 查询只能来自 CR-2e。

**指标**：
- 文件级 Hit@1、Hit@5、MRR；gold 不在语料里的题不计入，和 CR-1 一致。
- 按题配对 bootstrap，10000 次，种子 20261018，95% 区间。
- 模型查询先在每道题内对该题的运行取平均，再按题配对。

**预先写定的判定规则**：脚本和规则在跑任何新查询之前提交，提交号 `3060b4c`，见 `run.py` 的文档字符串。
- R0：用全文查询时，bm25、embedding、hybrid 的前 100 文件排序必须和 CR-1 冻结的 test 排序逐题一致，否则先查原因、不报告其他数字。
- D1：存在某个改写 q（title、first_para、identifiers）和检索器 r，在 test 400 上同时满足三条：
  - Hit@5(q, r) − Hit@5(全文, r) ≥ 10 个百分点；
  - 配对区间下限 > 0；
  - Hit@5(q, r) ≥ Hit@5(全文, hybrid)。
- D2：在 CR-2 题上，model_first 或 model_first3 配某个检索器，满足和 D1 相同的三条，比较对象换成同一批题上的全文结果。
- D3：慢运行定义为 CR-2 N 组首次触碰步数 > 4 的运行（N 组的 75 分位）；k = 2 和 k = 10 作敏感性分析。存在某个查询类型和检索器的组合，且不是（全文, hybrid），能在第 0 步把 gold 文件排进前 5 的慢运行占比 ≥ 50%。（全文, hybrid）是 CR-2 P 组已经测过的预取，只报告，不计入。
- D1、D2、D3 任一成立，就值得接入实验；都不成立就关闭这条线。

提交 `3060b4c` 之后改过一处代码：hybrid 列“改写 − 全文”的配对结果被同名键覆盖，只留下了 Hit@5，Hit@1 和 MRR 的差值丢失。修复后 Hit@5 的数值没有变化，判定也没有变化。

## 2. 复现 CR-1 基线（R0）

全文查询在 test 400 上，三个检索器的前 100 文件排序和 `runs/cr/test-frozen-rankings.jsonl` 逐题完全一致（400 / 400，0 处不同）。指标和 CR-1 结果文档一致：

| 检索器 | Hit@1 | Hit@5 | MRR |
|---|---|---|---|
| bm25 | 0.400 | 0.675 | 0.520 |
| embedding | 0.530 | 0.850 | 0.665 |
| hybrid | 0.545 | 0.858 | 0.684 |

## 3. 查询类型 × 检索器（CR-1 test 400）

| 查询 | bm25 Hit@1 / Hit@5 / MRR | embedding Hit@1 / Hit@5 / MRR | hybrid Hit@1 / Hit@5 / MRR |
|---|---|---|---|
| full | 0.400 / 0.675 / 0.520 | 0.530 / 0.850 / 0.665 | 0.545 / **0.858** / 0.684 |
| title | 0.465 / 0.735 / 0.590 | 0.500 / 0.792 / 0.629 | 0.535 / 0.840 / 0.666 |
| first_para | 0.455 / 0.738 / 0.582 | 0.477 / 0.812 / 0.620 | 0.560 / 0.840 / 0.684 |
| identifiers | 0.445 / 0.715 / 0.563 | 0.400 / 0.662 / 0.521 | 0.472 / 0.755 / 0.599 |

**配对差值（Hit@5，同一检索器下改写减全文）**：

| | bm25 | embedding | hybrid |
|---|---|---|---|
| title − full | +6.0 [+0.7, +11.0] | −5.8 [−9.2, −2.2] | −1.8 [−5.5, +1.8] |
| first_para − full | +6.2 [+1.0, +11.3] | −3.7 [−6.8, −0.7] | −1.8 [−5.0, +1.5] |
| identifiers − full | +4.0 [−0.3, +8.5] | −18.8 [−23.3, −14.2] | −10.2 [−14.0, −6.5] |

单位是百分点。MRR 的方向相同：BM25 上三种改写都提高 MRR（+4.3 到 +7.0，区间都在 0 以上）；embedding 上三种都降低；hybrid 上 first_para 不变（−0.0），title −1.8，identifiers −8.6。

和全文 hybrid 比，所有其他组合的 Hit@5 都更低。最接近的是全文 embedding，−0.7 [−3.7, +2.2]。改写后最好的 BM25（first_para）是 −12.0 [−16.3, −8.0]。

**哪个影响更大**：
- 固定查询、换检索器，Hit@5 的极差：全文 18.3、title 10.5、first_para 10.3、identifiers 9.3，平均 12.1；
- 固定检索器、换查询，Hit@5 的极差：bm25 6.3、embedding 18.8、hybrid 10.3，平均 11.8。

两个平均值差不多，但方向不同。换检索器的收益是实打实的：从 BM25 换到 hybrid，全文 +18.3。改写查询的极差主要来自改写让 embedding、hybrid 变差。能带来提高的改写只有 BM25 上的 +6 个点。

**按仓库（Hit@5）**：

| 仓库 | 题数 | bm25 full | bm25 title | bm25 first_para | bm25 identifiers | hybrid full | hybrid title | hybrid first_para | hybrid identifiers |
|---|---|---|---|---|---|---|---|---|---|
| django | 181 | 0.75 | 0.73 | 0.71 | 0.74 | 0.87 | 0.84 | 0.83 | 0.80 |
| sympy | 61 | 0.61 | 0.59 | 0.66 | 0.70 | 0.75 | 0.70 | 0.77 | 0.72 |
| sphinx | 37 | 0.46 | 0.76 | 0.78 | 0.59 | 0.78 | 0.89 | 0.81 | 0.62 |
| scikit-learn | 28 | 0.89 | 0.86 | 0.89 | 0.89 | 0.96 | 0.89 | 0.89 | 0.89 |
| matplotlib | 26 | 0.42 | 0.73 | 0.73 | 0.38 | 0.77 | 0.88 | 0.88 | 0.62 |
| astropy | 17 | 0.71 | 0.88 | 0.88 | 0.76 | 1.00 | 0.94 | 0.94 | 0.76 |
| xarray | 17 | 0.65 | 0.88 | 0.88 | 0.76 | 1.00 | 1.00 | 1.00 | 0.71 |
| pytest | 17 | 0.76 | 0.76 | 0.65 | 0.76 | 0.88 | 0.76 | 0.82 | 0.76 |
| pylint | 8 | 0.50 | 0.75 | 0.88 | 0.75 | 0.75 | 1.00 | 0.88 | 0.75 |
| requests | 6 | 0.67 | 0.67 | 0.67 | 0.83 | 1.00 | 0.83 | 0.83 | 0.67 |
| seaborn | 1 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 |
| flask | 1 | 0.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 |

- 标题和首段对 BM25 的帮助集中在 sphinx、matplotlib、astropy、xarray、pylint，大约 +17 到 +38 个点。
- django 占 test 集的 45%，在这几个仓库上没有提高。
- hybrid 用标题后，sphinx 和 matplotlib 各升 11 个点，其他仓库多数下降。
- embedding 各列和宏平均见结果 JSON。宏平均受 seaborn、flask 各 1 道题影响很大，不宜拿来下结论。

## 4. CR-2 56 道题：模型自己写的查询

模型查询的来源：
- 560 次 N、T 运行里，552 次至少写过一条搜索查询；8 次一条也没有。
- 第一条查询里，525 条来自 `search_code`，27 条来自 grep；中位数在第 1 次模型调用时发出。
- 有 85 / 552 条第一条查询，发出时模型已经在同一次或更早的调用里碰过 gold 文件。model_first3 的窗口更长，这类情况更多。所以模型查询的数字偏乐观。
- 12 条 `run_command` 无法按 shell 规则切词，没有计入。

| 查询（56 道题） | bm25 Hit@5 | embedding Hit@5 | hybrid Hit@5 |
|---|---|---|---|
| full | 0.625 | **0.893** | 0.857 |
| title | 0.732 | 0.714 | 0.750 |
| first_para | 0.732 | 0.804 | 0.804 |
| identifiers | 0.750 | 0.696 | 0.804 |
| model_first（552 次运行） | 0.694 | 0.567 | 0.640 |
| model_first3（551 次运行） | 0.787 | 0.713 | 0.755 |

| 配对差值（Hit@5，百分点） | 同检索器减全文 | 减全文 hybrid |
|---|---|---|
| model_first, bm25 | +6.9 [−6.8, +20.9] | −16.3 [−28.6, −4.3] |
| model_first, embedding | −32.6 [−44.9, −20.6] | −29.0 [−41.8, −16.1] |
| model_first, hybrid | −21.7 [−34.5, −9.2] | 同左 |
| model_first3, bm25 | +16.2 [+3.9, +28.6] | −7.0 [−17.7, +3.4] |
| model_first3, embedding | −18.0 [−29.6, −6.4] | −14.5 [−25.9, −3.2] |
| model_first3, hybrid | −10.2 [−21.2, +0.7] | 同左 |

- 模型写的是给 grep 用的短模式，比如 `class GenericForeignKey`、`def get_prep_value`。
- 拿它们当检索查询，只在 BM25 上比全文好：model_first3 +16.2，区间在 0 以上。但仍不超过全文 hybrid，而且这批查询里有一部分是碰过 gold 之后写的。
- 对 embedding 和 hybrid，模型查询都明显更差。
- D2 不成立。

在这 56 道题上，全文 embedding（0.893）比全文 hybrid（0.857）高，和 CR-1 holdout 62 上的情况一致（0.887 对 0.855）。但在 test 400 上两者没有可分辨的差异。

## 5. CR-2e 的 `retrieve_code` 查询（只描述）

CR-2e 两轮共有 10 次 `retrieve_code` 调用，分布在 3 道题上，全都在第 1 次模型调用时发出。这 3 道题属于 CR-2e 的开发题，是按 CR-2 N 组最慢挑出来的，原定只用于开发检查。这里只列出来，不参与任何判定。

| 题 | 查询（节选） | bm25 / embedding / hybrid 的 gold 排名 | 全文 hybrid 的 gold 排名 |
|---|---|---|---|
| django-15973 | migration state resolve many-to-many through string reference to model in another app | 2 / 3 / 1 | 7 |
| django-15973 | migration state resolve many-to-many through string reference to model | 4 / 4 / 2 | 7 |
| django-15973 | ManyToManyField through string reference resolution in migration state | 8 / 8 / 8 | 7 |
| sphinx-7440 | glossary duplicate term case-insensitive duplicate term description（另 2 条相近） | 1 / 1–2 / 1 | 3 |
| sympy-19040 | factor with extension=True drops a factor, multivariate factorization over extension（4 条相近） | 1–2 / 2 / 1 | 11 |

模型专门写给检索工具的自然语言查询，在这 3 道难题上把 gold 排到了 hybrid 的第 1–2 名（10 条里有 9 条），比全文好。这和第 4 节里 grep 模式的表现差别很大。但只有 3 道题，而且是按难度挑出来的，只能作为线索。

## 6. 潜在价值：慢运行在第 0 步检索能不能命中

CR-2 N 组 280 次运行的首次触碰步数：中位数 2，75 分位 4，90 分位 5，18 次从未触碰（记为 41）。主分析取 k = 4：34 次慢运行，分布在 13 道题，其中 18 次从未触碰。第 0 步检索只依赖题目，所以一道题的所有慢运行命中与否相同。

先核对：全文 hybrid 在 56 道题上的 Hit@5 是 48 / 56，和 CR-2 P 组预取前 5 含 gold 的题数（48）一致。

| 查询, 检索器 | 慢运行命中（k=4，34 次） | 慢题命中（13 道） | 其余运行命中（246 次） |
|---|---|---|---|
| full, bm25 | 32.4% | 38.5% | 66.7% |
| full, embedding | **79.4%** | 76.9% | 90.6% |
| full, hybrid（P 组预取，参照） | 55.9% | 69.2% | 89.8% |
| title, bm25 | 41.2% | 61.5% | 77.6% |
| title, embedding | 52.9% | 61.5% | 74.0% |
| title, hybrid | 52.9% | 61.5% | 78.0% |
| first_para, bm25 | 55.9% | 69.2% | 75.6% |
| first_para, embedding | **82.4%** | 84.6% | 80.1% |
| first_para, hybrid | 41.2% | 61.5% | 85.8% |
| identifiers, bm25 | 50.0% | 53.8% | 78.5% |
| identifiers, embedding | 47.1% | 46.2% | 72.8% |
| identifiers, hybrid | 47.1% | 46.2% | 85.0% |

敏感性分析：
- k = 10：23 次慢运行，9 道题。全文 hybrid 命中 34.8%；全文 embedding 和 first_para embedding 都是 73.9%。
- k = 2：120 次慢运行，32 道题。全文 hybrid 命中 75.0%，多数组合都在 50% 以上。

和全文 hybrid 逐题比（13 道慢题）：
- 全文 embedding 多命中 django-15973 和 sympy-19040，少命中 pytest-5840；
- first_para embedding 多命中同样两道，没有少命中的；
- first_para bm25 和全文 hybrid 命中的题完全相同；
- 其余过线组合是有得有失，或者只失不得。

13 道题里挑 11 个组合的最大值，结果偏乐观。first_para embedding 在 test 400 上比全文 hybrid 低 4.5 个点，区间 [−8.3, −0.7]，不是更好的通用配置。

## 7. 事后核对（没有预注册）：P 组在慢题上的实际步数

慢题是按 N 组的结果挑出来的，直接比 P 和 N 会受均值回归影响。T 组和 N 组一样没有预取，又和挑题用的 N 运行相互独立，所以这里用 P − T 来比。脚本是 `posthoc.py`，区间是按题 bootstrap（10000 次，种子 20261026），题数很少。

| 题组 | 题数 | N 均值 | P 均值 | T 均值 | O 均值 | P − T（步） | P 比 T 快 / 慢的题 |
|---|---|---|---|---|---|---|---|
| 慢题，预取命中 | 9 | 8.38 | 1.33 | 9.29 | 1.02 | −7.96 [−13.6, −2.8] | 8 / 0 |
| 慢题，预取未命中 | 4 | 29.10 | 31.20 | 27.75 | 17.35 | +3.45 [+0.2, +6.7] | 0 / 3 |
| 其余题，预取命中 | 39 | 2.41 | 3.76 | 2.46 | 1.66 | +1.30 [−0.8, +3.8] | 24 / 8 |
| 其余题，预取未命中 | 4 | 2.65 | 2.85 | 2.75 | 4.40 | +0.10 [0.0, +0.3] | 0 / 1 |

- 9 道预取命中的慢题里，有 7 道 P 组 5 次运行都在第 1 步就打开了 gold 文件，和 O 组（直接给出正确文件）几乎一样。
- 4 道预取没命中的慢题是 django-11211、django-15973、sympy-19040、sympy-20428。P 组在 django-11211 上 3 次从未触碰 gold，T 组只有 1 次。
- “其余题、预取命中”这一行，P 的均值比 T 高 1.3 步，但区间跨 0。按题计数则是 P 快的多（24 对 8），说明均值被少数几道题拉高。

所以，第 0 步检索的潜在价值在慢运行里确实能兑现：命中时，模型几乎立刻去读 gold 文件。CR-2 总体看不出差异，原因之一是没命中的题被错误提示拖慢，和命中题的收益抵消了。

## 8. 判定

| 规则 | 结果 | 依据 |
|---|---|---|
| R0 | 通过 | 400 / 400 题、三个检索器的前 100 排序和 CR-1 冻结结果一致 |
| D1 | 不成立 | 改写只对 BM25 有提高，最多 +6.2 个点，不到 10；所有改写组合都低于全文 hybrid |
| D2 | 不成立 | model_first3 配 BM25 有 +16.2 [+3.9, +28.6]，但 Hit@5 0.787 低于同批题上全文 hybrid 的 0.857；其余组合都比全文差 |
| D3 | 成立（6 个组合） | 全文 embedding 79.4%、first_para embedding 82.4%、first_para bm25 55.9%、title embedding 52.9%、title hybrid 52.9%、identifiers bm25 50.0% |

按预注册规则，结论是“值得接入实验”。我的判断和建议：

1. **关闭“改写查询、更换检索器”这条线。** D3 的 50% 阈值是绝对值，没有和参照配置比；全文 hybrid 自己就有 55.9%，也过了线。所以 D3 成立，只说明第 0 步检索在慢运行里常常能命中，不说明哪种新写法或新检索器更好。过线的 5 个非参照组合，在 test 400 上都不比全文 hybrid 好：4 个显著更差，全文 embedding −0.7，区间跨 0。在 13 道慢题上，最多也只比参照多命中 2 道。阈值应该定成“比参照高多少”，这是预注册里的设计失误。
2. **第 0 步预取本身有价值，问题出在没命中的时候。** 第 7 节显示，命中时慢题的步数降到和 O 组差不多；没命中时反而变慢。如果继续，下一步不是换查询，而是先离线检验能不能事先判断预取是否可信，比如用检索分数的分布，只在可信时给提示。确认可行后再预注册一次真实运行。这个方向是看过结果才提出的，第 7 节也不是预注册分析，不能当作已证实的结论。
3. **模型专门写给检索工具的自然语言查询值得留意**（第 5 节，3 道题），但样本太小，只能当线索。

## 9. 局限

- 轨迹只来自一个模型（`deepseek-flash`，服务端别名，CR-2 期间出现过两个 `system_fingerprint`）。换模型后，搜索习惯和对预取提示的反应都可能不同。
- SWE-bench 是公开题目，模型训练时可能见过这些 issue 和代码，这会影响它找文件的快慢，也会影响它对提示的依赖。
- 慢运行只有 34 次、13 道题，其中 18 次从未触碰 gold，集中在少数几道题；第 6、7 节的比例和区间都很不稳定。
- 第 7 节是事后分析，挑题依据是 N 组结果。P − T 避开了这层选择，但比较的题组本身是看过数据才划分的。
- 模型查询的有效性偏乐观：有 85 / 552 条第一条查询发出时已经碰过 gold；`search_code` 的 `path` 范围没有并入查询文本。
- 规则抽取的标识符只是一种写法；更好的抽取规则（比如只保留 traceback 帧）没有试，试了也属于事后挑选。
- 全文超过 512 token 的 issue（test 集 29.5%）在 embedding 里被截断，这在 CR-1 里就已存在，这里沿用。

## 10. 复现

```
.venv\Scripts\python.exe scripts/analysis/retrieval_queries/run.py queries
set HF_HUB_OFFLINE=1
.venv\Scripts\python.exe scripts/analysis/retrieval_queries/run.py retrieve
.venv\Scripts\python.exe scripts/analysis/retrieval_queries/run.py score
.venv\Scripts\python.exe scripts/analysis/retrieval_queries/posthoc.py
```

- 需要本地的 CR-1 缓存（位置用 `CR_CACHE_DIR` 指定）、CUDA，以及 CR-2 / CR-2e 的运行目录（`runs/`，不入库）。
- 检索阶段约 28 分钟，大部分时间花在加载 django 和 sympy 的快照；打分阶段约 8 分钟。
- 中间产物（查询、向量、完整排序、检索报告）在 `runs/retrieval-queries/`，不入库。

# AUDIT CR-1a

日期：2026-10-26。审计：Claude Code。对象：[CHECKPOINT-CR-1a](CHECKPOINT-CR-1a.md)，提交 `90750f3`..`2fe07a1`。

结论：**通过**，可以进入 CR-1b。合同主体不改；CR-1b 需要的几条细化写进合同第 11 节。

## 1. 我独立核对了什么

| 项 | 方法 | 结果 |
|---|---|---|
| 划分 | 读 `cr1-split.json` | dev 100、test 400，无重叠，并集 500；62 道保留题全部在 test，与 dev 交集为 0 |
| 交接 sha | 按 HANDOFF-CONVENTIONS 重算 | `cr1-split.json`、`cr1a-dev-summary.json`、parquet 三个值都一致 |
| 文件级标签 | 不用 `labels.py`，自己从 diff 的 `--- a/` 行取 `.py` 路径（排除 `/dev/null`） | 500 题与 `runs/cr/labels.jsonl` 的 `gold_files` 全部相同 |
| dev 指标 | 用上面的独立标签，对 `runs/cr/dev-rankings.jsonl` 重算 hit@5、MRR | 四个检索器与检查点表格逐位相同 |
| 函数级标签 | 抽 3 题对照 hunk 头 | `deferred_to_data`、`_hermite_normal_form`、`pytest_runtest_makereport` 均正确 |
| 答案隔离 | 读 `dataset.py`、`cr_eval.py`、隔离测试 | `QueryView` 只有 4 个字段；排序先落盘再加载标签；`load_gold_diffs` 只有 `labels.py` 调用；`--split test` 需显式开关 |
| BM25 | 读 `bm25.py` | 标准 Okapi，idf 用 log1p 形式，实现正确 |
| 全量 pytest | PowerShell 下重跑 | 738 passed、24 skipped、3 failed，45 秒 |

## 2. 测试数目的差异

检查点写的是 741 passed、0 failed；我重跑得到 3 failed。这 3 个（`test_dataset_oracle`、`test_runner` 的两个 command judge）都在子进程里调用 `python -m pytest`，结果取决于 PATH 上的 `python` 是不是 `.venv` 的解释器。Cursor 的终端激活了 venv，我的没有。它们都属于合同第 0 节的基线失败，与 CR 的代码无关。**没有新增失败**这一结论成立。

## 3. 对检查点里取舍的判定

- 第 5 条，函数级标签按段读：**接受**。这比字面读法更合理，两种读法都保留了，只影响次要指标。
- 第 3 条，块头不进 blob 缓存：**接受**。BM25 等价。向量缓存的键在合同第 11 节规定。
- 第 4、6、7、8、11、12、13 条：接受，没有影响答案隔离或主比较。
- 第 1 条，clone 代理：接受。`ensure_clone` 不带代理，换机器时要注意，在 CR-1c 结果文件的复现说明里写一句即可。

## 4. dev 结果给 CR-1b 的信号（不是结论）

- 路径提及参照（hit@5 0.76、MRR 0.623）高于两种 BM25。58% 的 issue 直接写了文件路径或模块名。这说明 hybrid 之外还应冻结一个"提及优先"的版本，见合同第 11.3 节。
- BM25 块聚合与整文件在 dev 上未检出差异。冻结的 BM25 取块聚合：它能给出函数级排序，CR-2 的工具也要返回 symbol。整文件版本仍留在 hybrid 网格里。这个决定在看到 embedding 结果之前做出。
- sphinx（7 题）和 xarray（5 题）上两种 BM25 都很弱，test 上要单独看。

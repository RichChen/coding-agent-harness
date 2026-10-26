# AUDIT CR-1c

日期：2026-10-26。审计：Claude Code。对象：[CHECKPOINT-CR-1c](CHECKPOINT-CR-1c.md)、[CR-1-RESULTS](CR-1-RESULTS.zh-CN.md)，提交 `dd7bc95`。

结论：**通过**。CR-1 到此结束。最后一项独立重算由项目作者在本机跑一次脚本确认，见第 2 节；结果不一致就撤回这个结论。

## 1. 核对了什么

| 项 | 方法 | 结果 |
|---|---|---|
| test 只跑一次 | 读 `runs/cr/frozen-test.log`、`embed-jina-code-test.log`、reflog | 每条命令一次；HEAD 在运行期间只从 `1273804` 前进到结果提交 `dd7bc95` |
| 冻结代码 | 检查点第 2 节的 diff 记录；运行日志里没有 `WARNING: retrieval code differs` | 与 `f1d1b92` 相同 |
| 日志与结果文件一致 | 对照 `frozen-test.log` 的汇总行和结果文件第 3 节 | 六个检索器的 hit@1/5/10、recall@10、MRR、函数级指标逐位相同 |
| 逐题文件 | 抽 pytest-7205 的三行，对照 `labels.jsonl` 的 gold 文件 | gold 为 `setuponly.py`；BM25 排第 1、embedding 第 13、hybrid 第 7，与结果文件第 8 节一致 |
| 统计脚本 | 读检查点第 5、6 节 | `cr_report.py` 只读运行产物，不重新检索；看到输出后的两处修改都只影响渲染 |
| 表述 | 对照合同第 9 节 | 区间跨 0 的都写了"未检出差异"；没有夸大用词；embedding 选择接近噪声、截断、单次运行都写进了局限 |

对取舍的判定：第 6 节第 1 条（`cr_report.py` 多读了 token 数和 gold 文件，用于截断分组和例子展示）接受；第 6、8 条接受；第 7 条正确，AUDIT-CR-1b 和合同第 12 节由项目作者提交。

## 2. 待作者在本机确认的一项

审计期间命令行工具不可用，我写的独立重算脚本没有跑成。脚本 `runs/cr/audit_cr1c.py` 不用 `labels.py`，直接从 gold patch 取文件，重算 dev 和 test 冻结排序的 hit@5、MRR 和三组差值，并检查逐题文件的前 10 名与完整排序一致。期望输出：

- test：bm25 hit@5 0.675、MRR 0.520；embedding 0.850 / 0.665；hybrid 0.858 / 0.684；hybrid_mention 0.833 / 0.671。
- dev：与 CHECKPOINT-CR-1b 第 7 节一致。
- `top10 mismatches vs rankings 0`，`holdout flag mismatches 0`。

我的区间用 5000 次重抽、不同种子，和结果文件只会在小数点后第三位有出入。

## 3. 结果要点（给 CR-2 用）

- 主比较成立：hybrid 对 BM25，hit@5 +0.182 [+0.140, +0.225]，MRR +0.165 [+0.129, +0.199]，按仓库 cluster bootstrap 也不含 0。
- 文件级的提升几乎都来自 embedding：hybrid 对 embedding 未检出差异。hybrid 的额外好处在函数级（fn_hit@5 +0.075 [+0.037, +0.115]）。
- 提及优先重排让 hit@5 下降 0.025：213 道提到了路径的题里，91 道提到的文件不是要改的文件。异常栈里出现的常常是调用方，而不是出错的地方。
- dev 预测的 hybrid hit@5 是 0.90，test 是 0.858，回落的幅度符合"在 dev 上选择偏乐观"的预期。
- 62 道保留题上 hybrid hit@5 0.855、BM25 0.645。CR-2 用冻结的 hybrid，不用 hybrid_mention。

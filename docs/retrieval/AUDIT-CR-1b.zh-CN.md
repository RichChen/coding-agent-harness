# AUDIT CR-1b

日期：2026-10-26。审计：Claude Code。对象：[CHECKPOINT-CR-1b](CHECKPOINT-CR-1b.md)、[CR-1-FREEZE](CR-1-FREEZE.md)，提交 `732f9e5`..`1273804`。

结论：**通过**，可以进入 CR-1c。CR-1c 的补充要求写进合同第 12 节。

## 1. 核对了什么

| 项 | 方法 | 结果 |
|---|---|---|
| 只用 dev | 查 `runs/cr/` | 没有任何 test 的排序或打分文件；只有 `count-texts-test.json`（语料文本计数，不涉及标签，接受） |
| 冻结 | 读 tag 对象、`cr1-freeze.json`，`git diff f1d1b92 HEAD -- src scripts tests pyproject.toml` | tag `cr1-freeze` 指向 `4faaa53`；冻结的代码提交 `f1d1b92` 之后检索代码没有改动；工作区干净 |
| 选择规则 | 读 `select_hybrid`、网格表 | 与合同 11.3 一致，机械执行；门槛内两组，按 `k=60` 优先选中 |
| embedding 缓存键 | 读 `embed.py`、`EmbeddingView` | 键是实际编码文本（块头 + 前缀 + 正文）的 sha256；每个 (模型, revision, 最大长度) 一个库，meta 不符会报错。满足 11.2 |
| 向量质量检查 | 读 `Encoder.encode` | 非有限值和零范数都会抛错，向量归一化后入库 |
| 答案隔离 | 读新检索器签名与 `EmbeddingView` | 检索只用 `QueryView.problem_statement` 和语料；打分在排序落盘之后 |
| 远程代码固定 | 读检查点第 6.3 节与加载检查 | 处理到位：手工组装模块、从固定快照加载、加载后核对模块路径里的 commit hash |

**未完成**：我写了独立重算 dev 冻结指标的脚本 `runs/cr/audit_cr1b.py`（不入库），但审计期间命令行工具不可用，没有跑成。CR-1a 已经验证过打分路径；CR-1c 审计时我会在 test 上做同样的独立重算，这一项不阻塞 CR-1c。

## 2. 对检查点取舍的判定

- 第 2 条，依赖降到 transformers 4.x：**接受**。保留了合同的三个候选，Qwen 新旧环境向量余弦中位 0.999998。
- 第 4.2 节，embedding 选择用全部题平均而不是仓库宏平均：**接受**。这是合同第 7 节的第一层，也是默认口径。三个模型差距在 0.04 以内，换口径顺序就变，说明这个选择本身接近噪声，结果报告里照实写。
- 第 5 条，网格从 27 组删到 24 组：接受，开跑前定的。
- 第 8 条，NaN 检查粒度：接受。
- 第 9 条，`dataset.py` 读 `HF_HUB_CACHE`：接受。但这使 parquet 路径受环境变量影响，CR-1c 开跑前必须核对 parquet sha，见合同 12.1。
- 第 10 条，冻结前改代码并全部重跑：接受，做法正确。
- 第 11 条，40% 的 issue 超过 512 token：接受。BM25 用全文，只有 embedding 截断。结果报告的局限一节要写。

## 3. 更正 AUDIT-CR-1a 第 2 节

我在 CR-1a 审计里把 3 个测试失败归因于"PATH 上的 python 不是 venv"。这次 Cursor 没有激活 venv，这 3 个用例也通过了，所以这个解释不成立。失败只出现在我这边的命令行环境里，原因没有查明。结论不变：它们都是基线用例，与 CR 代码无关。

## 4. dev 上的信号（偏乐观，不是结果）

hybrid 和 embedding 都是在这 100 题上选出来的，下面的差值会高估 test 上的效果。

- 文件级 hit@5：BM25 0.67，embedding 0.82，hybrid 0.90，hybrid_mention 0.92。
- hybrid − BM25 的 MRR 差 +0.172 [+0.100, +0.244]。
- hybrid − embedding 的 MRR 差未检出差异。

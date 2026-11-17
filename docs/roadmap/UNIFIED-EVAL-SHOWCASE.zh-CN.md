# 统一评测展示 smoke

更新时间：2026-11-15

本 smoke 用三个无模型 fixture 展示统一评测层的边界：

1. `mini`：本地任务使用内置判定器；
2. `repo_pytest`：仓库任务保留 pytest 回执；
3. `swebench`：保留 ImageGrader 风格的 F2P/P2P 字段。

三种记录都进入同一个 `RunReceipt` 外壳，但各自的 `grader.kind`、原始回执和
证据来源仍然保留。这说明统一层统一的是任务身份、运行身份和报告入口，
不是把三种执行器或评分语义强行改成同一种。

运行：

```text
pytest -q tests/test_unified_showcase.py
```

这个 smoke 不启动模型、不启动 Docker、不读取或修改正式运行目录，也不证明
三个 adapter 已经可以自动执行。真实 adapter 接线、manifest 入口和更广泛的
仓库兼容性仍是后续工作。

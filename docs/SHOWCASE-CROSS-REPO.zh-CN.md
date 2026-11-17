# 跨仓库评测展示报告

这份报告用于展示项目已经超出单一仓库实验的范围，同时保留兼容性边界。
它由 `scripts/analysis/showcase_cross_repo.py` 从任务清单的仓库字段生成，
不读取运行目录，不启动模型、Docker 或 grader。

## 当前覆盖

`configs/rebench/fix13-tasks.yaml` 包含 30 道任务，去重后覆盖 **30 个不同仓库**，
每个仓库在这份清单中各有一条任务。示例包括 `keras-team/keras`、
`tox-dev/tox`、`vyperlang/vyper`、`holoviz/holoviews`、`mpmath/mpmath`、
`pytest-dev/pyfakefs`、`packit/packit` 和 `graphistry/pygraphistry`。

FIX-13 在这批任务上完成 240 个正式运行名额；ET、FIX-14 和 FIX-15 使用了同一批
题集或其固定子集继续做比较。它们的原始 runner、Docker 镜像、grader 和有效名额
仍然分别记录，不能把运行数直接当作仓库数。

## 结果入口

| 批次 | 正式运行数 | 组内解出运行 | 说明 |
|---|---:|---|---|
| FIX-13 | 240 | U 16/60；R 18/60；E 26/60；UT 28/60 | 四组在 30 道任务上比较 |
| FIX-13 ET | 60 | ET 27/60 | 在固定题集上追加 |
| FIX-14 | 120 | T8 26/60；O32 17/60 | 其中 U/UT 为历史参考格 |
| FIX-15 | 180 | U-new 17/60；N 23/60；E-new 24/60 | 同一题集上的 E 消融 |

完整解释仍以 [总矩阵](analysis/ALL-EXPERIMENT-MATRIX.zh-CN.md)、
[FIX-13 结果](analysis/fix13/FIX-13-RESULTS.zh-CN.md) 和
[FIX-15 结果](analysis/fix15/FIX-15-RESULTS.zh-CN.md) 为准。

## 能够支持的项目表述

当前可以诚实地说：

> 这是一个面向多个 Python 仓库和 SWE-bench 风格任务的可复现 Coding Agent 评测 Harness，支持独立 Docker 评分、轨迹记录、成本统计和工具循环干预分析。

当前不能说它已经支持任意 GitHub 仓库即插即用。新仓库仍需要准备基线提交、固定
镜像、测试路径、校准结果和对应的独立 grader。JavaScript、Go、Rust 等其他语言也
没有获得与当前 Python/pytest 路径同等的验证。

30 道题已经参与开发和正式实验；固定这批题继续调参属于开发或验证证据，不应再称为
新的未见测试集。运行数、仓库数和跨批次统计需要分别报告。

## 生成命令

```powershell
python scripts/analysis/showcase_cross_repo.py `
  --tasks configs/rebench/fix13-tasks.yaml `
  --format markdown `
  --output reports/showcase-cross-repo.md
```

该命令只读取任务清单并生成新报告，不修改任务清单和历史运行证据。

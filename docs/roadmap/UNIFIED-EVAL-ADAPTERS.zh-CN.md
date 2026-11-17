# 统一评测 adapter 接线说明

统一评测层的边界已经固定为“显式输入、保留原始回执、统一外壳”。

| adapter | 真实执行者 | 必须显式提供 | 统一层记录 |
| --- | --- | --- | --- |
| `mini` | `EvalRunner` | agent factory、任务 expected、配置 | `RunSpec`、状态、成本和 intervention provenance |
| `repo_pytest` | `RepositoryGrader` | `base`、`candidate`、`RepositorySpec` | `RunReceipt` + 原始 repo pytest 回执 |
| `swebench` | `ImageGrader` | image、delta、test overlay、F2P/P2P | `RunReceipt` + 原始 image grader 回执 |

`RepoPytestAdapter` 和 `SwebenchAdapter` 都要求调用方传入 `case_loader`。loader 可以是同步或异步函数，但必须返回明确的任务输入对象。adapter 不会扫描 `runs/`、按 task id 猜测文件、替换私有 grader 语义，也不会把 Docker 执行结果改写成统一的假指标。

`AdapterRegistry.execute_many_async()` 是统一入口的调度面。它按显式 manifest 顺序调用已注册 adapter，并把运行编号、adapter 名称、grader 类型、原始回执和来源写进稳定 envelope。失败的 adapter 未注册、任务类型不匹配或 case 输入不完整时会显式报错。

干预信息从 `AgentRunResult.intervention` 进入统一回执，包含策略名、策略版本、配置哈希、触发次数、提示注入次数、session 恢复标记和紧凑事件摘要。评测层可以据此做后续行为统计，但这些统计仍属于报告层，不会被伪装成 adapter 的评分结果。

本轮只做本地接口与 fixture 验证，没有调用模型、Docker 或付费 API。

# 统一评测兼容层状态

更新时间：2026-11-15

## 本次完成的范围

本次只增加 `src/eval/unified/` 兼容层和对应的契约测试，没有修改旧的
`EvalRunner`、`repo_grader`、`image_grader`、FIX-12～FIX-15 runner，也没有
启动模型调用、Docker 正式评测或付费实验。

兼容层提供四种记录：

- `TaskSpec`：任务身份、任务类型、仓库/基线信息、执行和评分配置；
- `RunSpec`：一次运行的配置、模型、干预策略、runner 版本和尝试编号；
- `GraderSpec`：评分器种类、版本和 manifest 引用；
- `RunReceipt`：统一报告外壳，同时保留 `raw_receipt` 和 `provenance`。

`normalize_legacy_result()` 可以把历史脚本产生的单条结果包装进新的
`RunReceipt`。它只识别通用状态并保留原始字段，不重算历史分数、不删除
历史字段，也不把不同评分器的内部语义强行混成同一种结果。

## 现有两条数据链路

### mini / 本地任务

```text
JSON 任务集 → EvalRunner → 宿主机 AgentLoop → expected/custom judge → CSV/Markdown
```

它适合小型任务、fixture 仓库和快速回归。结果的核心字段来自
`TaskResult`，例如 `success`、`expected_check`、步骤、工具失败和轨迹路径。

### repo pytest / SWE-bench 风格任务

```text
任务清单 → 固定基线和 Docker 镜像 → AgentLoop/container_files
→ 候选差异 → 独立 pytest 或 ImageGrader → grader receipt
```

这条链路要求更严格的基线哈希、固定镜像、测试路径、候选快照和独立回执。
通用 `EvalRunner` 不直接执行 Docker 评分，而是转交 H3/repo/SWE-bench 专用
路径。这种差异是有意保留的安全和证据边界，不应为了统一报告而消除。

## 目标链路

```text
统一 manifest → TaskSpec → adapter
                         ├─ MiniAdapter
                         ├─ RepoPytestAdapter
                         └─ SWEbenchAdapter
                  → 现有执行器和评分器
                  → Trace + raw receipt
                  → RunReceipt → 统一报告
```

第一版的统一发生在任务身份、运行身份、评分器标识、状态和证据索引层，
不是把三个执行器改成同一个执行器。`adapter`、`grader.kind`、
`raw_receipt` 和 `provenance` 必须同时保留，避免把 pytest 的 partial、
SWE-bench 的 F2P/P2P 或基础设施失败混成不可解释的单一布尔值。

## 兼容性边界

| 场景 | 当前结论 |
| --- | --- |
| mini JSON 任务 | 可由 `TaskSpec(kind="mini")` 表示 |
| Python 仓库和 pytest | 可由 `repo_pytest` 记录，仍使用原有独立 grader |
| SWE-bench 风格固定镜像 | 可由 `swebench` 记录，仍使用原有 ImageGrader/H3 路径 |
| 多个已准备好的仓库 | 已有题集和历史结果可以逐条导入 |
| 任意 GitHub 仓库自动接入 | 尚未实现；仍需基线、镜像、测试和校准 |
| 非 Python 项目 | 可在未来加入 adapter，但当前没有广泛验证 |

因此，统一层不会把当前项目宣传成“任意仓库即插即用”的平台。它首先
解决的是多种已存在评测路径的统一记录、历史回放和展示问题。

## 旧实验的效力

旧实验仍以原始运行目录、原 runner、原 grader 和原报告为准。新契约只
增加一层索引和归一化视图，因此不会推翻 FIX-12～FIX-15 的统计结果。

后续如果修改 E 提示、工具策略、Docker 镜像、评分规则或 runner 版本，
必须改变 `runner_version`、`config_name` 或 `provenance`，不能把新旧运行
直接合并成同一个实验样本。

## 无付费验证

测试文件：`tests/test_unified_eval_contract.py`

已验证：

1. mini 任务可以构造、序列化并生成稳定回执哈希；
2. repo pytest 和 SWE-bench 记录可以使用不同的 adapter/grader，并保留各自
   的原始回执；
3. 未知任务类型会被拒绝；
4. `pytest -q tests/test_unified_eval_contract.py`：3 passed。

下一步如果要继续做展示版，应增加三个 adapter 的只读导入器和一个统一报告
命令；优先回放已有 FIX-15/总矩阵结果，再做一个 mini fixture 和一个 Docker
mock smoke，不需要重新运行正式模型实验。

## 历史结果回放第一版

新增 `src/eval/unified/legacy.py`，只接受显式的 JSON 数组（或
`{"rows": [...]}`）运行摘要导出。它不会遍历 `runs/`，不会修改原始文件，
也不会根据轨迹重新计算历史分数。每一行只被包装成 `RunReceipt`，原始字段
保存在 `raw_receipt`，再按实验组生成一个小型摘要表。

这已经可以读取 FIX-15 的 `runs.json` 形状，用于展示已有跨题结果；组名、
评分器类型和原始行索引会进入 provenance。它不是通用的历史目录扫描器，
也不会把不同批次的报告自动合并。跨批次总分析仍应使用已经审阅过的总矩阵
和各批次的原报告。

无模型验证：`tests/test_unified_legacy.py` 与统一契约测试共 5 项通过。

## 真实 mini adapter 的最小接线

新增 `MiniAdapter`，通过显式注入的 `agent_factory` 复用现有 `EvalRunner` 的单题执行和判定逻辑，并把结果包装为 `RunReceipt`。它只接受 `kind=mini`，不接触 Docker 或仓库级 grader；在事件循环内必须显式使用 `execute_async`，不会偷偷创建第二个执行器。

当前统一 CLI 的 `--manifest --mock` 仍是无模型展示模式。真实 mini adapter 已提供 Python API 和 Mock LLM contract test，但 repo pytest/SWE-bench 真实 adapter 尚未接入统一 CLI；这两条链路继续由原有专用入口负责。

## Manifest mock 入口（2026-11-15）

`scripts/unified_eval_report.py` 现在也接受显式 `--manifest`。Manifest 可以是
JSON 或 YAML 的数组，也可以是包含 `entries`/`tasks` 的对象；每个条目必须明确
任务类型、adapter 和 grader。当前只允许配合 `--mock` 使用：

```text
python scripts/unified_eval_report.py --manifest demo.yaml --mock
```

这会通过三个 `MockAdapter` 构造统一回执并生成摘要，不会自动选择真实 runner，
也不会调用模型、Docker 或 grader。省略 `--mock` 会明确报错，避免把“统一入口”
误解成真实执行器已经接线。原有 `--input` 历史回放行为保持不变。

## 最小 adapter facade（2026-11-15）

新增 `src/eval/unified/adapters.py`，提供显式 `ManifestEntry`、`AdapterRegistry`
和 `MockAdapter`。它可以校验 mini、repo pytest、SWE-bench 三种任务的 adapter
与 task kind 是否一致，并在 mock 模式下生成保留原始评分字段的 `RunReceipt`。

这一步仍然没有把旧执行器接到统一入口：没有注册的 adapter 会明确报错，不能
自动猜测 runner；现有 `EvalRunner`、`repo_grader`、`image_grader` 和正式实验
脚本未修改。真实 adapter 接线需要下一轮分别设计 runner 参数、Docker 生命周期、
评分回执和失败分类的映射，不能在这个 facade 中隐式完成。

测试：`pytest -q tests/test_unified_adapters.py`。

## 只读报告 CLI

新增 `scripts/unified_eval_report.py`。它要求用户显式指定一个 JSON 文件，
输入可以是运行摘要数组或 `{"rows": [...]}`。CLI 只读取该文件，使用
`legacy.py` 包装和汇总，不遍历 `runs/`，不读取目录中的其他证据，也不调用
模型、Docker、adapter 或 grader。

示例：

```powershell
python scripts/unified_eval_report.py `
  --input path\to\explicit-runs.json `
  --task-kind mini `
  --adapter mini `
  --grader-kind builtin `
  --format markdown
```

把 `--format` 改为 `json` 可得到机器可读的组摘要；`--output` 只会写入一份
新报告，不会改写输入文件。该命令适合先把已有 FIX-15 导出或其他明确选定
的结果整理成展示表，不代表自动发现或自动合并所有历史运行。

验证：`pytest -q tests/test_unified_eval_contract.py tests/test_unified_legacy.py
tests/test_unified_eval_report.py`，6 项通过（仅有现有环境的
`asyncio_mode` 配置警告）。

## MiniAdapter 的真实 mini 接线（2026-11-15）

`MiniAdapter` 现在可以在调用方显式注入 `agent_factory` 和配置后，复用现有
`EvalRunner` 的单题路径，并把 `TaskResult` 包装为 `RunReceipt`。它只接受
`kind=mini` 的 manifest 项；repo pytest 和 SWE-bench 仍然没有注册真实 adapter。

CLI 可通过下面的显式入口启动真实 mini 评测：

```powershell
python scripts/unified_eval_report.py `
  --manifest path\to\mini-manifest.yaml `
  --real-mini `
  --config configs\baseline.yaml
```

这个模式会真实调用配置中的模型，当前没有被无模型验证自动启动；缺少 `--config`
会在构造 agent 之前直接报错。`--manifest --mock` 仍是无模型展示入口，repo/SWE-bench
不能通过统一命令隐式执行。

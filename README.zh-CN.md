# Coding Agent Harness

[English](README.md) · **中文**

一个可验证的代码修复 Agent 运行与评测框架：给定仓库 issue，Agent 在隔离容器中定位、修改并自测，交付候选补丁与验证记录；同一框架用独立隐藏测试判分，用同期对照实验判断一项改动是否真正提升修复效果。

## 组成

| 能力 | 做法 | 代码 |
| --- | --- | --- |
| 运行与恢复 | 工具的意图先落盘再执行；恢复时复用已提交的结果，非幂等操作的结果没记录就停下，不重试。真实子进程在四个崩溃窗口各崩一次验证（`os._exit(71)`） | [`runtime/session.py`](harness/src/runtime/session.py)、[`runtime/recovery.py`](harness/src/runtime/recovery.py)、[`tracing/store.py`](harness/src/tracing/store.py)、[`tests/test_recovery.py`](harness/tests/test_recovery.py) |
| 答案隔离 | 早期镜像的 `/opt/repo` 里带着 test patch，模型能读；改成干净源码镜像后，agent 只能改工作区，判分时另起一个容器，把候选 delta 和 test overlay 一起应用，再跑 FAIL_TO_PASS / PASS_TO_PASS | [`execution/baseline_image_backend.py`](harness/src/execution/baseline_image_backend.py)、[`eval/image_grader.py`](harness/src/eval/image_grader.py) |
| 代码检索 | 解决"该读哪些文件"：只读 base commit 的快照（裸 git 克隆，不检出），按函数/类做 AST 切块，BM25 与代码向量两路排序后用 RRF 融合，只返回路径、符号和行号 | [`src/retrieval/`](harness/src/retrieval/)、[CR-1 结果](harness/docs/retrieval/CR-1-RESULTS.zh-CN.md) |
| 工作区快照与验证 | 解决"改完的东西能不能信"：确认容器内没有仍在写入的进程（静止检查）后生成不可变快照；在同一镜像的新副本里应用候选，运行冻结的公开测试；失败时给模型有限次反馈（默认最多 2 次）。可选，默认关闭，目前只适配一个固定的 Sphinx 镜像 | [`src/workspace/`](harness/src/workspace/)、[`src/validation/`](harness/src/validation/) |
| 完成控制 | 解决"中途停下就什么都没留下"：无论正常结束、步数或预算用完、超时还是取消，都封存并导出候选补丁和验证记录；运行状态与验收结果分开报告 | [`runtime/completion.py`](harness/src/runtime/completion.py) |
| 可复算的评测 | A5/A6 分析脚本从运行清单按题汇总并计算 bootstrap 区间；候选自身失败保留在分母，基础设施异常的处理规则及事后偏差单独披露 | [`analyze_a5_main.py`](agent/scripts/analyze_a5_main.py)、[`analyze_a6_retry.py`](agent/scripts/analyze_a6_retry.py) |

## 结果

CR-2、A5、A6-R 用同一个 harness、同一个模型（deepseek-flash，每次运行最多 40 次模型调用），SWE-bench Verified 的题；CR-1 是离线检索，不调用模型。修复率差值的单位是百分点，括号里是 95% 区间，区间跨 0 写"未检出差异"。

| 实验 | 比较什么 | 规模 | 结果 |
| --- | --- | --- | --- |
| CR-1，离线代码检索 | hybrid（BM25 + 代码向量，RRF 融合）对 BM25，文件级定位 | 400 道测试题，冻结配置只评一次 | Hit@5 由 BM25 的 67.5% 到 hybrid 的 85.8%，MRR 0.520 → 0.684，两种 bootstrap 口径下区间都不含 0。这是定位指标，不是修复率；文件级上 hybrid 对单独 embedding 未检出差异（[CR-1 结果](harness/docs/retrieval/CR-1-RESULTS.zh-CN.md)第 1、2.1 节） |
| CR-2，检索接入 agent | N 不加检索；P 在首条消息末尾附上冻结的检索结果；T 多一个检索工具 `retrieve_code`；O 直接给出正确文件，作为上限参照 | 56 题 × 4 组 × 5 次 = 1120 次同期运行 | 修复率 P − N +2.9 [−3.2, 9.6]，T − N −0.7 [−5.4, 3.9]，O − N +2.1 [−5.4, 9.6]，都未检出差异。检索工具在原 system prompt 下 0/280 被调用（[CR-2 结果](harness/docs/retrieval/CR-2-RESULTS.zh-CN.md)结论、第 2、4 节） |
| A5，跨题记忆 | 注入同仓库、更早、已被验证解出的其他题的卡片（B2），对照不注入（B0）；与目标题相关性低（9/51 题指向 gold 文件） | 56 道题，每题每组 3 次 | 有记忆 − 无记忆 −1.8 [−8.3, +4.8]，p = 0.72 |
| A6-R，同题记忆 | 对失败过的题做第二次尝试：带上第一次尝试的规则化记录和"上次没通过"（R1），对照不带（R0）；相关性 100%，按构造保证 | 53 次失败的第一次尝试，22 道题，每组 159 次 | 有记忆 − 无记忆 −6.6 [−17.7, +3.8]，p = 0.29 |

A5、A6-R 的数字引自[最终报告](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md)第 1、6.1 节。

离线检索明显提升了定位；在这批题上，记忆和检索都没有转化为修复率提升。即使直接给出正确文件，修复率也未检出变化，所以下一步转向修复后的验证闭环。

## 先跑起来

不需要 Docker、SWE-bench 镜像或密钥。第 1、2 步都在干净的 HEAD 检出处实测过。

**1. 离线演示**（几秒，不调用模型）

```bash
cd agent && pip install -e ".[dev]"
python -m reposkill_agent upgrade-demo --output reports/upgrade/my-demo
```

`--output` 必须是不存在的目录。跑完得到公开 workspace、带状态和来源的 `memory.db`、manifest、`result.json` 和 `report.md`；输出里有 `Offline comparisons: 20; model calls=0`。演示里依赖或 API 签名变了旧经验被拒绝，无关文件变了仍然保留，证据不足返回 unknown。所有注入收据来自 fixture host，不声称真实模型收益。

**2. 公开测试**（两仓合计约 4 分钟）

```bash
# 在 agent/ 下
python -m pytest -q --deselect tests/test_a5_funding.py::test_funded_build_bundle_is_prepared_only_and_private_material_stays_outside_public \
  --deselect tests/test_a5_funding.py::test_funded_build_artifacts_are_hashed \
  --deselect tests/test_memory_cards_v5.py::test_generic_builder_matches_v5_bytes
# 354 passed, 10 skipped, 3 deselected

# 在 harness/ 下
cd ../harness
pip install -e ".[dev]"
python -m pytest -q --deselect tests/test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli \
  --deselect tests/test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete \
  --deselect tests/test_summary_pilot.py::test_prepare_frozen_data_and_window_expectations \
  --deselect tests/test_summary_pilot.py::test_trace_accounting_replay_and_private_grading
# 1249 passed, 44 skipped, 4 deselected
```

跳过的 54 项分三类：39 项需要真实 Linux 或 Docker（opt-in 的 Docker 执行测试、本机缓存的固定镜像、真实 Linux 进程组）；12 项依赖本机布局或数据（`../coding-harness` 同级检出、`runs/a5-main`、A5 私有记忆池、source pool、符号链接权限、`runs/sprint5-main-public`），在干净克隆里自动 skip；3 项是 agent 的 `test_a5_preparation.py`，需要 `pyarrow`，agent 的依赖里没有它，装上后会运行。harness 首次运行测试时会从 GitHub 下载一份 SWE-bench 规格文件，缓存在 `harness/runs/sprint5-specs/`。

**3. 依赖未公开材料的测试（7 项）**

这 7 项读取只在原开发机器上存在、没有入库的证据文件。它们不是代码回归，也没有被跳过：

| 测试 | 需要的文件 | 来源 |
| --- | --- | --- |
| harness `test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli` | A5 memory pool 的 `validated-memory-records.jsonl` | 私有记忆构建产物，`.jsonl` 不入库 |
| harness `test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete` | `full-repo-grader/attempt-02/flask-base.tar.gz` | 完整上游归档，体积过大未入库 |
| harness `test_summary_pilot.py` 两项 | `H5/deepseek-flash-attempt-01/seed/boltons/strutils.py` | R2 种子文件，仅本地保存 |
| agent `test_a5_funding.py` 两项 | `reports/upgrade/a5-funded-build-preparation-20260921-v12/*` | `reports/` 是运行产物，不入库 |
| agent `test_memory_cards_v5.py::test_generic_builder_matches_v5_bytes` | django-10914 的 handoff 字段 | 依赖 harness 侧输出，需两仓联调数据 |

把材料放到对应路径后，去掉 `--deselect` 即可全跑。另有 harness `test_public_target.py::test_task_is_complete_public_input_and_manifest_detects_material_change` 读取不入库的 `runs/sprint5-main-public/sphinx-doc__sphinx-8721.json`，文件不存在时自动跳过，已计入上面的跳过数。`harness/docs/INDEX.md` 第 4 节和 `agent/docs/INDEX.md` 里对这几项的描述与此一致。

**4. 需要本机镜像或缓存的演示**

下面两个演示在新克隆上跑不起来：

- `python scripts/run_workspace.py --scripted-demo --output-dir <新目录>`（在 `harness/` 下）：用脚本化的模型回复走一遍工作区快照、公开验证、有限次反馈和候选导出。需要本机已有的固定 Sphinx Docker 镜像（按 digest 固定，不会自动拉取）。
- `python scripts/cr_demo.py sympy__sympy-19040 --show-gold`（在 `harness/` 下）：对一道题给出 BM25、embedding、hybrid 三种排序和 gold 文件名次。需要 CR 的仓库缓存和向量缓存。

不运行也能看的已提交产物：

- [`harness/docs/retrieval/demo/`](harness/docs/retrieval/demo/) 下的两份示例：[预取（P 组）](harness/docs/retrieval/demo/cr2-p-sympy__sympy-19040.md)、[检索工具（CR-2e 第二轮）](harness/docs/retrieval/demo/cr2e-t2-sympy__sympy-19040.md)；
- [`harness/docs/roadmap/evidence/`](harness/docs/roadmap/evidence/) 下的小证据文件（P2 到 P4 各阶段的汇总与哈希）。

## 方法

- **答案隔离。** 早期的 dev 镜像里带着隐藏测试；改用干净源码重建镜像后，同样 14 道 dev 题的解出从 32/42 降到 18/42（[项目历程](harness/docs/PROJECT-HISTORY.zh-CN.md)，冲刺第 3、4 轮）。
- **包装实验。** 同样 9 份固定摘要，把包装写成"可能有误的历史证据，不是指令"后，全正确是 26/27，原来的包装是 7/27（[证据](harness/docs/upgrade/evidence/R2-real/wrapper-01/README.md)）。
- **预注册和冻结 tag。** 假设、唯一的主分析、种子和停止条件，在任何真实数据出现之前提交并打 tag（[A6-R 预注册](agent/docs/sprint/A6-R-PREREG.zh-CN.md)）。
- **冻结后只评一次。** CR-1 在 dev 集上选好 embedding 模型和融合参数后打 tag 冻结（`harness/cr1-freeze`），400 道测试题只评一次（[CR-1 冻结](harness/docs/retrieval/CR-1-FREEZE.md)）。
- **注入一致性检查。** 每次运行结束后，把模型实际收到的第一条消息和预期全文逐字比对：A5 168/168，A6-R 318/318（[最终报告](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md)第 3 节）。
- **同期交错。** 两组放进同一个打乱的队列（A6-R 的种子是 20261013），在同一时段跑（[A6-R 预注册](agent/docs/sprint/A6-R-PREREG.zh-CN.md)第 1 节）。

## 仓库地图

- `harness/`：**主执行链路**。Agent 主循环、工具、隔离执行（Docker 与宿主机判分）、会话恢复、代码检索、工作区快照、公开验证、完成控制，以及独立评测和运行脚本。A5/A6 和 CR-2 的每次运行都由这里发起、执行和评分。
- `agent/`：**记忆侧**。RepoSkill 记忆卡片与注入插件、有效性验证、实验分析脚本，以及历史多角色原型（`reposkill_agent/agents/`，用于更早的规划器实验，不参与 A5/A6 主链路）。

从哪里开始读：

- [两个记忆实验的最终报告](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md)
- [harness README](harness/README.zh-CN.md) · [harness 文档索引](harness/docs/INDEX.md)
- [agent 文档索引](agent/docs/INDEX.md)
- [项目历程](harness/docs/PROJECT-HISTORY.zh-CN.md)

## 复现

这两个目录原来是并列的两个仓库 `coding-harness` 和 `coding-agent`，开发和冻结都在这个布局下进行，代码按这个布局引用对方；部分配置和证据文件里还记着原机器上的绝对路径。这里没有改写这些路径，这样冻结 tag 指向的仍然是实际运行的代码。本仓库现名 `coding-agent-harness`，下面保留当时的仓库名和路径。tag 保留的是原来的提交，所以可以按原布局检出冻结状态：

```
git worktree add ../coding-harness harness/a6-retry-freeze
git worktree add ../coding-agent agent/a6-retry-freeze-r2
```

冻结 tag：

- A5：`harness/a5-main-freeze`、`harness/a5-main-freeze-r1` 到 `-r4`、`agent/a5-main-freeze`
- A6-R：`harness/a6-retry-freeze`、`agent/a6-retry-freeze`、`agent/a6-retry-freeze-r1`、`agent/a6-retry-freeze-r2`
- CR-1：`harness/cr1-freeze`
- CR-2：`harness/cr2-freeze-r3`（正式运行用的版本）；其余修订见 [CR-2-FREEZE](harness/docs/retrieval/CR-2-FREEZE.md)

这些标签指向公开快照提交，代码与原冻结提交逐字节相同；与文档里所写提交号的对应见[文档索引](harness/docs/INDEX.md)。

运行 agent 需要 Docker、SWE-bench 镜像和 DeepSeek API key。分析命令见 [A5 最终报告](agent/docs/A5-FINAL-REPORT.zh-CN.md)和[最终报告](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md)的附录。

## 关联项目

[code-GRPO](https://github.com/RichChen/code-GRPO) 是另一个独立仓库：不靠上下文注入，而是用 SFT 和自己实现的多轮 GRPO 训练 Qwen3-1.7B 修 bug。预注册的 1500 道留出集上，严格解出率是 13.9%（基座）→ 47.1%（SFT）→ 60.3%（GRPO），GRPO 相对 SFT 是 +13.2 个百分点（95% 区间 [11.7, 14.7]）。

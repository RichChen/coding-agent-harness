# A5-main 最终报告

日期：2026-09-29。数字来自 `docs/sprint/A5-MAIN-RESULTS.zh-CN.md`、harness 的 `docs/sprint/CHECKPOINT-6.md`、`docs/sprint/A5-MAIN-INVENTORY.md`、`docs/sprint/A5-CASE-DATA.md` 和 `docs/sprint/A5-MAIN-FREEZE.md`。设计叙述来自 harness `A5-MAIN-DESIGN.zh-CN.md` 和本仓库 `A5-MAIN-PREREG-ADDENDUM.zh-CN.md`。事后分析都标了「描述性，非预注册」。

## 1. 摘要

我们在 56 道 SWE-bench Verified 题上比较了两种条件：不注入记忆（B0），以及注入同仓库、更早、已被测试验证解出的题目的记忆卡片（B2）。每道题每种条件跑 3 次，模型是 deepseek-flash，每次最多 40 次模型调用，答案严格隔离，分析预先注册。B0 的解出率是 68.5%（harness CHECKPOINT-6：115/168）。B2 相对 B0 的平均配对差是 −1.8 个百分点（95% 区间 [−8.3, +4.8]，p = 0.72）；只看实际注入卡片的 51 道题，差值是 0.0（区间 [−6.5, +6.5]）。没有检出记忆带来的效应，并且可以排除大于约 5 个百分点的平均提升。案例分析显示，卡片很少指向正确的修改位置，agent 也几乎不按卡片行动。

精确数字见第 6 节，来源是 `A5-MAIN-RESULTS.zh-CN.md`。

## 2. 背景和问题

研究问题是：在同一个 harness、同一个模型和预算下，跨 issue 的同仓库「已验证修复」记忆能否提高解出率。设计文件把对照写成 B0 对 B2。

早期 RepoSkillAgent 在自建题集 medium_48 上测到 L5 比 L2 高 9.4 个百分点。那个题集按 support/holdout 成对设计，记忆和目标题在构造上相关。需要在真实基准上再检验一次。

原计划是 240 次矩阵（20 题 × B0–B3 × 3）。B0 和 B2 的对比只用到其中 120 次。功效主要取决于题目数：设计阶段用标准差 0.35、双侧 α=0.05、功效 80%，检出下限约为 2.8×0.35/√N。N=20 约 22 个百分点，N=60 约 13 个百分点。同样的钱用来加题目，比加变体更有价值。B1 和 B3 留到 B2 出现信号之后再做。见 harness `A5-MAIN-DESIGN.zh-CN.md` 第 2 节。

## 3. 系统

### 3.1 harness

agent 循环的工具是 `read_file`（带 offset 和行号）、`search_code`、`search_replace`、`run_command`。masking 保留路径。profile 是 `b0-v2`：最多 40 次模型调用，单次 1 元硬上限。

运行用干净镜像和 `BaselineImageBackend`。答案隔离有三道检查：源码 hash（含 sed 重放 override）、全文件系统扫描、运行前检查。`ImageGrader` 在同一镜像里应用 delta、覆盖 test patch，按 nodeid 收据判定。

运行器做 preflight，并行 3，并在这些情况停止：`infra_error` 超过停止线、`harness_tool_errors`、隔离失败、grader missing 经空改动重评仍对不上、故障警报、注入与预测不一致。进入分析的运行模型都是 deepseek-flash（harness CHECKPOINT-6）。profile 里 `thinking: disabled`，`max_model_calls` 是 40；单次花费硬上限 1 元（harness PROGRESS，第五轮第 7 步）。

### 3.2 记忆

卡片按规则从 B0 轨迹提取：改过的文件、读过的行、测试命令、诊断句作为 approach、环境坑。不调用模型。每道源题取编号最小的解出运行，标成 `validated_fix`；没有解出就取编号最小的非 `infra_error` 运行，标成 `failed_attempt`。

检索是 BM25，加上路径重合加分。`min_score` 1.0，`max_bytes` 4096。时间过滤在注入前做：同仓库、`created_at` 更早、不是本题、只注入 `validated_fix`。

v6 卡片 70 张，其中 `validated_fix` 50 张、`failed_attempt` 20 张。`cards_sha256` 是 `3416eb9d8f6119081330fb3b0a2d612b8fde4678d6ec1c73a7fc681cedfcfc70`（harness CHECKPOINT-6 与 `A5-MAIN-INVENTORY.md` 一致）。

下面是 `render_card` 对 `scikit-learn__scikit-learn-13439` 的渲染，原样贴出。issue 摘要按渲染规则在 180 字处截断。

````text
[historical attempt on a different issue in this repo; status=validated_fix — use as navigation hints, not as the answer]
repo: scikit-learn/scikit-learn @ a62775e99f2a
source: scikit-learn__scikit-learn-13439
approach: I'll start by locating the Pipeline class and its indexing support. Changed Pipeline.__len__.
files: sklearn/pipeline.py:29-148
tests: python -m pytest sklearn/tests/test_pipeline.py -q 2>&1
pitfalls: (none)
issue: Pipeline should implement __len__
#### Description

With the new indexing support `pipe[:len(pipe)]` raises an error.

#### Steps/Code to Reproduce

```python
from sklearn i
deps: sklearn/pipeline.py[__len__]
````

## 4. 实验设计

题目来自 SWE-bench Verified 的 test split，五个仓库：django、sympy、scikit-learn、sphinx-doc、pytest-dev。14 道 dev 题只做记忆源，不作为目标题。每个仓库按 `created_at` 等间隔抽取，最多 12 道，合计最多 60 道。环境或校准失败的题在调用任何模型之前剔除，只看环境，不看难度。冻结记录里合格题是 56 道：pytest 12、sympy 12、django 11、sphinx 11、scikit-learn 10。因此有 4 道在开跑前离开题目池。逐题原因在 harness 校准记录里，本报告不另算。原来的 20 道 pilot 不再单独 hold-out。

记忆源是 14 道 dev 的 `b0-v2` 运行，加上题目池自己的 B0。对目标题 t，只有同仓库、`created_at` 更早、不是 t 本身、并且 B0 被判为解出的卡片可以注入。先跑完全部 B0，再建卡，再跑全部 B2。每题每条件 3 次。两组都用干净镜像和答案隔离。

主要分析是 56 道题的配对 d_t。d_t 是 B2 解出率减去 B0 解出率。解出率是非 `infra_error` 运行里的解出次数除以有效运行数；三次都有效时就是 k/3。只用 r1–r3。r4 不进入主要分析，也不用来补缺失重复。有效运行少于 2 次的题剔除。检验是 10000 次双侧符号翻转，种子 20260926；95% 区间是按题 bootstrap。区间含 0 时写成「没有检出大于 X 个百分点的效应」，X 是检出下限。设计下限是 2.8×0.35/√N，数据下限把 0.35 换成 d_t 的样本标准差。

次要分析在 B2 开跑前改成：处理前预测会注入至少 1 张卡的题。依据是 `b2-predicted-injection.json`。实际注入只做核对，两组应该相同。

漂移对照臂是描述性的。B0 和 B2 不在同一时段跑，所以在 B2 队列里混入每题一次不带卡片的 `b0/r4`，种子 `random.Random(20260929)`。比较 r4 的 0/1 和 r1–r3 的解出率。区间不含 0 才在主结论后加「存在时间漂移」。用 r4 当同期 B0 的差值只报平均，不做检验。按 B0 档位列出 d_t 时必须写明：按 B0 结果分组会受回归均值影响，不可作为效应证据。

## 5. 执行和偏离记录

冻结 tag 和输入 sha 见附录 B。下面的日期是对应提交的日期。作废次数和花费来自 `A5-MAIN-INVENTORY.md`。

| 修订 | 日期 | 原因 | 改了什么 | 对已完成运行的影响 | 作废 | 作废花费 |
| --- | --- | --- | --- | --- | --- | --- |
| 修订 1 | 2026-09-28 | 构建和运行的 source-only 比对不一致，12 道题会在运行前失败 | sed 重放后的期望 sha；preflight；infra 停止线改为 ≥3 且超过 10% | 零花费的 sphinx-8593 槽位移到 `a5-main-halted`。有模型调用的 canary 当时仍算有效 | 0 | 0（halted 花费 0） |
| 修订 2 | 2026-09-28 | Python 3.6/3.8 镜像里工具用了 3.9/3.7 才有的 API | 容器内代码改到 3.6；工具冒烟；Docker 控制重试；`harness_tool_errors` 停止；并行改为 3 | 修订 2 之前的全部 B0 作废，包括修订 1 里仍有效的 canary | 25 次 | 13.682 元 |
| 修订 3 | 2026-09-29 | pool 的 user prompt 只有 `Public issue for <id>`，没有 issue 文本 | 从 SWE-bench Verified 原样写入公开 issue；静默回退改为报错；故障警报；候选改动引起的 missing 记普通失败 | 修订 3 之前的全部 B0 作废。dev 14 题的 prompt 不受影响 | 38 个目录（含 2 个 infra） | 24.055 元 |
| 修订 4 | 2026-09-29 | B0 已完成，需要交接 v2 和漂移臂 | 交接 schema v2；漂移队列 `--phase b2 --with-b0-drift` | 没有改运行结果，没有调用模型 | 0 | 0 |
| 补记 1 | 2026-09-29 | B2 开跑前的核对 | B2 故障警报；v6 sha 核对；预测注入表；注入不一致就停止 | 第一次 B2 之前提交 | 0 | 0 |
| 补记 2 | 2026-09-29 | django-13964 B2 r3 的 missing 来自候选改动，不是 grader 故障 | 空改动重评一致则记普通失败，`grader_candidate_error=execution` | 该槽位不重跑，`solved=false`。B0 的 168 次没有 missing | 0 | 0 |
| 补记 3 | 2026-09-29 | sphinx-7590 B2 r3 测试执行超时，空改动重评与校准一致 | 超时且空改动重评干净则记普通失败，`grader_candidate_error=execution_timeout` | 该槽位不重跑，`solved=false`。其余已完成运行没有 grader reason | 0 | 0 |

两批作废运行都没有进入分析：工具在 Python 3.6/3.8 上报错的 25 次，以及 prompt 里没有 issue 文本的 38 个目录。django-13964 B2 r3 和 sphinx-7590 B2 r3 按上面的规则记为普通失败，留在原槽位，没有重跑。

这些规则变化都发生在计算 B2 与 B0 的差异之前。分析脚本和预注册追加项在看到 B2 结果之后没有改。

进入分析的槽位是 B0 r1–r3 168/168、B2 r1–r3 168/168、B0 r4 56/56（`A5-MAIN-INVENTORY.md`）。隔离失败 0，`harness_tool_errors` 0，注入 168/168 与预测一致。晚于目标题的卡、目标题自己的卡、跨仓库的卡都是 0。

## 6. 结果

来源：`docs/sprint/A5-MAIN-RESULTS.zh-CN.md`。56 题全部进入主要分析。剔除：无。不满 3 次：无。

### 6.1 主要分析

- 题数：56
- 平均 d_t：−0.0179
- 双侧符号翻转 p：0.7175
- 95% 区间：−0.0833 到 0.0476
- 设计检测下限：13.1 个百分点（2.8×0.35/√N）
- 本次数据检测下限：9.0 个百分点（2.8×s/√N）
- 结论：没有检出大于 13.1 个百分点的效应

漂移区间包含 0，主结论没有加「存在时间漂移」。

### 6.2 次要分析

分组是处理前预测会注入至少 1 张卡。预测注入和实际注入的题一致，都是 51 题。

- 题数：51
- 平均 d_t：0.0000
- 双侧符号翻转 p：1.0000
- 95% 区间：−0.0654 到 0.0654
- 结论：没有检出大于 13.7 个百分点的效应

按仓库的平均 d_t（仍是这 51 题）：

- django/django：11 题，平均 d_t −0.1212
- pytest-dev/pytest：12 题，平均 d_t 0.0278
- scikit-learn/scikit-learn：9 题，平均 d_t 0.0370
- sphinx-doc/sphinx：10 题，平均 d_t 0.0000
- sympy/sympy：9 题，平均 d_t 0.0741

### 6.3 花费、调用和按仓库的描述

有效运行的平均（预注册里声明要报告的过程量）：

- 花费 CNY：B0 0.329，B2 0.340
- 模型调用次数：B0 23.619，B2 23.720
- 第一次编辑轮次：B0 10.215，B2 9.730

下面这张表是全部 56 题，标题按报告原文保留。描述性，非预注册。解出率是各题有效运行解出率的平均。花费和调用是有效运行的平均。没有做检验。

| 仓库 | 题数 | B0 解出率 | B2 解出率 | B0 花费 | B2 花费 | B0 调用 | B2 调用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| django/django | 11 | 0.7273 | 0.6061 | 0.311 | 0.294 | 22.788 | 21.485 |
| pytest-dev/pytest | 12 | 0.8056 | 0.8333 | 0.228 | 0.250 | 18.972 | 19.806 |
| scikit-learn/scikit-learn | 10 | 0.8667 | 0.8667 | 0.105 | 0.113 | 9.500 | 9.467 |
| sphinx-doc/sphinx | 11 | 0.6061 | 0.5758 | 0.524 | 0.556 | 34.879 | 36.000 |
| sympy/sympy | 12 | 0.4444 | 0.4722 | 0.456 | 0.460 | 30.472 | 30.306 |

### 6.4 漂移对照（描述性，非预注册）

- 题数：56
- 平均差（r4 减 r1–r3）：−0.0238，也就是 −2.4 个百分点
- 95% 区间：−0.0833 到 0.0298
- 用 r4 作同期 B0：平均（B2 的 k/3 减 r4）0.0060，也就是 +0.6 个百分点

不做显著性检验。

### 6.5 解读

主要分析的区间是 [−8.3, +4.8] 个百分点。按预注册写法，结论是「没有检出大于 13.1 个百分点的效应」；用本次数据的离散程度估算的检出下限是 9.0 个百分点。区间上限 +4.8 表示平均提升大于约 5 个百分点的可能性可以排除。点估计的轻微负值和漂移对照一致：同一时段混跑的 B0 r4 比凌晨的 r1–r3 低 2.4 个百分点，而 B2 与同期 r4 的差只有 +0.6 个百分点。

## 7. 案例分析

全部为描述性，非预注册。数字以 harness `A5-CASE-DATA.md` 第 1–4 节为准。该文件文首写「引用第 7 节」，正文只有第 1–4 节；这里按正文节号引用。

### 7.1 卡片很少指向正确的修改位置

51 道注入卡片的题里，只有 9 道的卡片（去掉测试文件后）提到了 gold 修改的文件。共享文件里有 `django/db/models/fields/__init__.py`、`sphinx/util/inspect.py`、`sphinx/ext/autodoc/typehints.py`，也有 `django/forms/fields.py`、`sympy/geometry/point.py`、`sklearn/mixture/base.py`、`src/_pytest/capture.py`、`src/_pytest/unittest.py`。明细在案例数据第 1 节，本报告不改这张表。

这 9 道的 B0 全部是 3/3 或 0/3。其中 8 道 d_t = 0，只有 sphinx-9673 从 3/3 掉到 1/3。重合组平均 d_t −0.074（n=9），不重合组 +0.016（n=42）。同一仓库的 SWE-bench 题跨越多年、分布在不同子系统，更早题目的修法很少能直接迁移。

### 7.2 agent 几乎不按卡片行动

153 次注入卡片的 B2 运行里，只有 5 次打开过「卡片提到、不在 gold 修改范围内、并且该题 B0 三次都没打开过」的文件，0 次把最终修改落在这类文件上。5 次是：django-13964 的 r2 和 r3（`fields/__init__.py`），sphinx-9258 的 r1 和 r3（`typehints.py`、`inspect.py`），sphinx-9673 的 r1（`sphinx/ext/autodoc/__init__.py`）。

同时，B0 和 B2 的平均调用次数（23.6 对 23.7）、首次编辑轮次（10.2 对 9.7）几乎一样（第 6.3 节的 23.619/23.720 和 10.215/9.730）。agent 的行为主要由 issue 决定。

### 7.3 三个具体案例

sphinx-9673（B0 3/3，B2 1/3）。gold 在 `sphinx/ext/autodoc/typehints.py`，hunk 上下文是 `augment_descriptions_with_types`。卡片 8459 的依赖符号是 `record_typehints`，approach 是轨迹中途叙述。6 次运行都改了 `augment_descriptions_with_types`。B0 r2 还改了 `modify_field_list`。没有一次改 `record_typehints`。新增行数：B0 三次分别 1、2、2 行，全部解出；B2 三次分别 1、2、1 行，只有 r2 解出。B2 的两次失败是修复细节写法不同，不是被卡片引向 `record_typehints`。

sphinx-9258（B0 1/3，B2 3/3）。gold 在 `sphinx/domains/python.py`。注入的卡片来自 8459（typehints）、8120（locale）、7440（std glossary），和 gold 文件没有交集。B2 r1、r3 先打开了 `typehints.py` 和 `inspect.py`，之后在 `python.py` 里解出。提升和卡片内容无关。

django-13964（B0 2/3，B2 0/3）。gold 在 `django/db/models/base.py`。卡片来自 11211（contenttypes UUID）、13109（ForeignKey.validate）、11039（sqlmigrate），都无关。B2 r2、r3 打开过 `fields/__init__.py`，修改落在 `related_descriptors.py`，和 B0 解出的那次是同一区域。B2 r3 删掉外键赋值分支，按 missing_regrade 记为普通失败。B0 r2 是 delta_lost（自己撤回）。这道题的单次成功率大约一半，连续 3 次失败的概率约 1/8。

### 7.4 随机性的量级

5 道没有注入卡片的题，B2 拿到的输入和 B0 相同，平均 d_t 是 −0.200。其中 sklearn-9288、sphinx-7440、sympy-13757 从 3/3 变成 2/3；sympy-11618 和 sympy-14248 的 d_t 是 0。单题 ±1/3 的变化不能当作效应证据。

### 7.5 提升空间

B0 里 34 道 3/3、13 道 0/3，1/3 与 2/3 合计 9 道处在中间（harness CHECKPOINT-6 的分档：2/3 为 4 道，1/3 为 5 道；RESULTS 的中间档也是 9 题）。按档位看 d_t：0/3 档 0.0513，中间档 0.1111，3/3 档 −0.0784。按 B0 结果分组会受回归均值影响，不可作为效应证据。

## 8. 讨论

### 8.1 为什么这种记忆在这里没有检出效应

1. 相关性低。跨 issue 的同仓库修复很少共享修改位置（7.1）。
2. 记的是具体修法，不是可迁移的策略。approach 字段是 agent 中途的诊断自述，换一道题基本用不上。
3. 瓶颈更多在修复细节，不在定位。9673 的 6 次运行都找对了函数，成败只在一两行写法。这个判断目前来自少数案例，没有对全部失败运行统计。
4. 被动注入，模型不理会（7.2）。
5. 提升空间有限（7.5）。文献里报告的记忆收益多是个位数，低于本设计的检出下限。

### 8.2 和主流 agent 记忆设计的对比

| 类型 | 代表 | 记什么 | 适用条件 |
| --- | --- | --- | --- |
| 项目规则 | CLAUDE.md、AGENTS.md、Cursor rules | 仓库约定、构建和测试命令、已知坑 | 每次都加载，内容少而稳定 |
| 用户或会话记忆 | ChatGPT memory、Mem0、MemGPT/Letta | 偏好、长对话事实 | 个性化和长对话 |
| 同任务反思 | Reflexion | 同一题上次为什么失败 | 重试同一题，有失败信号 |
| 跨任务经验和策略 | ExpeL、Agent Workflow Memory、ReasoningBank、Dynamic Cheatsheet | LLM 从成功和失败轨迹里提炼的抽象策略或流程 | 任务同质、流程可复用 |
| 技能库 | Voyager、Claude Skills | 可执行的代码或流程 | 同一种操作反复出现 |

本实验的卡片属于第四类，但是规则提取、跨异质任务、被动注入的弱化版本。主流做法有效的前提基本是两条之一：记忆和当前任务高度相关（同一任务、同一网站、同一套 API），或者记的是稳定的项目知识。本场景两条都不满足。

### 8.3 和 RepoSkillAgent 早期 +9.4 个百分点的关系

早期结果来自自建题集。support 和 holdout 按类别成对构造，记忆在设计上就和目标题相关。本实验按时间顺序取真实 SWE-bench 题，没有人为保证相关性。两者并不矛盾：记忆的作用取决于相关性，而真实基准里跨 issue 的相关性很低。

## 9. 局限

- 只用了一个模型（deepseek-flash，关闭 thinking），调用预算固定为 40 次。
- 卡片由规则提取，没有做 LLM 提炼；检索只用 BM25。approach 可能是 agent 中途的自述，而不是最终修法。
- 5 个 Python 仓库，56 道题；每题 3 次，单题估计的噪声大。
- B0 和 B2 不在同一时段运行，靠漂移对照臂描述，不能完全消除时段影响。
- 「卡片是否指向正确文件」用 gold 修改文件作代理，是粗略指标。
- 事后案例分析不是预注册的，只用来解释机制，不作为效应证据。

## 10. 后续方向

本节是以后如果再做实验时的方向。本项目不做。

主推同题重试记忆（Reflexion 式）。B0 里 53 次失败运行可以直接作为第一次尝试。R0 从头再做一次，R1 带上第一次尝试的摘要。失败信号只给「上次提交没有通过」这 1 比特，事先声明，不透露测试内容。约 212 次运行，约 70 元。这是唯一保证记忆和当前任务是同一题的设定，用来回答相关性有保证时记忆有没有用。

备选是项目规则记忆（测试命令、环境坑、约定）。主要指标改成调用次数、花费、首次编辑轮次，样本量要求更低。

再一个备选是 LLM 提炼策略（ReasoningBank 式），配合更弱的模型、更紧的预算或更同质的任务序列。成本最高，预期信号最弱。

任何后续实验都是新的预注册实验，不能复用本次数据调参。

## 11. 结论

实验链路按设计完成：隔离、确定性注入、预注册分析、漂移对照都到位，注入 168 次全部与预测一致，没有答案泄漏。结果是一个有信息量的零效应：对 deepseek-flash 来说，同仓库、更早、按规则提取的已验证修复卡片，在 SWE-bench Verified 上没有带来可检出的解出率提升，可以排除大于约 5 个百分点的平均效应。被否定的是这种具体的记忆设计，不是所有形式的记忆。

按预注册，主结论是：没有检出大于 13.1 个百分点的效应。

## 附录 A. 复现

环境：Docker Desktop（冻结记录：8 核，7.7 GiB）；harness 用仓库里的 Python 环境跑 `scripts/run_a5_main.py`；本仓库建卡和分析不调用模型。profile 是 `configs/sprint/b0-v2.yaml`。

tag（`A5-MAIN-INVENTORY.md`）：

| 仓库 | tag | commit |
| --- | --- | --- |
| coding-harness | `a5-main-freeze` | `02c8671d3afb4be51533f00fe12b8074ecefbb56` |
| coding-harness | `a5-main-freeze-r1` | `fb26dbd8def7055cb2602dca4a737392f6e4b8c5` |
| coding-harness | `a5-main-freeze-r2` | `2f28232a0d35cad254cc1222cb2d35a94d84cfcc` |
| coding-harness | `a5-main-freeze-r3` | `1707d230729d4faa450ea89d357eb01e197280e8` |
| coding-harness | `a5-main-freeze-r4` | `074219cf971e3e3389eff38f80804a345cdd3785` |
| coding-agent | `a5-main-freeze` | `a22e4ca0a3994012c167a3e02e06498731c24a18` |

进入分析的运行对应修订 3 之后的代码，B2 和 r4 在修订 4 及其补记之后。卡片文件的 sha 在附录 B，不要求 tag `a5-main-freeze` 上已经有 v6。

命令顺序，摘自两边 PROGRESS，不是一键重跑 392 次的脚本：

1. preflight：harness `run_a5_main.py --preflight`。修订 3 的记录是 56/56 通过，工具冒烟 56/56，prompt 56/56。
2. B0：`scripts/run_a5_main.py` 先跑合格题的 B0 三次（harness PROGRESS 第五轮第 7 步；修订 3 之后的正式命令是 `--phase b0`）。完成后写出交接文件。
3. 建卡：本仓库 `scripts/build_memory_cards.py --handoff --out`，两份交接是 harness 的 `docs/sprint/memory-source-runs.json` 和 `docs/sprint/main-b0-runs.json`（v2）。产物 `benchmarks/a5/memory-cards-v6.jsonl`。
4. 预测注入：harness `scripts/sprint6_predict_injection.py`，输出 `docs/sprint/b2-predicted-injection.json`。
5. B2 加漂移对照：`--phase b2 --with-b0-drift`（harness PROGRESS，6D 第 A 段）。
6. 分析：本仓库 `scripts/analyze_a5_main.py` 读取 `runs/a5-main/` 和预测注入表，写出 `docs/sprint/A5-MAIN-RESULTS.zh-CN.md`。

输入文件用附录 B 的 sha 核对。作废目录在 `runs/a5-main-void/`，不进入分析。

## 附录 B. 数据清单和 sha256

来源：harness `docs/sprint/A5-MAIN-INVENTORY.md`。清单文件的 `handoff_sha256` 是 `e277cfefaeb30948f202888de999f0f6335ea581b56bd256fceaefea0d5be9ec`。

| 文件 | sha256 |
| --- | --- |
| `configs/sprint/main-targets.yaml` | `3556a5fdfac8b7d4ac2b631a31e2a7951312523aff605014f3b32eaf184738a4` |
| `docs/sprint/main-accepted-ids.txt` | `d71cb17adb44fd06f71bf8b0f8babcac12ea839354f1dcec30aae2ffa719b730` |
| `configs/sprint/b0-v2.yaml` | `ddf53b2db293bf03c7a099a43a34ea96aee5ecca365d9b8b4f11d66afb344e60` |
| `docs/sprint/memory-source-runs.json` | `984826707e8f69ad9b900134bd244c6056f2d8860f3036a3606bfc60b4bcb450` |
| `docs/sprint/main-b0-runs.json`（v2） | `fe0f47b8df67730a0335d7109631dc09bc4d6b5d19a601acfec0963f5dcb1f8c` |
| `docs/sprint/b2-predicted-injection.json` | `5d858eb4f3ab368fc655b310168e2c77de311ddfee4f77c0928e4d08ba79e3e5` |
| `benchmarks/a5/memory-cards-v6.jsonl` | `3416eb9d8f6119081330fb3b0a2d612b8fde4678d6ec1c73a7fc681cedfcfc70` |

槽位：B0 r1–r3 168/168（infra 退役 2），B2 r1–r3 168/168（infra 退役 3），B0 r4 56/56（infra 退役 2）。每个退役槽位只重跑一次。

## 附录 C. 花费汇总

来源：`A5-MAIN-INVENTORY.md`。单位：元。

| 组 | 合计 |
| --- | --- |
| B0 r1–r3 | 55.774 |
| B2 r1–r3 | 57.790 |
| B0 r4 | 18.240 |
| `runs/a5-main/` 有效实验 | 131.804 |
| 作废 `r1-tool-py36` | 13.682 |
| 作废 `r2-no-issue` | 24.055 |
| 作废合计 | 37.737 |
| 停止目录 | 0 |
| 第 6 轮合计 | 169.541 |
| 第 1–5 轮（从 PROGRESS 抄录） | 43.422 |
| 项目模型花费合计 | 212.963 |

未计入 `runs/a5-main/` 的 canary 3.739 元已经含在作废 37.737 元里。修订 3 canary 的 1.733 元已经含在 B0 r1–r3 的 55.774 元里。不重复相加。`b2-stats.json` 的 `spent_total_cny` 也是 131.804。

## 附录 D. 逐题表

从 `docs/sprint/A5-MAIN-RESULTS.zh-CN.md` 复制。

| 题 | 仓库 | B0 | B2 | d_t | B2 注入 |
|---|---|---|---|---|---|
| django__django-11211 | django/django | 3/3 | 3/3 | 0.000 | True |
| django__django-11749 | django/django | 3/3 | 2/3 | -0.333 | True |
| django__django-12276 | django/django | 3/3 | 3/3 | 0.000 | True |
| django__django-13109 | django/django | 3/3 | 3/3 | 0.000 | True |
| django__django-13512 | django/django | 0/3 | 0/3 | 0.000 | True |
| django__django-13964 | django/django | 2/3 | 0/3 | -0.667 | True |
| django__django-14434 | django/django | 3/3 | 3/3 | 0.000 | True |
| django__django-15022 | django/django | 1/3 | 0/3 | -0.333 | True |
| django__django-15499 | django/django | 3/3 | 3/3 | 0.000 | True |
| django__django-15973 | django/django | 0/3 | 0/3 | 0.000 | True |
| django__django-16527 | django/django | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-10081 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-5262 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-5631 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-5809 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-5840 | pytest-dev/pytest | 0/3 | 0/3 | 0.000 | True |
| pytest-dev__pytest-6202 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-7205 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-7324 | pytest-dev/pytest | 0/3 | 0/3 | 0.000 | True |
| pytest-dev__pytest-7490 | pytest-dev/pytest | 2/3 | 3/3 | 0.333 | True |
| pytest-dev__pytest-7521 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-7982 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| pytest-dev__pytest-8399 | pytest-dev/pytest | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-10908 | scikit-learn/scikit-learn | 2/3 | 3/3 | 0.333 | True |
| scikit-learn__scikit-learn-12585 | scikit-learn/scikit-learn | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-12973 | scikit-learn/scikit-learn | 0/3 | 0/3 | 0.000 | True |
| scikit-learn__scikit-learn-13142 | scikit-learn/scikit-learn | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-13439 | scikit-learn/scikit-learn | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-14053 | scikit-learn/scikit-learn | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-14496 | scikit-learn/scikit-learn | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-14710 | scikit-learn/scikit-learn | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-15100 | scikit-learn/scikit-learn | 3/3 | 3/3 | 0.000 | True |
| scikit-learn__scikit-learn-9288 | scikit-learn/scikit-learn | 3/3 | 2/3 | -0.333 | False |
| sphinx-doc__sphinx-10323 | sphinx-doc/sphinx | 3/3 | 3/3 | 0.000 | True |
| sphinx-doc__sphinx-10614 | sphinx-doc/sphinx | 0/3 | 0/3 | 0.000 | True |
| sphinx-doc__sphinx-7440 | sphinx-doc/sphinx | 3/3 | 2/3 | -0.333 | False |
| sphinx-doc__sphinx-7590 | sphinx-doc/sphinx | 0/3 | 0/3 | 0.000 | True |
| sphinx-doc__sphinx-7910 | sphinx-doc/sphinx | 3/3 | 3/3 | 0.000 | True |
| sphinx-doc__sphinx-8120 | sphinx-doc/sphinx | 1/3 | 3/3 | 0.667 | True |
| sphinx-doc__sphinx-8459 | sphinx-doc/sphinx | 3/3 | 2/3 | -0.333 | True |
| sphinx-doc__sphinx-8593 | sphinx-doc/sphinx | 3/3 | 2/3 | -0.333 | True |
| sphinx-doc__sphinx-9258 | sphinx-doc/sphinx | 1/3 | 3/3 | 0.667 | True |
| sphinx-doc__sphinx-9461 | sphinx-doc/sphinx | 0/3 | 0/3 | 0.000 | True |
| sphinx-doc__sphinx-9673 | sphinx-doc/sphinx | 3/3 | 1/3 | -0.667 | True |
| sympy__sympy-11618 | sympy/sympy | 2/3 | 2/3 | 0.000 | False |
| sympy__sympy-13091 | sympy/sympy | 0/3 | 1/3 | 0.333 | True |
| sympy__sympy-13757 | sympy/sympy | 3/3 | 2/3 | -0.333 | False |
| sympy__sympy-14248 | sympy/sympy | 0/3 | 0/3 | 0.000 | False |
| sympy__sympy-15599 | sympy/sympy | 0/3 | 1/3 | 0.333 | True |
| sympy__sympy-16766 | sympy/sympy | 3/3 | 3/3 | 0.000 | True |
| sympy__sympy-17655 | sympy/sympy | 3/3 | 3/3 | 0.000 | True |
| sympy__sympy-19040 | sympy/sympy | 1/3 | 0/3 | -0.333 | True |
| sympy__sympy-20428 | sympy/sympy | 0/3 | 0/3 | 0.000 | True |
| sympy__sympy-21596 | sympy/sympy | 0/3 | 0/3 | 0.000 | True |
| sympy__sympy-22714 | sympy/sympy | 3/3 | 3/3 | 0.000 | True |
| sympy__sympy-23950 | sympy/sympy | 1/3 | 2/3 | 0.333 | True |


## 附录 E. 案例数据

harness `docs/sprint/A5-CASE-DATA.md`。核对行：`核对：c85886c，table/follow 与正文一致。` 不在这里重贴全文。

## 附录 F. 项目历程

harness `docs/PROJECT-HISTORY.zh-CN.md`。本仓库的短指针是 `docs/PROJECT-HISTORY.zh-CN.md`。

## 勘误（2026-11）

日期：2026-11-06。上文原文不改，本节补充 2026-11 回看时核对出的事实。数字全部来自 harness `scripts/audit/errata_2026_11/e1_a5_card_visibility.py`，输出是同目录的 `e1_a5_card_visibility.json`。脚本只读 `runs/a5-main/` 的事件日志，不调用模型。

### B2 的卡片只在第一次模型请求里可见

**原来的说法。** 第 3.2 节和第 5 节把 B2 写成"注入卡片"，第 5 节和第 11 节写"注入 168/168 与预测一致"。报告没有说卡片在一次运行里出现几次，读起来像是模型每一步都看得到卡片。

**实际情况。**

- `MemoryCardPlugin.decide`（tag `a5-main-freeze` 上的 `reposkill_agent/memory/card_plugin.py` L127–133，当前代码相同）只在插件第一次被调用时给出卡片；之后状态里的 `calls` 不为 0，返回空候选。这个写法沿用自 `reposkill_agent/memory/public_runtime_view.py` 的 `A5PublicMemoryPlugin`（L121–125）。A5 的设计文件和预注册都没有规定只注入一次。
- harness 每一步都从原始历史重新构建一次性的模型视图（harness A5 冻结 tag `a5-main-freeze` 到 `a5-main-freeze-r4` 上 `src/runtime/loop.py` 的 `_model_view`，L282），插件给出的内容不写回历史。所以卡片只出现在第 0 步的视图里，从第 1 步起模型就看不到了。
- 复算结果（`context.prepared` 事件；超过 16KB 的载荷到同一运行的 `session/artifacts/<sha256>` 读取）：
  - B2 168 次运行，实际注入的卡片集合和预测表 168/168 一致。
  - 有注入的 153 次（51 道题），卡片全部只在第 0 步出现，第 1 步的视图里都没有卡片文本。没有注入的 15 次和预测一致。
  - 第 0 步的 assistant 回复提到卡片的：0/153。用的是关键词匹配，正则见输出里的 `inputs.mention_regex`；sphinx-9258 回复里的 "type hint" 是题目本身的用语，不算。
  - 这 51 道题上，B0 和 B2 各 153 次运行里碰到卡片所列文件的运行数：按文件名匹配，第 0 步 B0 22、B2 26，整次运行 B0 65、B2 70；按完整相对路径匹配，第 0 步 B0 19、B2 21，整次运行 B0 42、B2 40。两种口径下差别都很小，方向也不一致。

**当时为什么没发现。** 注入一致性检查 D7（harness `scripts/run_a5_main.py` 的 `injection_mismatch`）读取一次运行的全部 `context.prepared`，但只比较去重后的卡片 id 集合是否等于预测，不看卡片出现在哪几步、出现几次。只在第 0 步注入和每一步都注入，在它看来没有区别。

**对结论的影响。**

- B0 和 B2 的对比本身仍然成立。两组的差别就是第一次请求里有没有卡片，运行、打分和分析都按预注册执行。
- 处理条件比报告写的弱。第 1 节和第 11 节的结论应读作：只在第一次模型请求里展示一次卡片，没有检出效应。它回答不了"整次运行都看得到卡片"时会怎样。
- 第 7.2 节"agent 几乎不按卡片行动"和第 8.1 节第 4 条"被动注入，模型不理会"，都要放在这个条件下理解：从第 1 步起，卡片已经不在模型的上下文里。
- 卡片的相关性本来就低，第 7.1 节已经写明：51 道注入题里只有 9 道的卡片（去掉测试文件后）提到了 gold 修改的文件，而且只到文件级。

### A5 的 2 次 `model_parse_error`

正文没有单独讨论这 2 次（scikit-learn-9288 B2 r2，花费 0.923 元；sympy-15599 B0 r3，花费 0.937 元）。它们实际是单次运行 1 元上限拒绝了下一次调用的预留，不是模型输出解析失败，原因和 A6-R 的 11 次相同，见 `sprint/A6-R-RESULTS.zh-CN.md` 的勘误。两组各 1 次，不影响上文的结论。

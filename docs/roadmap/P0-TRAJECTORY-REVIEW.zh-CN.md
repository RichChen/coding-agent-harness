# P0 真实轨迹诊断：B 能介入什么

日期：2026-10-30。代码阅读基点：`76388a59116c415267f8923a14c28c1104a89ef6`。只读诊断；未运行模型、Docker、旧命令或独立评分。唯一新增仓库文件为本报告。

## 1. 可供 P1 使用的结论

B 的近期价值有真实轨迹支撑，主要是**让候选、公开检查和交付状态有可核查的关系**，尚不能据此主张修复率改善。本样本未确认“最终使用已过期通过记录”或“最后明确行为失败被直接忽略”的案例。可见失败后的修改、重跑，在部分旧轨迹里已经由模型完成。

18 条中，3 条留下非空候选却没有执行测试或行为检查，另 1 条只执行 fixture 展示探针、没有运行既有回归测试；这 4 条均以预算耗尽或上下文溢出结束。5 条正常 completed 的运行都有非空测试通过正文。**所以不能把样本描述成“模型经常正常结束而不测试”**，也不能假设增加一次完成反馈就能继续获得模型调用预算。

8 条有非空测试通过正文（包括最后导出失败的 CR-04）；没有一条提供足以证明“该检查针对最终完整候选”的运行时版本记录。有检查的候选，其版本绑定未知；没有检查或没有可交付候选的运行，不适用“检查已绑定”。这里的未知是证据限制，不是 18 次验证错误。测试命令、环境镜像、最终 delta 各有记录，不等于三者已在执行时绑定。

建议 P1 保留 B 的候选身份、冻结公开检查、独立验证工作区、准确状态和总预算规则；把“失败反馈提高修复率”降为待检验假设。优先做 Sphinx 的既有 pytest 检查；scikit-learn 作为导出/副作用问题核清后的第二场景。不要借 P0 扩展到自动生成测试或环境修复。

## 2. 数据、抽样规则和限制

### 2.1 先定规则，再读轨迹

先阅读 AGENTS.md、纲领与计划第 5 节、第 6 节 P0，并检查清单字段和既有分析脚本。随后在读取样本的命令、编辑和测试正文之前，按以下规则固定 18 条。没有按是否支持 B 更换样本，也没有补选“更好看的”案例。

- 总体仅限三份现有最终运行清单：A5 的 `main-b0-runs.json`（168 条 B0）、A6 的 `a6-retry-runs.json`（318 条）、CR 的 `cr2-runs.json`（1120 条）。不纳入 B2、退役 infra、作废运行或 CR-2e；本次范围不代表整个 A5/A6/CR 的全量审计。
- 各清单按 `run_dir` 的 Unicode 字典序升序排列。
- A5：按 `status` 分层，分别取 pass、fail、partial、delta_lost 的第 1 条，再取 fail、partial 的第 2 条，共 6 条。A5 清单没有 stop_reason；读取样本 summary 后补记，不为补齐结束原因换样本。
- A6：按 stop_reason 取 completed、max_model_calls、model_parse_error、context_overflow 的第 1 条，再取 completed 且 solved=true 的第 1 条、max_model_calls 且 status=delta_lost 的第 1 条，共 6 条。
- CR：按 stop_reason 取 completed、max_model_calls、step_deadline、candidate_snapshot_failed、model_parse_error、context_overflow 的第 1 条，共 6 条。
- 不可读或缺失的已选样本仍保留，并标记未知；不改 ACL。此次 18 份 events 与 summary 均可读，17 份最终 delta 可读，CR-04 没有 delta。需要解开的 21 个事件 payload 产物引用全部可读，且 SHA-256 匹配。

这是一种有意覆盖结束原因的机械分层抽样，**不是随机抽样**。字典序导致 Django 占 14 条，Sphinx 2 条，scikit-learn、pytest 各 1 条；A5/A6 还有同题重复，不能当成相互独立的 18 道题。4 条 solved=true 仅作成功参照，来源是既有清单/summary 的最终状态；不引用其私有评分内容。

### 2.2 清单指纹与阅读边界

| 清单 | 原始字节 SHA-256 |
| --- | --- |
| [docs/sprint/main-b0-runs.json](D:/crq/src/coding-harness/docs/sprint/main-b0-runs.json) | `2980e83bb0ef47ef0d74c6e1c7e3a9a343d9384eb9ef30960f7372b078f170c0` |
| [docs/sprint/a6-retry-runs.json](D:/crq/src/coding-harness/docs/sprint/a6-retry-runs.json) | `d46b6bc011c5b21a35407a0aa4aa3461bab15ae92ae875e3da1f16d42811aff3` |
| [docs/retrieval/cr2-runs.json](D:/crq/src/coding-harness/docs/retrieval/cr2-runs.json) | `bb6171cfe2cdf862135c4c41a22be2821157acc63c7c8c4899e8f961a1d7cabc` |

仅沿选定 run_dir 读取 `summary.json`、`events.jsonl`、`candidate-delta.json`，以及事件直接引用的 `session/artifacts/<sha256>`。候选只核对字段、文件路径和是否非空，不将候选正文搬入报告。未读取 `grader-result.json`、私有任务、隐藏测试、test patch 或 gold patch；没有将这些内容用于反馈设计。

首次文件名检索范围过宽，触及旧 `docs/upgrade/evidence` 的若干 session 并收到拒绝访问；随即收窄到清单定位读取，没有枚举其内容、改权限或以它们为样本。选定样本中的 session/artifacts 可以直接读取。一次最初按 run_dir/artifacts 寻址得到文件不存在，按现有 session schema 改为 session/artifacts 后解析成功，不能把该寻址错误算成运行证据缺失。

schema 依据：[src/runtime/session.py](D:/crq/src/coding-harness/src/runtime/session.py:210) 的 tool.intent/result、[src/tracing/artifacts.py](D:/crq/src/coding-harness/src/tracing/artifacts.py:42) 的内容寻址产物；分析脚本参考 [scripts/analyze_cr2.py](D:/crq/src/coding-harness/scripts/analyze_cr2.py) 和 [scripts/analyze_cr2e.py](D:/crq/src/coding-harness/scripts/analyze_cr2e.py)。只阅读其事件/路径解释逻辑，没有执行分析入口或加载 gold 数据。源代码中的 `execution.snapshot.files={}` 是旧日志事实，不能冒充对 Docker 完整候选的检查快照。

### 2.3 判定口径

- **U：未执行足够的开发检查。** 区分没有执行任何检查、只生成复现文件、只跑展示/打印探针，以及实际执行非空既有测试。grep/search 的 exit 0 不是测试。
- **S：通过后继续修改。** 只有明确的事件顺序才能判断曾经失效；还要检查后续是否重跑。没有中间候选哈希时，最终版本绑定仍为未知。
- **F：可见行为失败。** 要有执行正文中的断言/公开行为失败，并检查后续修改及复测。导入、收集、测试名拼错、工具错误不直接归为目标行为失败。
- **E：执行/环境/交付错误。** 导入路径、工具缺失、快照超限分别记载；测试副作用造成超限的可能性不等于已证明环境本身坏了。
- **?：证据不足。** shell 管道可能掩盖上游状态，tail/grep 可能丢失结果正文；执行版本、检查身份或环境身份不完整时不认证通过。置信度针对“日志里看到了什么”，不针对反事实修复收益。

## 3. 固定样本清单

每个运行链接指向本地目录；S、D 分别链接 summary 和最终 delta。事件编号 `#n` 为 JSONL 的 `seq=n`，本样本中也等于文件的 1-based 行号；下节提供可点击的精确位置。status/solved 是既有独立结果字段，不是本报告推导的开发验证结果。

| ID | 仓库与运行路径 / 证据 | stop_reason；status；solved | 诊断 | 置信度 |
| --- | --- | --- | --- | --- |
| A5-01 | django/django<br>[runs/a5-main/django__django-11211/b0/r1](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1)<br>[S](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/candidate-delta.json) | `max_model_calls`；`pass`；`true` | 测试正文通过；中途通过后改测试并重跑；最终版本绑定未知 | 高（事件顺序）；未知（绑定） |
| A5-02 | django/django<br>[runs/a5-main/django__django-15022/b0/r3](D:/crq/src/coding-harness/runs/a5-main/django__django-15022/b0/r3)<br>[S](D:/crq/src/coding-harness/runs/a5-main/django__django-15022/b0/r3/summary.json) / [D](D:/crq/src/coding-harness/runs/a5-main/django__django-15022/b0/r3/candidate-delta.json) | `max_model_calls`；`fail`；`false` | 非空候选；未执行测试；反复编辑至预算耗尽 | 高 |
| A5-03 | django/django<br>[runs/a5-main/django__django-13512/b0/r1](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1)<br>[S](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1/candidate-delta.json) | `completed`；`partial`；`false` | 测试正文通过；目标覆盖不足的可能性；绑定未知 | 高（正文）；未知（覆盖与绑定） |
| A5-04 | django/django<br>[runs/a5-main/django__django-13964/b0/r2](D:/crq/src/coding-harness/runs/a5-main/django__django-13964/b0/r2)<br>[S](D:/crq/src/coding-harness/runs/a5-main/django__django-13964/b0/r2/summary.json) / [D](D:/crq/src/coding-harness/runs/a5-main/django__django-13964/b0/r2/candidate-delta.json) | `max_model_calls`；`delta_lost`；`false` | 编辑后导出空 delta（delta_lost）；没有测试 | 高（产物）；未知（丢失原因） |
| A5-05 | django/django<br>[runs/a5-main/django__django-15973/b0/r1](D:/crq/src/coding-harness/runs/a5-main/django__django-15973/b0/r1)<br>[S](D:/crq/src/coding-harness/runs/a5-main/django__django-15973/b0/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a5-main/django__django-15973/b0/r1/candidate-delta.json) | `max_model_calls`；`fail`；`false` | 无编辑、空候选；无测试；定位/预算问题 | 高 |
| A5-06 | django/django<br>[runs/a5-main/django__django-13512/b0/r2](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2)<br>[S](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2/summary.json) / [D](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2/candidate-delta.json) | `completed`；`partial`；`false` | 12 项通过；另一命令只见清理正文；绑定未知 | 高（观察）；未知（另一命令结果） |
| A6-01 | django/django<br>[runs/a6-retry/django__django-13512/r0/b0r1/r1](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1)<br>[S](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1/candidate-delta.json) | `completed`；`partial`；`false` | 测试正文通过；任务仍 partial；绑定未知 | 高（观察）；未知（覆盖与绑定） |
| A6-02 | django/django<br>[runs/a6-retry/django__django-13512/r1/b0r3/r2](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r3/r2)<br>[S](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r3/r2/summary.json) / [D](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r3/r2/candidate-delta.json) | `max_model_calls`；`partial`；`false` | 非空候选；无命令、无测试；反复编辑至预算耗尽 | 高 |
| A6-03 | sphinx-doc/sphinx<br>[runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1)<br>[S](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1/candidate-delta.json) | `model_parse_error`；`fail`；`false` | 11 项既有测试通过；model_parse_error；绑定未知 | 高（观察）；未知（覆盖与绑定） |
| A6-04 | django/django<br>[runs/a6-retry/django__django-13964/r1/b0r2/r1](D:/crq/src/coding-harness/runs/a6-retry/django__django-13964/r1/b0r2/r1)<br>[S](D:/crq/src/coding-harness/runs/a6-retry/django__django-13964/r1/b0r2/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a6-retry/django__django-13964/r1/b0r2/r1/candidate-delta.json) | `context_overflow`；`fail`；`false` | 非空候选；只写复现文件、未运行；上下文溢出 | 高 |
| A6-05 | django/django<br>[runs/a6-retry/django__django-13512/r1/b0r1/r1](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1)<br>[S](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/candidate-delta.json) | `completed`；`pass`；`true` | 先通过后改另一源码，再跑测试；最终绑定未知 | 高（顺序）；未知（绑定） |
| A6-06 | django/django<br>[runs/a6-retry/django__django-15022/r0/b0r2/r1](D:/crq/src/coding-harness/runs/a6-retry/django__django-15022/r0/b0r2/r1)<br>[S](D:/crq/src/coding-harness/runs/a6-retry/django__django-15022/r0/b0r2/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/a6-retry/django__django-15022/r0/b0r2/r1/candidate-delta.json) | `max_model_calls`；`delta_lost`；`false` | 复现导入错误；之后无复测；空 delta | 高（导入与产物）；未知（根因） |
| CR-01 | django/django<br>[runs/cr2-main/django__django-11749/n/r1](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1)<br>[S](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/candidate-delta.json) | `completed`；`pass`；`true` | 可见行为失败后继续修改并复测；230 项通过；绑定未知 | 高（顺序）；未知（绑定） |
| CR-02 | django/django<br>[runs/cr2-main/django__django-11211/n/r1](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/n/r1)<br>[S](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/n/r1/summary.json) / [D](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/n/r1/candidate-delta.json) | `max_model_calls`；`fail`；`false` | 只读至预算耗尽；无候选、无测试 | 高 |
| CR-03 | django/django<br>[runs/cr2-main/django__django-11211/t/r3](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/t/r3)<br>[S](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/t/r3/summary.json) / [D](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/t/r3/candidate-delta.json) | `step_deadline`；`fail`；`false` | 只读后 step_deadline；无候选、无测试 | 高 |
| CR-04 | scikit-learn/scikit-learn<br>[runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4)<br>[S](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/summary.json) / D 缺失 | `candidate_snapshot_failed`；`原始 infra_error / 清单 delta_too_large`；`false` | 测试正文通过；导出超限、无候选可交付 | 高（导出失败）；未知（膨胀根因） |
| CR-05 | sphinx-doc/sphinx<br>[runs/cr2-main/sphinx-doc__sphinx-10614/t/r2](D:/crq/src/coding-harness/runs/cr2-main/sphinx-doc__sphinx-10614/t/r2)<br>[S](D:/crq/src/coding-harness/runs/cr2-main/sphinx-doc__sphinx-10614/t/r2/summary.json) / [D](D:/crq/src/coding-harness/runs/cr2-main/sphinx-doc__sphinx-10614/t/r2/candidate-delta.json) | `model_parse_error`；`fail`；`false` | 反复读取至解析错误；无候选、无测试 | 高 |
| CR-06 | pytest-dev/pytest<br>[runs/cr2-main/pytest-dev__pytest-8399/n/r2](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2)<br>[S](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/summary.json) / [D](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/candidate-delta.json) | `context_overflow`；`pass`；`true` | 非空候选；仅 fixture 展示探针、零测试；上下文溢出 | 高（零测试）；未知（覆盖与绑定） |

CR-04 的原始 summary 与清单差别来自冻结 runner 的 counted_status 规则，见 [scripts/run_cr2.py](D:/crq/src/coding-harness/scripts/run_cr2.py:484)；本报告保留两种状态，不修改历史口径，也不把它重新算成普通环境退役运行。

## 4. 每条轨迹的命令、后续编辑和证据

以下列出影响判断的命令；长命令的脚本正文和完整参数留在相应 intent。除特别说明，提到的测试命令都带有 `2>&1 | tail ...` 或 `grep ...`，正文通过仅是观察结果。有检查且有候选的案例，其最终版本绑定未知；没有检查或没有可交付候选时，不适用绑定通过。

### A5-01

[#219](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:219) → [#220](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:220)：`python tests/runtests.py prefetch_related.tests.PrefetchRelatedTests.test_prefetch_GFK_uuid_pk`，测试类名错误，正文 FAILED(errors=1)，shell exit 0。纠正类名后 [#233](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:233) → [#234](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:234) 为 1 test / OK。随后 [#247](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:247)、#254、#261 修改公开测试/模型；[#268](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:268) → [#269](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:269) 产生 1 个实际断言失败（exit 仍为 0）。[#282](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:282) 修改 `django/contrib/contenttypes/fields.py`；[#289](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:289) → [#290](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:290) 重跑两个 GFK 用例，2 tests / OK；[#296](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:296) → [#297](D:/crq/src/coding-harness/runs/a5-main/django__django-11211/b0/r1/events.jsonl:297) 跑 `prefetch_related contenttypes_tests`，可见 OK，测试总数被 tail 丢弃。此后没有显式编辑；#300 snapshot，#301 finished。S 在中途存在但已重跑；F 已处理。最终 delta 3 文件。

### A5-02

[#25](D:/crq/src/coding-harness/runs/a5-main/django__django-15022/b0/r3/events.jsonl:25) 起反复 search_replace `django/contrib/admin/options.py`，一直到 [#284](D:/crq/src/coding-harness/runs/a5-main/django__django-15022/b0/r3/events.jsonl:284) → [#285](D:/crq/src/coding-harness/runs/a5-main/django__django-15022/b0/r3/events.jsonl:285)；21 次编辑工具结果均 success=true。完整 tool.intent 中没有 run_command，也没有其他验证工具。[#288](D:/crq/src/coding-harness/runs/a5-main/django__django-15022/b0/r3/events.jsonl:288) snapshot / #289 finished，summary 为 max_model_calls；最终 delta 1 文件。这是 U 的强证据，不能推断补丁一定错误，也不能推断尚有反馈预算。

### A5-03

[#27](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1/events.jsonl:27) → #28 修改 `django/forms/fields.py`。[#34](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1/events.jsonl:34) → [#35](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1/events.jsonl:35)：`python tests/runtests.py forms_tests.field_tests.test_jsonfield --settings=test_sqlite`，12 tests / OK；[#41](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1/events.jsonl:41) → [#42](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r1/events.jsonl:42)：`forms_tests admin_inlines model_fields`，1167 tests / OK(skipped=22)。之后没有显式编辑；#48 snapshot / #49 finished。partial 与公开测试通过正文并存；目标覆盖不足是可能解释，不是已从隐藏评分验证的原因。

### A5-04

唯一 run_command 是 [#34](D:/crq/src/coding-harness/runs/a5-main/django__django-13964/b0/r2/events.jsonl:34) → #35 的 `ls tests/ | grep -i "many_to_one\|foreign"`，属于定位。[#73](D:/crq/src/coding-harness/runs/a5-main/django__django-13964/b0/r2/events.jsonl:73) 至 [#292](D:/crq/src/coding-harness/runs/a5-main/django__django-13964/b0/r2/events.jsonl:292) 多次 edit_file `django/db/models/fields/related_descriptors.py`，记录 success=true。#296 snapshot / #297 finished；最终 delta 空且 summary=delta_lost。没有测试；有编辑记录却无最终差异，可能涉及编辑恢复/导出等多种原因，不能直接断言后端丢失了正确补丁。

### A5-05

完整轨迹只有搜索、读取和 [#292](D:/crq/src/coding-harness/runs/a5-main/django__django-15973/b0/r1/events.jsonl:292) → #293 的 `grep -rn "through=" tests/migrations/ | ... | head -20`，没有写工具或测试。#298 snapshot / #299 finished；delta 空，max_model_calls。主要是定位/推理/预算问题；B 能报告未检查、无候选，不能保证产生修复。

### A5-06

[#27](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2/events.jsonl:27) 修改 `django/forms/fields.py`；[#34](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2/events.jsonl:34) → [#35](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2/events.jsonl:35)：`forms_tests.field_tests.test_jsonfield --settings=test_sqlite -v1`，12 tests / OK。[#41](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2/events.jsonl:41) → [#42](D:/crq/src/coding-harness/runs/a5-main/django__django-13512/b0/r2/events.jsonl:42)：`python tests/runtests.py admin_views --settings=test_sqlite -v1 2>&1 | tail -8`，只有数据库清理正文，exit 0；该命令是否通过未知。没有后续显式编辑，#49 finished，delta 1 文件。

### A6-01

[#27](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1/events.jsonl:27) 修改 `django/forms/fields.py`；[#34](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1/events.jsonl:34) → [#35](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1/events.jsonl:35) 为 `forms_tests.field_tests.test_jsonfield`，12 tests / OK；[#41](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1/events.jsonl:41) → [#42](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r0/b0r1/r1/events.jsonl:42) 为 `forms_tests admin_utils --settings=test_sqlite`，726 tests / OK(skipped=1)。之后没有显式编辑，#49 finished，partial。与 A5-03 同题，不作为独立覆盖证据。

### A6-02

[#57](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r3/r2/events.jsonl:57) 修改 `django/forms/fields.py`；#59 起反复修改 `django/db/models/fields/json.py`，最后一次为 [#292](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r3/r2/events.jsonl:292) → #293。完整轨迹没有 run_command。#296 snapshot / #297 finished；max_model_calls，最终仅 forms 文件留在 delta（不把编辑过的 json.py 自动计作最终修改）。U；无法确认测试 would-fail，也不能把没有最终差异的编辑都解释成补丁丢失。

### A6-03

#76、#90 修改 `sphinx/ext/inheritance_diagram.py`。[#97](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1/events.jsonl:97) → #98、#118 → #119、#167 → #168、#195 → #196 均为写 `/tmp/repro_test.py` 后执行自建 pytest，分别有 1 passed；这些不是预先冻结的既有测试。[#265](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1/events.jsonl:265) 修改 `sphinx/ext/graphviz.py`，#272 → #273 跑调试脚本打印链接；[#279](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1/events.jsonl:279) → [#280](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1/events.jsonl:280)：`python -m pytest tests/test_ext_inheritance_diagram.py tests/test_ext_graphviz.py -q`，11 passed。此后没有显式编辑；#284 finished，summary model_parse_error / fail，delta 2 文件。不能以 11 passed 推断目标已解决，也不能用最终 fail 反向捏造公开失败细节。

### A6-04

#74、#81、#102、#116 修改 `django/db/models/fields/related_descriptors.py`。[#123](D:/crq/src/coding-harness/runs/a6-retry/django__django-13964/r1/b0r2/r1/events.jsonl:123) → [#124](D:/crq/src/coding-harness/runs/a6-retry/django__django-13964/r1/b0r2/r1/events.jsonl:124) 的长命令只有 `cat > /tmp/repro_test.py << ...; echo done`，**没有 Python 执行**；exit 0 只证明写文件命令结束。之后 #162 至 [#242](D:/crq/src/coding-harness/runs/a6-retry/django__django-13964/r1/b0r2/r1/events.jsonl:242) 继续编辑；#174 是一次失败的 search_replace，属于工具写失败。[#245](D:/crq/src/coding-harness/runs/a6-retry/django__django-13964/r1/b0r2/r1/events.jsonl:245) context.rejected；#247 finished，context_overflow，delta 1 文件。U；不把包含 Python 代码的 shell 字符串当作运行测试。

### A6-05

[#156](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:156) → #157 的 `python -c` 打印 JSON 字符串；#170 修改 `django/forms/fields.py`，#177 → #178 再打印；[#179](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:179) → [#180](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:180) 的 JSONField 既有测试 12 tests / OK。[#216](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:216) 又修改 `django/db/models/fields/json.py`，所以前面的通过不能继续代表新候选。#223 → #224 是打印探针；#225 → #226 的 tail 只有清理正文。随后 [#232](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:232) → [#233](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:233) 跑 JSONField + model_fields：419 tests / OK(skipped=10)；[#239](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:239) → [#240](D:/crq/src/coding-harness/runs/a6-retry/django__django-13512/r1/b0r1/r1/events.jsonl:240) 跑 `forms_tests admin_inlines model_fields`：1167 tests / OK(skipped=22)。#246 → #247 的 git diff exit 127 是工具缺失，不是测试失败；之后只有 grep。#261 finished，delta 2 文件，pass。中途 S 已由后续重跑处理；最终绑定仍未知。

### A6-06

[#88](D:/crq/src/coding-harness/runs/a6-retry/django__django-15022/r0/b0r2/r1/events.jsonl:88) → [#89](D:/crq/src/coding-harness/runs/a6-retry/django__django-15022/r0/b0r2/r1/events.jsonl:89) 写并执行 `/tmp/repro.py`，在 django.setup 导入阶段 `ModuleNotFoundError: No module named 'tests'`，exit 1。属于 E/无法判定，不是已复现的行为失败。#123、#130 起修改 `django/contrib/admin/options.py`，直到 [#284](D:/crq/src/coding-harness/runs/a6-retry/django__django-15022/r0/b0r2/r1/events.jsonl:284)；#256 有写工具失败；没有再执行命令。#289 finished，max_model_calls / delta_lost，最终 delta 空。导入根因与空差异根因未知，B 应保留无法判定和无可交付候选，不能用行为反馈循环自动修复环境。

### CR-01

#78 写公开复现命令文件；[#80](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:80) → #81 是 Unknown command；[#87](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:87) → #88 是 settings 导入错误；均是准备/配置失败。修正脚本后 [#101](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:101) → [#102](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:102) 出现公开行为 CommandError（互斥必需参数未被识别）。#122、#136 修改 `django/core/management/__init__.py`；[#143](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:143) → [#144](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:144) 行为仍失败（pipeline exit 0）。[#157](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:157) 再改源码，[#164](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:164) → [#165](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:165) 脚本正常打印。#185 修改公开测试；[#192](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:192) → [#193](D:/crq/src/coding-harness/runs/cr2-main/django__django-11749/n/r1/events.jsonl:193)：`python tests/runtests.py user_commands admin_scripts -v1`，230 tests / OK。之后 git diff exit 127，无后续显式编辑；#216 finished，delta 3 文件，pass。F 被模型处理，不能算“失败不处理”。

### CR-02

完整轨迹只有 11 次 search_code、39 次 read_file，无 run_command、无写工具。最后 [#302](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/n/r1/events.jsonl:302) → #303 仍是读取；#306 snapshot / #307 finished。max_model_calls，delta 空。B 的完成状态检查可指出没有候选和验证，主要瓶颈仍是没有形成补丁。

### CR-03

完整轨迹只有搜索、目录和读取，无写工具、无 run_command。[#229](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/t/r3/events.jsonl:229) → [#230](D:/crq/src/coding-harness/runs/cr2-main/django__django-11211/t/r3/events.jsonl:230) 为最后读取的 step_deadline 失败结果；#231 snapshot / #232 finished。delta 空。不能将这个 deadline 自动归类成测试超时或补丁行为失败。

### CR-04

[#50](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/events.jsonl:50) → [#51](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/events.jsonl:51)：`python -c` 调用公开 CountVectorizer 行为，NotFittedError / exit 1。#120 修改 `sklearn/feature_extraction/text.py`；#127 → #128 打印行为结果；#134 修改公开测试。[#148](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/events.jsonl:148) → [#149](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/events.jsonl:149)：`python -m pytest sklearn/feature_extraction/tests/test_text.py -q`，49 passed；[#155](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/events.jsonl:155) → #156 的范围筛选为 54 passed / 39 deselected；[#162](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/events.jsonl:162) → [#163](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/events.jsonl:163) 跑 feature_extraction 与 test_common，正文分别 85 passed、5 passed/3 xfailed。此后没有显式编辑；#169 finished。summary.snapshot_error=`ValueError: delta_too_large`，candidate-delta 缺失；清单计作 delta_too_large、solved=false。确有导出失败，不能断言本条由 Cython 重生成导致：runner 注释描述的历史机制只是假设线索，本条无导出清单证明。

### CR-05

12 次读取、4 次搜索、29 次 run_command，命令都是 grep/sed/ls 等阅读，不是测试；多轮重复读取相对路径/渲染相关函数。最后 [#287](D:/crq/src/coding-harness/runs/cr2-main/sphinx-doc__sphinx-10614/t/r2/events.jsonl:287) → #288 仍为 grep。没有写工具，#292 finished，model_parse_error；delta 空。不能把命令中出现 tests、python 文件名或异常示例当成执行过测试。

### CR-06

[#55](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:55) 修改 `src/_pytest/unittest.py`。[#69](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:69) → [#70](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:70) 写临时 unittest 文件并跑 `python -m pytest --fixtures | grep ...`，grep exit 1；这不执行测试体，也不直接证明修复失败。[#76](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:76) → [#77](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:77) 明确显示 no tests ran，并打印导入源为 /workspace。[#83](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:83) → [#84](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:84) 比较默认与 -v 的 fixture 展示，可见 -v 下名称；这是一项行为探针，不是非空测试通过。随后只读搜索，直到 [#223](D:/crq/src/coding-harness/runs/cr2-main/pytest-dev__pytest-8399/n/r2/events.jsonl:223) → #224；#226 context.rejected，#228 finished。delta 1 文件、独立结果 pass；既有回归测试未执行。B 的零测试判断不能简单把这种展示命令判为行为失败，应由声明的检查类型区分。

## 5. 聚合结果与未知项

下面都是这 18 条中的计数，类别可重叠；不外推项目失败分布，不计算收益置信区间。

| 观察 | 条数 | 案例与含义 |
| --- | ---: | --- |
| 有非空最终 delta | 11 | 17 份 delta 中的 11 份；CR-04 没有 delta，不放入分母 |
| 非空候选，没有执行任何测试/行为检查 | 3 | A5-02、A6-02、A6-04；后者只生成脚本 |
| 非空候选，仅展示探针，没有既有回归执行 | 1 | CR-06；不能与上行混称“没做任何验证” |
| 有非空测试通过正文 | 8 | A5-01/03/06、A6-01/03/05、CR-01/04；不能认证为最终候选通过 |
| 观察到通过后继续改、后来又重跑 | 2 | A5-01（改测试/模型）、A6-05（改另一源码）；最终仍使用旧通过记录的已确认案例为 0 |
| 明确公开行为失败后修改并再次观察/测试 | 3 | A5-01、CR-01、CR-04；均有继续处理；最终明确失败未处理的已确认案例为 0 |
| 没有编辑、最终空 delta | 4 | A5-05、CR-02/03/05；主要是读取/定位/停止问题 |
| 有编辑记录而最终 delta 空 | 2 | A5-04、A6-06，delta_lost；具体原因未知 |
| 导出失败且没有候选文件 | 1 | CR-04；有测试通过正文也不能交付补丁 |
| 明确复现准备/导入错误 | 2 | A6-06、CR-01；后者后来修正，前者未复测；不是两次目标行为失败 |
| 运行时可证明最终候选—检查—环境完全绑定 | 0 | 有检查的候选绑定未知；无检查/无候选不适用；缺少中间完整内容身份/冻结检查身份 |
| solved=true 成功参照 | 4 | A5-01、A6-05、CR-01/06；正常完成与独立解出不可合并 |

结束原因：completed 5、max_model_calls 7、model_parse_error 2、context_overflow 2、step_deadline 1、candidate_snapshot_failed 1。4 条无充分既有检查的非空候选全部在预算耗尽/上下文溢出下结束；B 需要为验证保留预算和如实结算，不能靠追加同样的剩余模型上下文解决。

主要未知：测试开始/结束时的完整候选内容、冻结的测试/配置身份、上游测试进程退出码、部分 tail 丢弃的计数、未执行的目标检查、delta_lost 的原因、CR-04 的具体膨胀文件、旧镜像当前仍否可用。本轮没有重放验证，也没有以 grader 的未公开诊断补齐这些空白。21 个已解引用 payload 的哈希校验只证明日志产物完整，不证明候选版本身份。

## 6. B 的三个可审阅完整案例

### 6.1 A5-02：有候选，没有一次测试执行

证据链是第 4 节 A5-02 的 #25 → #284/#285 → #288/#289，再对照非空 delta 与 max_model_calls。它支持“验证是独立状态、结束不能默认成功”的需求。初版 B 若在预算中保留一次公开验证，可在终止边界记录实际检查结果；若无法执行，应输出未执行/预算不足。已经没有模型调用余额时，不承诺反馈后还能修复。

本条没有公开失败输出可用于设计具体反馈，也没有预先声明的冻结公开命令。不能从私有评分反查应该跑哪些测试。P1 应由仓库配置另行声明一套正常开发可见检查，并明确它是回归覆盖还是目标行为覆盖。

### 6.2 A5-01：shell 绿色、正文失败、候选和测试都变化

可直接沿 #219/#220（测试名错误但 exit 0）→ #233/#234（1 项通过）→ #247/#254/#261（测试和模型变化）→ #268/#269（断言失败但 exit 0）→ #282（源码修改）→ #289/#290（2 项通过）→ #296/#297（更宽测试 OK）打开完整链条。

B 可以把收集/选择错误与断言失败分开，保留真实测试进程状态，绑定每轮候选和检查身份，并在测试或源码变化时使旧凭据失效。**本例旧模型已经处理了失败并重跑**；B 的可证价值是可靠记录/执行规则，而非本次本来会漏测。自建测试内容变化本身也会改变检查身份；新测试不能自动替代启动时冻结的既有公开检查。

### 6.3 A6-05：两处源码分阶段修改，回归通过仍需限制声明

沿 #170（forms 修改）→ #179/#180（12 项通过）→ #216（models 修改）→ #225/#226（只有清理正文）→ #232/#233（419 项通过）→ #239/#240（1167 项通过）→ 最终双文件 delta，即可检查候选变化和验证顺序。

B 应使 #180 对新候选失效，对后续检查重新记录候选/环境/测试身份，并解析出实际执行与跳过数量。#226 只有 tail 的清理正文不能计成通过；#233、#240 虽有通过正文，仍只能声称相应范围的回归观察。旧轨迹已经再次执行测试，不能把它写成最终候选未重测的正例。

这些案例足够支撑工程可信度目标；没有提供 B 改善实际解题率的反事实证据。

## 7. 主要不由 B 解决的问题

1. **定位和推理没有形成候选。** 4 条只读空候选、反复编辑和上下文溢出需要预算/上下文/模型行为方面的研究。B 的有限反馈不增加模型能力，不恢复已经耗尽的额度。
2. **检查覆盖不足。** A5-03/06、A6-01 的公开测试通过正文与 partial，A6-03 的 11 passed 与 fail 同时存在。原因未知，但足以要求分开报告回归检查和独立解出；禁止用隐藏测试补反馈。第一版只用既有公开测试时，应接受目标行为覆盖可能不足。
3. **执行准备和工具缺失。** A6-06 的导入问题、CR-01 初期 settings 错误、A6-05/CR-01 的 git 不可用，应分类而不是当成可修复的断言失败。没有必要把 B 扩为自动安装/环境修复系统。
4. **候选导出与副作用。** CR-04 在测试后导出超限；A5-04/A6-06 为空 delta。独立验证副本有机会减少测试生成物污染，但并不自动修好导出、确定膨胀来源或恢复旧补丁。需要环境/后端核查；本条不证明隔离副本一定消除超限。
5. **未知版本的历史数据。** 新 B 可以记录未来执行身份，不能追认旧记录为当前候选通过，也不能靠最终 delta 推回每一轮工作区状态。

## 8. 首批场景与 P1 决策建议

| 顺序 | 仓库/公开检查 | 已有依据 | 启动条件与限制 |
| --- | --- | --- | --- |
| 首选 | Sphinx，`python -m pytest tests/test_ext_inheritance_diagram.py tests/test_ext_graphviz.py -q`，cwd=/workspace | A6-03 #279/#280 的 11 passed；summary 有 clean_image_digest，既有 clean-image 记录有 Python 3.9 基础镜像元数据 | 从对应初始版本的原有公开测试冻结；先基线，不使用旧模型生成的 /tmp 脚本或隐藏评分材料；依赖版本与 Graphviz 等构建依赖由环境会话核实。本轮仅证明历史上能执行，不证明今天可启动 |
| 条件性第二 | scikit-learn，先限定 `python -m pytest sklearn/feature_extraction/tests/test_text.py -q` | CR-04 #148/#149 的 49 passed；单文件和更宽检查均有公开轨迹 | 先核查候选快照/导出上限及测试生成物范围。将目标模块的初始公开测试固定；49 项包含当时模型修改过测试的可能影响，不能直接作为未来冻结数目。更宽 test_common 先不纳入首批必选 |

两者采用 pytest，适合第一版支持范围；Django 的证据更丰富，但用自有 runtests/unittest runner，不宜为了使用本报告把它伪装成 pytest 场景。若主会话改选 Django，应明确把其结果解析器作为额外支持范围，并沿 A6-05 等已有公开命令冻结范围。

环境证据定位：[summary.json](D:/crq/src/coding-harness/runs/a6-retry/sphinx-doc__sphinx-10614/r1/b0r2/r1/summary.json)、[docs/sprint/clean-images/sphinx-doc__sphinx-10614.json](D:/crq/src/coding-harness/docs/sprint/clean-images/sphinx-doc__sphinx-10614.json)、[summary.json](D:/crq/src/coding-harness/runs/cr2-main/scikit-learn__scikit-learn-10908/t/r4/summary.json)、[docs/sprint/clean-images/scikit-learn__scikit-learn-10908.json](D:/crq/src/coding-harness/docs/sprint/clean-images/scikit-learn__scikit-learn-10908.json)。镜像 digest 应从这些原始记录完整读取，不用 Python 大版本或镜像别名代替环境身份。P0 不确认镜像仍在本机，也不预授权模型调用。

建议主会话在 P1 明确四个选择：

- 首批采用 Sphinx 单场景，还是在环境审计通过后加入 scikit-learn；后者的导出失败是实际阻塞信号。
- 完整候选版本纳入哪些执行相关文件、配置和依赖，如何与检索版本区分；覆盖范围外的修改如何拒绝/标记。
- 基线与最终候选的公开检查如何冻结、怎样保存真实测试状态与计数；打印/展示型行为探针应单独声明，不能按“pytest 命令”自动认证。
- 完成检查和异常结束分别如何输出；验证额度如何从总预算预留。保留“最多 3 次候选验证、最多 2 次失败反馈”为可讨论默认，不把旧 max_model_calls 结束强行恢复成新一轮满额调用。

P1 可据此开始接口设计，不需要先扩大轨迹审计。若计划要求先证明“失败反馈是主要收益来源”，本 P0 尚不满足该强结论，需要另行预注册同期小对照；不要补选案例来填补。

## 9. 本报告的校验与完成边界

已核对 18 个运行目录、18 份 summary/events、17 份 delta 以及使用的 21 个 payload 引用；事件定位通过 seq 与行号对应校验，151 个本地链接（149 个不同目标，含行号）在成文后再次检查，全部存在且事件行号匹配 seq。18 份 events 总计 8,679,115 字节，未全量扫描运行树。仅诊断历史证据，不实施功能，不改运行产物、旧报告或环境，不 commit/push。仓库已有并行变更保留原样。

# FIX-13 报告可读性与证据边界复核

## 复核范围

只读检查了：

- `docs/analysis/fix13/FIX-13-RESULTS.zh-CN.md`
- `docs/analysis/fix13/report.json`
- `docs/analysis/fix13/review.json`
- `docs/analysis/fix13/runs.json`
- `runs/fix13/progress.json`
- `runs/fix13/batch-report.json` 与 `batch-report.final.json`
- 四组设计和实现状态文档中的证据边界说明

未调用模型、未启动 Docker、未重跑实验，也未修改现有报告。

## 总体判断

报告已经具备易读的主干：先给出批次规模和费用，再给出四组表格、题级配对比较、行为指标、人工抽样复核、限制条件和原始证据入口。它也明确写出了 30 道新题、单一模型、旧 30 题仅用于开发、公开任务无法证明训练未见，以及 E/UT 不能拆分为单一机制等主要边界。

现有文字总体没有把行为指标直接写成解出率的因果证明；R 的提醒触发、E 的可见说明和 `found_idle` 计数都配有描述性限制。仍有几处标题和名词会让快速阅读者产生比证据更强的理解，建议在最终交付前做小幅措辞修订。

## 具体发现与建议

### 1. “有效名额”需要解释统计口径

表格把四组都写成“有效名额 60/60”，而 `batch-report.final.json` 的原始状态计数仍包含 9 个 `status=infra_error`。这 9 个都是 `candidate_snapshot_failed / ValueError: delta_too_large`，按冻结的 `counted_status` 规则作为任务结果标签计入，而不是缺失名额；现有报告没有在表格附近把这层映射说清楚。

建议在表格前或脚注增加：

> “这里的‘有效名额’按预注册的 counted-status 口径计算。9 个 `delta_too_large` 快照失败被保留为任务结果标签，不作为缺失名额；退役尝试和两次历史中断 R ledger 另列在费用与恢复说明中。”

这样读者不会把“有效 60/60”和状态表中的 `infra_error` 误读为互相矛盾。

### 2. 问题标题宜从因果式改成观测式

当前标题使用“重复提醒是否改善解出率和费用”“一句话解释是否改善‘读到后不编辑’”“UT 是否仍是更强基线”。正文有足够的限制语句，但标题先给出了“改善/更强”的因果或泛化框架。

建议改成：

- “R 与 U 的解出率、费用和提醒覆盖：本轮观测”
- “E 与 U 的解出结果及定位后未编辑计数：本轮观测”
- “UT 与 U 的解出率差异：本轮观测”

随后再在每段开头写“观测到……”，结尾保留“不能归因于单一机制/不能外推”的限制。

### 3. E 段落应把行为结果和解出结果分开

E 段标题把“读到后不编辑”的行为问题和解出率差异放在同一问句中；读者可能把 `found_idle` 从 19/60 降到 4/60 理解为解释提示导致了解题提升。报告后文已经写明可见文字、历史变化和行为差异不能单独归因，建议在该段开头明确分成两句：

> “行为上，E 的 `found_idle` 计数低于 U（4/60 对 19/60）；这是描述性差异。解出上，E-U 的题级差为 +16.7 个百分点，Holm 校正 p=0.09296。两者不能合并解释为‘一句话解释导致解出率提升’。”

### 4. UT 结果要在主句中立即限定范围

“该结果支持本配置下 UT 的解出表现更高”总体谨慎，但“更强基线”标题和这句相邻出现时，快速读者仍可能理解成一般模型能力结论。建议改为：

> “在这 30 道预先确定的新题、当前单一模型和本轮 UT 配置下，UT 的观测解出率高于 U；这不能拆分为 thinking、输出额度或两者交互的单独作用，也不能外推到其他模型或题库。”

### 5. 费用上界建议直接给出最大值

报告正确区分了 55.442973 元的已计入标准费用与旧中断 ledger 的 8 元 reservation 上界，也写明账户账单尚未对账。为了避免读者只记住一个数字，建议补一句：

> “若把两份历史中断 reservation 按最保守上界全部计入，本轮证据相关费用上界为 63.442973 元；这仍不是供应商实际账单。”

该数值与 `batch-report.final.json` 的 `total_with_unknown_unsettled_upper_bound_cny` 一致。

### 6. 人工复核的性质已经写对，但可再前置

报告已说明失败分类抽样 12/12 一致、E 语义抽样由单人完成且不是独立双人评审。这是正确的证据边界。建议在“必要复核”小节开头先放一句“以下是描述性人工抽样，不是盲法独立评审”，再列结果，减少读者把 12/12 当成分类准确率保证的风险。

### 7. “每解出运行费用”应保留运行分母提醒

列名已经是“每解出运行费用”，这是正确的，不应改成“每解出题目费用”。可在表头下补一句：

> “该列按解出运行数计算；它不是去重题目层面的成本。”

## 已确认的边界与优点

- 主比较保留原分配组，未把 R 触发后的子集表现当成随机组间因果结果。
- E 的可见文字计数与语义抽样分开，且承认仍有无句轮次。
- UT 的 reasoning token 暴露被单独报告，没有声称已识别 thinking 的单独因果作用。
- 30/30 完整题目、四组各 60 名额、恢复尝试和历史未知账本均有记录入口。
- 报告明确说明账户实际账单尚未对账，标准峰价费用不能直接替代供应商账单。
- 报告明确指出公开任务不能证明训练未见，旧 30 题仅用于开发，新 30 题支撑本次预先确定的比较。

## 建议的只读验证命令

```powershell
.venv/Scripts/python.exe -c "import json; from pathlib import Path; r=json.loads(Path('docs/analysis/fix13/report.json').read_text(encoding='utf-8')); print(r['planned_slots'], r['completed_slots'], r['complete_tasks']); print({g:(v['completed_slots'],v['valid_slots'],v['solved']) for g,v in r['groups'].items()}); print(r['batch']['spent_cny'])"
.venv/Scripts/python.exe -c "import json; from pathlib import Path; d=json.loads(Path('runs/fix13/batch-report.final.json').read_text(encoding='utf-8')); print(d['final_runs'], d['complete_tasks'], d['total_with_unknown_unsettled_upper_bound_cny']); print({g:v['status_counts'] for g,v in d['by_group'].items()})"
rg -n "因果|改善|更强|不能|支持|行为|费用|上界|单人" docs/analysis/fix13/FIX-13-RESULTS.zh-CN.md
```

## 结论

报告可以作为当前结果的易读初稿，统计主线和主要证据边界基本到位。最终交付前建议至少采纳第 1、2、3、5 条：解释 `delta_too_large` 的统计口径、把三个问题标题改为观测式、分离 E 的行为与解出结果，并直接给出 63.442973 元的保守费用上界。

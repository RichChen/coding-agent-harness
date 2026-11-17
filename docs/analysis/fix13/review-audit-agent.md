# FIX-13 只读一致性审计

审计时间：2026-11-15（共享工作区当前文件）

## 审计范围

只读核对了以下输入：

- `runs/fix13/progress.json`
- `runs/fix13/batch-report.json`
- `runs/fix13/batch-report.final.json`
- `runs/fix13/batch-report.recovery-blocked.json`
- `docs/analysis/fix13/runs.json`
- `docs/analysis/fix13/report.json`
- `docs/analysis/fix13/review.json`
- `docs/analysis/fix13/FIX-13-RESULTS.zh-CN.md`
- `runs/fix13` 下最终名额、退役尝试和保留未知账本

本审计没有调用模型、启动 Docker、重跑正式实验或修改上述证据文件；本报告是唯一新增文件。

## 核对结果

1. **最终名额一致。** `progress.json`、两个最终 batch report、`runs.json` 和 `report.json` 均记录 240/240 个最终名额、30/30 个完整题目；四组均为 60 个最终名额。
2. **解出数一致。** U/R/E/UT 分别为 16/18/26/28，合计 88 个 solved/pass。`runs.json` 按状态重算与 `progress.json`、batch report 和 `report.json` 一致。
3. **状态计数一致。** 最终 240 行状态为：U `fail 40 / pass 16 / partial 2 / infra_error 2`；R `fail 35 / pass 18 / partial 5 / infra_error 2`；E `fail 25 / pass 26 / partial 7 / infra_error 2`；UT `fail 19 / pass 28 / partial 10 / infra_error 3`。每组各项合计 60。
4. **最终名额费用一致。** 从 `runs.json` 的 240 个最终 summary 重算，四组费用分别为 15.800914、14.712864、7.265339、16.091229 元，与 `batch-report.json` 的 `cost_cny_final_runs` 一致。
5. **报告表的费用口径有意更宽。** `report.json` / 中文报告把退役尝试也计入“全尝试标准费用”。因此 E 为 7.323528 元，比最终名额多 0.058189 元（对应 `mmornati.../E/r1.infra1`）；UT 为 16.870168 元，比最终名额多 0.778939 元（对应 `vyperlang.../UT/r1.infra1`）。U、R 没有这类差额。中文报告已明确写出该口径。
6. **总账本和恢复边界一致。** 当前已知结算为 55.442973 元，其中身份冒烟 0.000212 元；正式与恢复 ledger 为 55.442761 元。两份第一次中断的 R ledger 已移入 `r1.unknown1`/`r2.unknown1` 保留，旧未知 reservation 上界单列为 8 元，当前恢复 reservation 为 0。报告没有把 8 元上界冒充已发生费用。
7. **恢复结果已进入最终统计。** R 的两次恢复各只增加一次尝试；`batch-report` 和中文报告均记录 R/r1 恢复未解出、R/r2 恢复解出，并把恢复结果按原分配组计入主比较。
8. **配对统计与组计数一致。** `report.json` 的三项主比较使用 30 个完整题目、每题四组各两次；差值与组解出率一致：R-U +3.3 个百分点、E-U +16.7 个百分点、UT-U +20.0 个百分点。Holm 校正 p 值与排序关系一致。
9. **必要复核记录存在且与报告一致。** `review.json` 记录失败分类抽样 12/12 一致、分类变化 0；E 说明语义抽样为各组 8 条、总计 32 条。中文报告未把这些抽样结果写成全面证明，并明确说明单人复核仅为描述性核查。
10. **证据边界表述基本合格。** 中文报告明确区分观察结果、方向性推测和不能证明的事情；明确指出 E 同时改变可见文字、历史结构和缓存，UT 同时改变 thinking 与输出额度，不能拆解成单一机制因果效应；也保留了 30 题、单一模型、公开任务和旧题开发边界。

## 发现与限制

- `batch-report.json` 使用 `fix13.batch-report.final.v2`，`batch-report.final.json` 使用较早的 `v1`。两者的 0.000212 元差异来自身份冒烟口径：v1 的 `known_settled_cny` 是 formal ledger，v2 把 identity smoke 一并计入；各自内部字段自洽，中文报告采用 v2 的总额并明确拆分。
- `report.json` 的 `cost_cny_all_attempts` 与 batch report 的 `cost_cny_final_runs` 不是同一口径；这不是算术错误，但审阅者应同时看表头和解释，避免把两列直接比较。
- 账户实际账单仍未供应商对账；报告中的费用是标准峰价账本费用。
- 旧未知 reservation 的 8 元只是保守上界，不能作为已花费金额或正式比较成本。

## 验证命令

以下命令均为只读核对：

```powershell
.venv/Scripts/python.exe -c "import json; from pathlib import Path; from collections import Counter; p=json.loads(Path('docs/analysis/fix13/runs.json').read_text(encoding='utf-8')); print(len(p)); print({g:(sum(x['group']==g for x in p),sum(x['status']=='pass' and x['group']==g for x in p),Counter(x['status'] for x in p if x['group']==g)) for g in ['U','R','E','UT']})"

.venv/Scripts/python.exe -c "import json; from pathlib import Path; p=json.loads(Path('docs/analysis/fix13/report.json').read_text(encoding='utf-8')); print(p['completed_slots'],p['complete_tasks'],[(g,p['groups'][g]['completed_slots'],p['groups'][g]['solved']) for g in ['U','R','E','UT']])"

.venv/Scripts/python.exe -c "import json; from pathlib import Path; p=json.loads(Path('docs/analysis/fix13/review.json').read_text(encoding='utf-8')); print(p['failure_review']['agreement'],p['failure_review']['classification_changes'],p['explanation_review']['total'])"
```

## 结论

当前 FIX-13 的最终名额、组统计、恢复记录和离线报告相互一致；没有发现会推翻最终汇总的算术或名额错误。主要需要在对外说明中保留两点：标准费用与账户实际账单不同，且旧中断 R reservation 的 8 元是历史未知上界而非已发生费用。中文易读报告的因果边界目前写得合适。

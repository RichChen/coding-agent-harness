# Phase 7 Positive Gain Summary（最终版 · Batch 6）

## 权威主证据：Batch 5.10 方差检验

**Run `20260704_092308`** · `phase7_medium_48` · 48 tasks · 4 medium repos · seed=42 · **repeats=2** · Mock=False

| Config | Mean | Std | Repeat 0 | Repeat 1 | parse_err |
| --- | ---: | ---: | ---: | ---: | ---: |
| L0 | 45.8% | ±2.1pp | 47.9% | 43.8% | 3 |
| L2 | 70.8% | ±2.1pp | 72.9% | 68.8% | 10 |
| **L5** | **80.2%** | **±5.2pp** | 75.0% | 85.4% | 10 |

**L5 − L2 mean = +9.4pp** · 两次 repeat 均 **L5 ≥ L2** · L5 parse_err 不高于 L2

### 按类别（2-repeat mean，L5 vs L2）

| Category | Tasks | L2 | L5 | Δ |
| --- | ---: | ---: | ---: | ---: |
| skill_heavy | 10 | 65.0% | 80.0% | **+15.0pp** |
| memory_heavy | 16 | 71.9% | 84.4% | **+12.5pp** |
| multi_file_api | 4 | 37.5% | 50.0% | **+12.5pp** |
| repo_convention | 3 | 83.3% | 100% | **+16.7pp** |
| context_heavy | 10 | 85.0% | 85.0% | 0.0pp |
| traceback_repair | 5 | 70.0% | 70.0% | 0.0pp |

**验收：强通过。** 实验收口，不再跑 repeats=3。

完整方差分析：`reports/phase7-variance-summary.md`

---

## Supporting Evidence：Batch 5.9 单次 48 题

**Run `20260704_062354`** · repeats=1 · 与 5.10 mean 一致

| Config | Success | L5−L2 |
| --- | ---: | ---: |
| L0 | 48% | — |
| L2 | 69% | — |
| L5 | **79%** | **+10.4pp** |

单次 +10.4pp 落在 5.10 mean±std 内，支撑「非单次偶然」叙事。

详见 `reports/phase7-medium48-summary.md`

---

## Development History

### 24-task full eval（Batch 5.7–5.8）

| 批次 | L2 | L5 | L5−L2 | 备注 |
| --- | ---: | ---: | ---: | --- |
| 5.7 | 71% | 79% | +8.4pp | L5 < L4，skill_neg=3 |
| **5.8** | 75% | **79%** | +4.2pp | L5≥L4，skill_neg=0，context=100% |

5.8 机制改动：移除 `conservative_mix`；multi_file_api/repo_convention → memory-only；skill_primary max_skills=1。

归档：`reports/archive/batch5.7-full24/` · 失败分析：`reports/phase7-full24-failure-analysis.md`

### Probe-12（Batch 5.5–5.6）

- 5.5：L5=75% < L2=92%，未过 full 24 gate
- 5.6：domain gating + skill-only route → L5=92% > L2=83%，准入 full 24

详见 `reports/phase7-probe-12-summary.md`

### Memory 子集修复（Batch 5.4）

memory_heavy 0%→86%（6-task probe）；holdout 匹配 + procedural gating + injection policy。

---

## 报告索引

| 文档 | 路径 |
| --- | --- |
| **5.10 方差（权威）** | `reports/phase7-variance-summary.md` |
| 5.9 单次 48 | `reports/phase7-medium48-summary.md` |
| Probe-12 历程 | `reports/phase7-probe-12-summary.md` |
| Full-24 失败分析 | `reports/phase7-full24-failure-analysis.md` |
| 5.7 归档 | `reports/archive/batch5.7-full24/` |
| 5.10 原始 JSON | `reports/llm/20260704_092308/llm-ablation-summary.json` |
| **本摘要** | `reports/phase7-positive-gain-summary.md` |

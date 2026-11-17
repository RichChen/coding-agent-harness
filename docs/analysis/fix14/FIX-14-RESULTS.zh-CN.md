# FIX-14 thinking 与输出额度的 2×2 结果

在 FIX-13 的 30 道新题上补齐矩阵：U（thinking 关、8192）与 UT（thinking 开、32768）用 FIX-13 的既有结果，本轮新增 T8（thinking 开、8192）与 O32（thinking 关、32768），各 30 题 × 2 次 = 60 个名额。

## 四个格子

| 组别 | thinking | max_tokens | 名额 | 解出运行 | 解出率 | 至少一次解出的题 | 全尝试费用/元 | 每解出运行费用/元 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| U | thinking off | 8192 | 58/60 | 16/60 | 26.7% | 9/30 | 15.8009 | 0.9876 |
| T8 | thinking on/high | 8192 | 55/60 | 26/60 | 43.3% | 15/30 | 17.2411 | 0.6631 |
| O32 | thinking off | 32768 | 58/60 | 17/60 | 28.3% | 11/30 | 16.9427 | 0.9966 |
| UT | thinking on/high | 32768 | 57/60 | 28/60 | 46.7% | 16/30 | 16.8702 | 0.6025 |

U 与 UT 的数值来自 FIX-13 正式批次（同一 30 题、同一镜像、同一评分器）；T8 与 O32 是本轮追加。全部费用为 provider usage 的标准峰价核算，不是已对账的账户账单。

## 按题配对比较

使用 27 道四格完整的题目，每题保留每格两次的整体向量；在 easy/other 层内配对 bootstrap 10000 次，符号翻转 100000 次。
Holm 只作用于 T8−U 与 O32−U 两项。

| 比较 | 含义 | 解出差/百分点 | 普通95%区间/百分点 | p（Holm） | 类型 |
|---|---|---|---|---|---|
| T8-U | 打开 thinking，输出额度同为 8192 | +20.4 | [+9.3, +33.3] | 0.01654 | 主比较 |
| O32-U | 输出额度扩大到 32768，thinking 同为关闭 | +1.9 | [-9.3, +13.0] | 1.00000 | 主比较 |
| UT-T8 | thinking 已开时把输出额度从 8192 提到 32768 | +3.7 | [+0.0, +9.3] | — | 探索 |
| UT-O32 | thinking 与输出额度的联合差 | +22.2 | [+9.3, +35.2] | — | 探索 |
| T8-O32 | thinking on/8192 对 thinking off/32768 | +18.5 | [+5.6, +31.5] | — | 探索 |
| UT-U | 两处都打开，对照 FIX-13 的既有结论 | +24.1 | [+11.1, +38.9] | — | 背景 |
| (UT-T8)-(O32-U) | 两个开关的交互：输出额度在 thinking 开启时的额外作用 | +1.9 | [-9.3, +13.0] | 1.00000 | 交互（探索） |

## 结论

**thinking_dominant**：T8−U 为 +20.4 个百分点且区间不跨零，O32−U 为 +1.9 个百分点且区间跨零：在这批题上，thinking 开关是主要作用，单独扩大输出额度没有可检测的收益。

从费用看，这条结论更尖锐：O32 的花费为 16.94 元，与 UT 的 16.87 元几乎相同，但解出 17 个运行，与 U 的 16 个接近；每解出运行费用 1.00 元，反而略高于 U 的 0.99 元。T8 则以 17.24 元解出 26 个运行，每解出运行 0.66 元，是四格中第二便宜。

行为上也一致：T8 的 `found_idle` 为 1 次、高重复 0 次，与 UT 的 0 次、0 次同向；O32 的 `found_idle` 为 19 次、高重复 26 次，与 U 的 19 次、22 次同向。也就是说，把输出额度从 8192 提到 32768 并没有改变模型的行动模式，而打开 thinking 把它改掉了。

## 行为与暴露

| 组别 | 定位后未编辑 | 高重复 | reasoning 非零/请求 | 每运行每请求 reasoning 中位数 | 截断 | 请求数 |
|---|---|---|---|---|---|---|
| U | 19 | 22 | — | — | 0 | 3623 |
| T8 | 1 | 0 | 1877/2202 | 325.5 | 1 | 2202 |
| O32 | 19 | 26 | — | — | 0 | 3664 |
| UT | 0 | 0 | 1920/2149 | 307.6 | 0 | 2149 |

### 缺失名额的界限

把不可评名额按全部解出（上界）和全部未解出（下界）分别计入，主比较的方向不变：

| 组别 | 实际解出 | 下界 | 上界 |
|---|---:|---:|---:|
| U | 16/60 (26.7%) | 16/60 (26.7%) | 18/60 (30.0%) |
| T8 | 26/60 (43.3%) | 26/60 (43.3%) | 31/60 (51.7%) |
| O32 | 17/60 (28.3%) | 17/60 (28.3%) | 19/60 (31.7%) |
| UT | 28/60 (46.7%) | 28/60 (46.7%) | 31/60 (51.7%) |

T8 即使 5 个不可评名额全部未解出，仍有 43.3%，高于 U 的上界 30.0%；O32 即使 2 个不可评名额全部解出，也只有 31.7%，与 U 的上界重叠。所以“thinking 是主要作用、单独扩大输出额度没有可检测收益”这一方向不由缺失名额造成。


## 不能判断的内容

- T8 与 O32 是事后追加的格子；同一 30 题已经用于 FIX-13 和 ET，不能称为未见过的独立验证。
- UT−T8 同时含输出额度与服务端采样差异；无法拆出“额度”与“服务端默认参数”各自的作用。
- 交互项只回答两个捆绑之间是否有额外作用，不能证明 thinking 与输出额度走的是同一条内部路径。
- 完整四格题目为 27/30；缺失题目见 report.json，主表结论只覆盖完整题。
- 公开任务源不能证明模型训练未见。
- 已知 harness 限制（见下节）使部分名额不可评，报告按缺失处理，不把它们算作模型失败以外的任何解释。

## 已知 harness 限制与不可评名额

本轮共有 8 个名额曾以基础设施异常收尾：1 个 `container cleanup failed` 已在批次结束后用捕获的候选补丁离线重评（`tox-dev__tox-3846/T8/r1`，重评结果为 `partial`，F2P 未全通过），因此保留为可评分的 `partial`；其余 7 个为 `delta_too_large`，没有导出候选补丁，无法重评，按不可评处理：

- `graphistry__pygraphistry-1107_interface/O32/r1`：`candidate snapshot failed: ValueError: delta_too_large`，无补丁，不可评
- `graphistry__pygraphistry-1107_interface/O32/r2`：`candidate snapshot failed: ValueError: delta_too_large`，无补丁，不可评
- `graphistry__pygraphistry-1107_interface/T8/r1`：`candidate snapshot failed: ValueError: delta_too_large`，无补丁，不可评
- `graphistry__pygraphistry-1107_interface/T8/r2`：`candidate snapshot failed: ValueError: delta_too_large`，无补丁，不可评
- `libp2p__py-libp2p-750/T8/r1`：`candidate snapshot failed: ValueError: delta_too_large`，无补丁，不可评
- `libp2p__py-libp2p-750/T8/r2`：`candidate snapshot failed: ValueError: delta_too_large`，无补丁，不可评
- `pasqal-io__Pulser-936/T8/r2`：`candidate snapshot failed: ValueError: delta_too_large`，无补丁，不可评

原因：导出候选补丁的上限是 200 个文件或 4 MB，跳过目录只含 `__pycache__`、`.pytest_cache`、`build`，不含 `.mypy_cache` 与 `.ruff_cache`。模型运行 mypy/ruff 后缓存目录被当作变更计入文件数。该限制在 FIX-13 与 ET 同样存在（240 次运行中 11 次），本轮未修改 `src/`，以保持与历史格子相同的运行时指纹。

这 7 个没有候选补丁的名额在统计中按缺失处理，不当作失败；上面的 `partial` 名额保留在有效评分中。`graphistry` 与 `pasqal` 在 FIX-13 里也存在同样的不可评名额，因此主比较只用 27 道四格完整的题目。

另外，统计口径需要提醒一点：共享的 `counted_status` 把 `delta_too_large` 映射成独立标签而非 `infra_error`，所以按该标签判断“有效名额”会把未评分的运行算作有效。本报告的 `valid` 改用 grader 结果判定，`docs/analysis/fix13/report.json` 里的历史 `valid_slots` 仍沿用旧口径，两者不可直接相减。

## 原始证据

- 运行与账本：`runs/fix14`；预检与身份绑定：`runs/fix14-preflight`。
- 逐运行数据：`docs/analysis/fix14/runs.json`；完整统计：`docs/analysis/fix14/report.json`。
- 四格历史数据：`docs/analysis/fix13/runs.json`。

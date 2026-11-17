# FIX-15 付费运行前预检审计

本文件对应 `docs/roadmap/FIX-15-E-ABLATION-DESIGN.zh-CN.md`。它只描述预检和绑定要求，不启动模型调用，不创建付费账本，不修改已有 `runs` 目录。

## 需要新增或冻结的输入

FIX-14 的预检脚本不能直接复用，因为它固定检查 `T8/O32` 两个组。FIX-15 需要一个新的计划和配置入口，明确列出 `U-new/N/E-new` 三组及 180 个名额。

每组应使用同一份 30 题任务清单、同一份 ingest/receipt/clean-image/gold 参照、同一镜像和同一评分器。计划文件应满足每题每组两次运行、顺序号唯一、题区数量为 30，且每个题区恰好包含三组各两个名额。

三组配置的允许差异只有工具干预键：

| 项目 | U-new | N | E-new |
|---|---|---|---|
| thinking | disabled | disabled | disabled |
| max_tokens | 8192 | 8192 | 8192 |
| 工具干预 | none | 新增的 visible-next-step | 现有 result_explanation |

N 的固定提示必须写入配置或代码绑定，且不能包含 `RESULT_EXPLANATION` 中“总结上一步结果及其如何决定下一步”的要求。N 不能复用 R 组的重复提醒。

## 可以在不调用模型下完成的检查

以下检查可以先完成，并应写入一个不可变的预检报告：

1. 读取三组配置并比较差异。除干预设置外，模型、thinking、输出额度、上下文策略、工具列表、工作区限制、超时、重试、追踪和评分配置都必须相同。
2. 计算计划、任务清单、配置、提示、干预实现、`scripts/run_swe_task.py`、评分器、gold 参照和相关 runner 的 SHA-256。绑定中还要记录 `source_fingerprint`。
3. 验证 30 个 task ID、30 个题区、180 个名额、每题每组两个重复、无重复序号、无遗漏任务，且任务哈希与 FIX-13 的 ingest 记录一致。
4. 验证三组均使用模型名 `deepseek-flash` 的配置值，正式运行前的系统指纹仍需单独通过身份冒烟确认。历史指纹不能直接替代本轮确认。
5. 验证标准峰价、单次 4 元上限、总批次 60 元硬上限、48 元启动阈值和 12 元在途缓冲已经进入 launcher 和批次账本设置。
6. 验证候选快照跳过 `.mypy_cache`、`.ruff_cache`、`__pycache__`、`.pytest_cache` 和其他既定生成目录，同时验证 `export_keep_original_files=true` 时原始源码保留规则仍然成立。针对性测试已经覆盖前两项缓存目录，正式预检还要用会产生缓存的真实无模型 case 验证导出结果。
7. 运行纯配置、计划、提示差异和分析绑定检查。这个阶段不得访问 provider URL，不得读取 API token，不得产生 `identity-smoke` 付费记录。

## 无模型 Docker 预检

三组至少各需要一个真实 Docker case，建议在同一首题上运行 `wiring` 和 `empty` 两类：

- `wiring` 让脚本 transport 返回固定的读取和 gold 编辑调用，检查正常工具轮、候选快照、评分和提示绑定；
- `empty` 返回空补丁，检查评分器的空补丁校准、缺失 ID、账本结算和最终摘要。

每个 case 都应保存配置/任务/source 指纹、请求体、`summary.json`、候选 delta 或明确的快照错误、`grader-result.json`、`cny-ledger.json` 和检查结果。预检必须确认 provider calls 为 0，三组的工具行为和评分结果符合脚本预期。

提示检查应逐请求进行：

- U-new 的系统提示不包含任何干预句；
- N 的可见句只说明准备执行的下一步，不出现结果总结或解释依据；
- E-new 的可见句出现于后续工具轮，并且使用完整 `RESULT_EXPLANATION`；
- 三组都保持 thinking 关闭，不能出现 reasoning replay；
- 三组均不能出现 FIX-13 R 的 repeat reminder。

## 身份、价格和账本绑定

真实身份冒烟应放在 Docker 无模型 case 全部通过之后。它最多使用 0.5 元预算，记录两次真实请求返回的模型名、系统指纹、价格快照、usage、账本路径和结算状态。前后价格快照需要一致，usage 不能未知，账本 outstanding 必须归零。

正式 ready 绑定至少应包含：

- 设计/计划/三组配置/提示实现/runner/评分器/gold 参照的哈希；
- 任务清单和 180 名额的哈希；
- 当前 `source_fingerprint`；
- 身份冒烟的模型名和系统指纹；
- 价格快照和身份账本路径；
- 无模型 Docker case 的结果及证据哈希；
- 总预算、启动阈值、并发和停止规则版本。

ready 文件写入后，任何被绑定文件变化都必须使 ready 检查失败并重新绑定。不得通过覆盖旧 evidence 来消除差异。

## 启动前暂停条件

出现下列任一情况时，停止在 180 个正式名额之前：

- U-new、N、E-new 的配置差异超出干预项；
- 任务、题区、重复次数、输入哈希或 `source_fingerprint` 不一致；
- N 和 E 的提示接线不能从请求体中明确区分；
- Docker case 有非预期失败、候选快照再次触发 `delta_too_large`、评分器输出与校准不符或账本出现未结算预留；
- 身份模型、系统指纹、价格快照或 usage 不满足绑定要求；
- 发现任何模型调用已经在预检阶段发生；
- 发现旧运行目录被覆盖或原始 FIX-13/FIX-14 证据被改写。

## 与正式运行的边界

完成上述无模型检查后，仍然只代表三组已经具备可运行条件。真实身份冒烟和 180 个正式名额属于下一阶段，必须在主会话审阅设计、预算、停止规则和预检结果后分别启动。即使用户已授权继续准备，本审计也不把预检通过自动解释成正式运行授权。

## 已完成的本地与 Docker 预检

本地配置、提示和 180 名额检查已通过，`model_calls=0`。随后使用 `scripts/rebench/fix15_preflight.py --all` 在同一题 `ome-zarr-models__ome-zarr-models-py-206` 上完成三组真实 Docker scripted wiring case。U-new、N、E-new 均通过，评分为 F2P 1/1、P2P 5/5，候选补丁成功导出，答案隔离通过，账本 outstanding 为 0。三组使用本地 scripted transport，`provider_calls=0`，没有访问 DeepSeek，也没有进行身份冒烟或正式 180 个名额运行。

证据总表为 `runs/fix15-preflight/e2e-report.json`，三组 evidence 目录位于 `runs/fix15-preflight/e2e/ome-zarr-models__ome-zarr-models-py-206/`。这一步确认了接线和运行链路，身份/价格冒烟以及正式名额仍待主会话审阅后单独处理。

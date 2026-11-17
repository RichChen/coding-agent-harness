# Coding Harness 求职展示包（草稿）

这份材料把项目整理成面试时可以在 5 分钟内讲清楚、在 20 分钟内展开的技术故事。它只使用已经有证据的结果；pilot 的数字和结论留到 pilot 完成后再补。

## 一句话定位

我做的不是一个只展示模型调用的 Coding Agent Demo，而是一套**可审计、可复现的 coding-agent harness**：模型负责决定下一步，运行时负责隔离工具、执行预算、记录轨迹，并用独立判定器区分“运行结束”和“任务真的做对”。

## 面试开场（约 90 秒）

Coding agent 的问题通常不只是模型能力。一个运行看起来成功，可能是评测被污染、工具没有真正执行、成本没有结算，或者 agent 读了很多文件却没有完成编辑。因此我把项目拆成三层：运行时约束、评测完整性、可回放的证据账本。

先做工程底座：路径和命令策略、Docker 工作区、步数/超时/模型预算、工具校验、trace、usage settlement 和恢复边界。然后用真实运行检查这些契约是否真的成立，而不是只看单元测试。这个过程发现一个更严重的问题：隐藏测试进入了 agent 可见镜像，原来的 32/42 结果不能再当作基线；清理镜像和答案隔离后，同一 dev set 变成 18/42。这个修正本身比一个漂亮的分数更有价值，因为它说明评测可信度必须先于效果结论。

之后我分析 agent 轨迹，而不是只看最终通过率。P4 中，定位以后仍有 682 次工具调用，其中 45.6% 是重复读取；`retrieve_code` 和 `validate_patch` 没有实际被使用。这样就把问题从“模型为什么不聪明”变成了可以测量的工作流问题：它是否知道何时停止读、何时开始改、工具是否真的进入决策路径。

现在的 pilot 比较 A、U、U+T 三个受控 harness 条件。它用于估计难度、成本和效应大小，不会被包装成已经证明某个组件有效；U 是多个约束一起放宽的 bundled intervention，U+T 还必须单独核对 reasoning exposure。

## 三张证据卡

### 证据卡 1：评测污染被发现并修正

- **症状**：旧结果显示 32/42，且看起来足以支持后续比较。
- **证据**：审计发现隐藏测试进入 agent 可见 image；同时检查答案隔离和 clean image。
- **修复**：重建干净镜像，确保 grader 的候选 delta 与测试补丁不泄漏到 agent 工作区。
- **结果**：同一 dev set 的可用结果变成 18/42；旧的 32/42 及其衍生卡片退役。
- **边界**：这证明了该评测路径曾经受污染并已校正，不证明所有数据集都没有污染。
- **下一步**：在 pilot 中继续记录任务来源、发布日期、image 身份、answer isolation 和任务接纳理由。

### 证据卡 2：轨迹显示“读很多但不改”

- **症状**：最终失败不一定来自定位错误，agent 可能一直在读取上下文。
- **证据**：P4 中定位后 682 次工具调用，45.6% 为重复读取；`retrieve_code` / `validate_patch` 实际调用为 0。
- **修复方向**：分别记录首次编辑前调用数、重读比例、工具使用率和最终 grader 结果，避免用一个总成功率解释所有问题。
- **边界**：这描述了观察到的行为，不等于知道模型内部原因，也不证明检索或验证工具永远没有价值。
- **下一步**：在 pilot 中预注册 reread share、calls-before-first-edit 和失败类别。

### 证据卡 3：上下文策略是成本问题，不只是窗口大小问题

- **症状**：增大 `keep_recent` 可以减少重复读取，但会把更多历史送回模型。
- **证据**：离线 context simulation 中，较大的 `keep_recent` 减少了重复读取，却使 cache-aware 成本估计增加约 58%；content-aware masking 在模拟中更便宜，但部分请求超出旧 admission 估计。
- **判断**：问题已经从“选哪个固定窗口”转为“在 prefix-cache pricing 下，什么条件值得 masking”。
- **边界**：这些是 offline replay/估算，不是 provider 账单，也不是 pilot 的真实成本。
- **下一步**：把 prompt、cache hit/miss、reasoning、admission、compaction 和实际 usage 分开记录。

## 我亲自解决的三类工程问题

1. **可信执行**：用 sandbox、命令策略、Docker、预算和恢复边界把模型行为放进可控运行时。
2. **可信评测**：把候选补丁、测试补丁、工作区和 grader 身份隔离；发现污染后回退旧结论。
3. **可解释实验**：保留逐步 trace、请求级 telemetry、usage ledger 和独立判定器，让“失败”可以继续分类为基础设施、评测、轨迹或任务结果。

## 现有证据矩阵

| Claim | 直接来源 | 强度 | 不能推出什么 |
|---|---|---|---|
| 评测曾发生 image contamination，清理后为 18/42 | `docs/roadmap/EXECUTION-STATUS.zh-CN.md`、`docs/analysis/SWE-REBENCH-FEASIBILITY.zh-CN.md` | 已审计的历史事实 | 不能推出所有 benchmark 都干净 |
| P4 存在大量重复读取且没有实际使用检索/验证工具 | `docs/analysis/P4-NO-EDIT.zh-CN.md` | 真实轨迹观察 | 不能推出模型内部原因或工具永远无效 |
| `keep_recent` 与 content-aware masking 存在成本/上下文权衡 | `docs/analysis/CONTEXT-SIM.zh-CN.md` | 离线模拟结果 | 不能当作 provider 实际账单 |
| harness 已有测试、telemetry、settlement 和 hard-limit 工程准备 | `docs/roadmap/FIX-10-RESULTS.zh-CN.md` | 工程验收/烟测事实 | 不能推出真实模型修复率提升 |
| A/U/U+T 中某个条件更好 | 待 pilot | 尚无结论 | 在 pilot 完成前不要填写 |

## 常见追问的短答

**为什么不直接说“模型不行”？**

因为最终失败混合了模型决策、工具可用性、评测泄漏、超时和成本上限。先拆开这些因素，才知道该修 prompt、工具、运行时还是评测。

**为什么没有把项目做成多 agent？**

当前目标是测量 harness 约束和行为，不是堆叠角色数量。多 agent 会引入协调、并发和分工变量，反而削弱当前 A/B 设计的可解释性。

**检索和 memory 是不是没用？**

现有结果没有检测到可靠的修复率增益，并观察到失败记忆可能产生 anchoring；这只能支持“当前实现和任务上未检出稳定收益”，不能支持“它们没有价值”。

**pilot 完成后最重要的审查是什么？**

先审有效性门槛和 telemetry 完整性，再看 solve-rate 和 cost。任何门槛失败都只能做工程诊断，不能把结果升级为扩展实验依据。

## 不能说的四句话

- “这个 harness 普遍提高了 coding-agent 能力。”
- “SWE-rebench 已证明没有污染。”
- “U 的收益来自 masking（或 admission、调用上限、预算）中的某一个组件。”
- “thinking 或 retrieval 没有价值。”

## 简历版（待用户确认）

**Coding Agent Harness** — Python coding-agent 运行时与评测框架。实现 plan-act-observe 循环、路径/命令沙箱、Docker 隔离、步数/超时/模型预算、JSONL trace 与 usage ledger；通过独立 grader 和答案隔离审计评测污染，并用真实轨迹定位重复读取、工具未使用和上下文成本权衡。基于 pilot 再补充受控条件下的成功率、成本与失败模式结果。

## 待 pilot 后补齐

- A/U/U+T 的任务级配对 solve contrast 和置信区间；
- provider usage、reasoning exposure、cache hit/miss、compaction/admission 统计；
- 失败分类和运行/任务级成本口径；
- 任务接纳、停止规则、rerun 和有效性门槛；
- 是否值得进入更大实验，以及要先修哪一个工程瓶颈。

# 相关工作定位：本项目的发现在已有研究里处在什么位置

日期：2026-11-07。只做文献检索和阅读，没有调用任何模型。

这份文件回答三件事：本项目六条发现（F1–F6）和已有研究是一致还是相反；下一轮实验该问什么；对外介绍时哪些话能说、哪些不能说。它不是综述，只收和这六条直接相关的文献。

**阅读程度的标法。** 每条文献都标了读到什么程度：

- **全文**：正文、局限和与本文相关的附录都读了。
- **相关章节**：读了摘要、方法和与本文相关的结果、局限章节，其余略过。
- **摘要**：只读了摘要，或检索结果里摘出的片段。

只读到摘要的文献，本文不下“对方没做过某事”的结论。找不到相关工作时写“在已读文献里没有找到”，不写“首次”。文献清单和每篇的阅读程度见第 6 节。

**“松绑”实验的含义。** 本文把它理解为：放宽本项目 harness 现有的硬约束，改用没被污染的新题再测一次。现有约束是：遮蔽窗口 `keep_recent=4`；准入上限 96,000 `calibrated_v1` token，约合 4 万 provider token；每次运行最多 24 或 40 次模型调用；单次花费 1 元上限；thinking 关闭。

## 1. 结论

- **F1（遮蔽破坏前缀缓存）的机制别人已经测到过，不能当作新发现讲。** The Physical Cost of Context 在自建 GPU 上实测过：保留最近 5 条观察的滑动遮蔽，让未命中缓存的 prefill 增加 608.5%。Break Later or Never、TokenPilot、Beyond Token Savings 也都讨论过遮蔽和缓存的冲突。本项目能补的是三点：
  - 在 API 计价下（DeepSeek 命中价是未命中价的 1/50），对 1,872 次真实运行做了离线重放；
  - 窗口从 4 加宽到 8，费用反而上升；
  - 规则 C（文件写入之前保护它的读取结果）这一种按“是否过时”而不是按“新旧”遮蔽的规则。
- **F2（重复读集中在遮蔽之后）方向上有人提过，但在已读文献里没有找到同样的计时测量。** Same Model, Different Harness 的检测器会盯“反复读、不修改”。Empirical Study 发现专门的取回工具几乎没人用。“重复读发生在原文滑出窗口之后几次调用”这种按遮蔽时刻的计时分析，在已读文献里没有找到。
- **F3（定位后不动手）和已有的“分析瘫痪”“定位了但改错”不完全是同一件事。** 已读文献里的失败，多是“找到了文件但改错了地方”（TRAJEVAL、Beyond Resolution Rates），或者推理模型的过度规划（Overthinking）。本项目的失败是关着 thinking、几乎不输出文字、到预算用完都没有发出一次修改。
- **F4（定位不是瓶颈）和多篇轨迹分析一致：** 失败的运行大多也找到了正确文件。和 Physical Cost 的预取结果方向不一致，条件差别很大，见第 2.4 节。
- **F5（记忆无增益）和文献量级吻合。** SWE-bench 上跨任务记忆的提升大多是 +3 到 +5 个百分点，本项目的样本量检不出这么小的效应。
- **F6（评测管线问题）和今年的多份审计一致。** 隐藏测试进入工作区、判分环境有缺陷、成本遥测口径错，同类问题 OpenAI、SWE-Bench Pro Verified、Harness or Model? 都报告过。本项目可讲的是自己查出、公开勘误的做法，不是问题本身新。

## 2. 逐条比对

### 2.1 F1：滑动遮蔽破坏前缀缓存

本项目的结论来自 [CONTEXT-SIM.zh-CN.md](CONTEXT-SIM.zh-CN.md)：
- **数据：** 离线重放 1,872 次运行，53,976 步，规则 A 能精确复现。
- **决策组 CR-2 n（275 次运行）的费用变化：** 窗口加宽到 8（B），输入中位只多 18.1%，按缓存估算的费用中位多 58.2%；规则 C 为 −5.0%；完全不遮蔽（N）为 −45.3%。
- **原因：** 每滑动一次就替换一条消息，后面的内容全部失去缓存，窗口越宽，失效的那段越长。
- **决策：** 按预注册阈值 T1–T4，没有候选通过，不推荐任何规则。

| 相关工作 | 一致还是相反 | 本项目多出的部分 | 阅读程度 |
| --- | --- | --- | --- |
| The Physical Cost of Context（arXiv 2610.34763） | **一致，而且是实测。** 在 SWE-bench Verified 100 题上，Mask-5（只留最近 5 条观察）让 prompt 总量减少 33.3%，但未命中的 prefill 增加 608.5%；缓存命中占比从约 96% 降到 53%。解出 26/100 对 29/100，配对差 −3.0，下界 −8.6，没过 −5 个百分点的非劣界。作者给出盈亏点：命中比例 ρ 超过约 0.55 时，遮蔽反而增加 prefill。局限里写明窗口扫描没有测。 | 它衡量的是本地 SGLang 的物理 prefill 和能耗，不是 API 账单。本项目用 API 价格计费，扫了窗口宽度（4 对 8），还比较了规则 C。本项目是离线重放，它是实测，这一点它更强。 | 相关章节 |
| Break Later or Never（Cim 等，figshare 预印本） | **一致。** 不遮蔽的轨迹前缀复用率 98–99%；“批量遮蔽”（每隔几步才遮一次）能保住 95–98% 的复用，同时把上下文长度最多降一半。另提出在 KV 层丢弃旧观察的 ObsDrop。 | 本项目没有测批量遮蔽，CONTEXT-SIM 把它列为后续候选。 | 摘要（论文页面无法访问，另读了作者代码仓库的 README） |
| TokenPilot（arXiv 2606.17016） | **一致。** 文本裁剪会改变布局，造成前缀失配和缓存失效；它的做法是稳定前缀，并按批次保守地驱逐。 | 它的任务是 PinchBench 和 Claw-Eval，不是 SWE 修复。表 7 有一个“最近 3 轮读过、之后没被覆盖就保留”的留存信号，是已读文献里离规则 C 最近的设计，但只作为对比信号，窗口也只有 3 轮。 | 相关章节（摘要、方法和表 7 段落） |
| Beyond Token Savings（arXiv 2609.32961） | **一致。** 经常改写轨迹的策略，token 变少了，账单却可能持平甚至更高。 | 它的计费是假设缓存价为原价的 1/10，再从相邻请求大小倒推出来的估算；Qwen 服务时关了前缀缓存。本项目的估算对照过 provider 实际用量（估算是实际的 0.92），价格比是 1/50。 | 相关章节（摘要、计费段和附录估算方法） |
| The Complexity Trap（Lindenbauer 等，NeurIPS 2025 DL4Code 研讨会；arXiv 2508.21433） | **结论方向看上去相反，但条件不同，不能直接判为矛盾。** 它发现遮蔽（M=10）比不管理上下文便宜一半以上。 | 见下面三点。 | 全文（JetBrains 页面正文，另读 arXiv 版附录 A、D） |

Complexity Trap 是计划里要求重点核实的一篇，这里单独写清楚它和本项目差在哪：

1. **费用怎么算。** 附录 A 写明：Gemini 用 Vertex AI 接口返回的费用；自建的 Qwen 模型，按观测到的每轮 token 用量和阿里云官方价目事后折算。作者特别说明 Qwen3-32B 的官方价目不区分缓存命中和未命中，所以这组费用偏高。Qwen3-Coder-480B 的价目区不区分命中，文中没有说明。
2. **有没有考虑缓存。** 考虑了，但不是主变量。第 5.2 节说，摘要调用每次处理的都是不同的轮次，几乎吃不到缓存，而 Gemini 等接口的命中价最多便宜 10 倍。附录 D 说，滑动遮蔽“缓存表现更差”。消融里，朴素超参数的混合策略因为“KV 缓存低效叠加”反而更贵。
3. **轨迹长度和窗口。** 它的回合上限是 250，窗口 M=10，模型窗口 122K–1M。本项目每次运行只有 24–40 次调用，窗口 4。轨迹越长，不管理上下文的代价越大。本项目的 N 更便宜，可能主要因为轨迹短、命中价比低。这是推测，没有验证。

另外，它的局限一节写到，固定窗口不区分观察是否过时，原文举的例子是“文件被修改后仍保留它的旧内容”。规则 C 正是按“文件是否已被写入”来决定能不能遮蔽的一种做法。在已读文献里，没有找到在 SWE 修复任务上评估这种规则、并且按缓存计价的研究。

### 2.2 F2：重复读占 15–40%，大多发生在原文被遮蔽之后

本项目的结论来自 [REREAD-AND-PROMPT-TIMING.zh-CN.md](REREAD-AND-PROMPT-TIMING.zh-CN.md)：
- **重复读的占比：** A5 15.5%，A6-R 30.6%，CR-2 40.3%，P4 31.3%。
- **发生在遮蔽之后：** 85–94% 的重复读发生在原文被遮蔽之后。
- **计时：** 距原文 1–4 次调用的只占 0.3–8%，距 5–6 次调用的占 42–56%，正好是原文滑出 4 条窗口之后。
- **和成败的关系：** 能被运行长度解释。
- **人工复核：** 留出集上误报 0/32，漏报 1/24。

| 相关工作 | 一致还是相反 | 本项目多出的部分 | 阅读程度 |
| --- | --- | --- | --- |
| Same Model, Different Harness（Lewis，arXiv 2608.26218） | **一致。** 它的检测器专门识别“反复读同一份材料而不修改”，发现后插入固定提醒。第 7.3 节建议缓存只读结果：源文件哈希没变时，只返回一个简短引用。 | 它没有单独估计检测器的作用，第 7.1 节把这列为下一步。本项目测的是重复读出现在遮蔽之后的第几次调用。 | 相关章节（正文、局限和部分附录） |
| An Empirical Study of Harness Design for Coding Agents（Fan 等，arXiv 2609.20804） | **互补。** 能把被遮蔽内容取回来的 `recall_event` 工具很少有人用：64 个设置里有 36 个从没调用过，中位调用率为 0；有没有这个工具，准确率差 −0.36 个百分点。 | 本项目看到的是另一面：模型不用专门工具，而是直接再调一次 `read_file`。两者合起来说明，被遮蔽的内容模型确实还要，只是用普通工具去拿。这个解读是推测。 | 相关章节 |
| Empirical Cost Attribution of Context-Compression Gateways（arXiv 2609.22114） | **一致。** 被压缩的内容每取回一次，就把省下的那段字节重新发一次。 | 它测的是生产网关的账单拆分；本项目测的是重复读的计时。 | 摘要 |
| The Scaffold Effect in Coding Agents（Vats、Golev，arXiv 2607.22585） | **方向一致。** 不同 harness 的“无动作回合”（既不改文件、也不发新命令）相差 10 倍，作者认为这是 harness 的属性，换模型也复现。原文举例“把同一个文件读三遍”。 | 它没有单独统计重复读。 | 全文 |

### 2.3 F3：失败的主因是“定位了但不动手”

本项目的结论来自 [P4-NO-EDIT.zh-CN.md](P4-NO-EDIT.zh-CN.md)：
- **数据：** 53 次有效运行，9 次通过，44 次失败。
- **分类：** 44 次失败里 43 次是找到了位置却没修改，1 次是上下文溢出；0 次因预算，0 次因没找到。人工抽 12 次，全部一致。
- **定位之后：** 定位的中位数在第 3 次请求，之后又发了中位 16 次请求，其中 45.6% 是重复读。通过的运行从定位到第一次修改只用 2–11 次请求。
- **行动提示：** 换了时机也没用。I 条件 15 次失败里 14 次仍然没修改。
- **背景：** thinking 关闭，44 次里 34 次几乎没有文字输出。

| 相关工作 | 一致还是相反 | 本项目多出的部分 | 阅读程度 |
| --- | --- | --- | --- |
| The Danger of Overthinking（Cuadron 等，arXiv 2502.08235） | **部分一致。** 它在 SWE-bench Verified 的 4,018 条轨迹里定义了“分析瘫痪”：花大量篇幅规划，环境上几乎没有进展。推理模型更严重。 | 机制不同：它说的是推理模型想得太多；本项目关了 thinking，失败运行几乎没有文字，表现为反复读文件而不是长篇规划。 | 相关章节（摘要和第 3 节定义） |
| TRAJEVAL（arXiv 2603.24631） | **相邻而不同。** GPT-5 能找到相关代码，但修改的位置不对（edit recall 低）。实时反馈让两个模型提升 2.2–4.6 个百分点。 | 本项目的失败是根本没有修改，不是改错位置。 | 摘要 |
| Beyond Resolution Rates（Mehtiyev、Assunção，arXiv 2604.02547） | **相邻而不同。** 12 道“简单却从没被解出”的题，最强 agent 12 题都找到了标准补丁所在的文件，10 题改了它，但改在症状层而不是根因层。 | 同上：它的失败发生在“改了，但改错层”。 | 相关章节（RQ1） |
| Same Model, Different Harness | **不矛盾。** 检测器加干预的整包设置，在窄窗口下提升明显（Verified 20,480 窗口：F2PF 28% → 49%，完整解出 43 → 72）；在 262,144 窗口下两组接近，F2PF 差 −0.3 [−4.5, +3.9]。 | 它的提升来自整包，无法归到检测器提醒本身。本项目只测提示时机，结果是零，和“单靠提醒未被证明有效”相容。 | 相关章节 |
| Beyond the Model（Hu 等，arXiv 2609.32459） | **相邻。** 在 ProgramBench 上归纳出“过度探测”和“探测不足”两种失败。好的 harness 两头都能纠正。 | 任务是从零生成仓库，不是修 issue。 | 相关章节 |

在已读文献里，没有找到把“定位后不修改”从“改错位置”里单独分出来，并统计它占失败多少比例的研究。

### 2.4 F4：定位不是瓶颈

本项目的结论来自 [../retrieval/CR-2-RESULTS.zh-CN.md](../retrieval/CR-2-RESULTS.zh-CN.md) 和 [RETRIEVAL-QUERIES.zh-CN.md](RETRIEVAL-QUERIES.zh-CN.md)：
- **设计：** 56 题 × 5 次重复，按题做 bootstrap。
- **直接告诉模型正确文件（O 组）：** 到第一次碰到正确文件的步数减少 2.42 [−3.85, −1.14]；修复率 O − N 为 +2.1 [−5.4, 9.6]，未检出差异。
- **预取（P 组）：** 到第一次碰到正确文件的步数，P − N 为 −0.02 [−1.87, 2.05]。
- **事后分析：** 预取命中时省 7.96 步，没命中时多花 3.45 步，两者相抵。

| 相关工作 | 一致还是相反 | 本项目多出的部分 | 阅读程度 |
| --- | --- | --- | --- |
| Understanding Code Agent Behaviour（arXiv 2511.00197） | **一致。** 失败的轨迹里，72–81% 也找到了出问题的文件，问题出在更深的层面。 | 本项目直接把正确文件给模型，做了干预实验，不只是观察。 | 摘要（另读了检索摘录里的定位段落） |
| Beyond Resolution Rates | **一致。** 见 2.3 节，12/12 找到了文件。 | 同上。 | 相关章节 |
| TRAJEVAL | **部分一致。** GPT-5 定位没问题；Qwen-32B 连文件都找不到。说明“定位是不是瓶颈”取决于模型。 | 本项目只有一个模型，不能推广到弱模型。 | 摘要 |
| The Physical Cost of Context | **方向不一致，条件差别大。** 在 SWE-rebench V2 派生的定位任务上，预先塞入 10 个检索片段（Eager），模型调用比按需搜索少 35.7%，通过了质量门槛。 | 两边的终点不同：它测的是“到定位终点”的调用数，本项目测的是“第一次碰到正确文件”的步数和最终修复率。检索器、模型、题集也都不同。本项目的事后分析显示命中时同样省步，只是没命中时把省下的抵掉了。 | 相关章节 |

### 2.5 F5：记忆没有检出增益

本项目的结论如下：
- **跨任务记忆（A5）：** B2 − B0 为 −1.8 [−8.3, +4.8]，p = 0.72。出处是 coding-agent `docs/A5-FINAL-REPORT.zh-CN.md`。
- **同题失败记录（A6-R）：** R1 − R0 为 −6.6 [−17.7, +3.8]，p = 0.29。出处是 coding-agent `docs/sprint/A6-R-RESULTS.zh-CN.md`。
- **锚定：** 事后分析发现，带记录的第二次尝试更常停在和第一次几乎相同的改法上，diff 相似度中位 0.873 对 0.658。这是看过数据后的机制假设，不是预注册结论。

| 相关工作 | 一致还是相反 | 本项目多出的部分 | 阅读程度 |
| --- | --- | --- | --- |
| ReasoningBank（arXiv 2509.25140） | **一致。** SWE-bench Verified 上跨任务记忆提升约 +3.4 到 +4.6 个百分点。存原始轨迹的 Synapse 在强模型上是 −0.6。 | 本项目的卡片也是规则提取、不经 LLM 提炼，结果落在“原始轨迹作用很小”这一侧。 | 沿用本项目此前的阅读记录：正文 |
| SWE-Exp（arXiv 2507.23361） | **一致。** 跨仓库经验 +3.8 个百分点。正文 6.2 节说，让模型显式引用经验会导致过度依赖。 | 和 A6-R 的锚定假设方向一致。 | 沿用此前记录：v1 正文 |
| Agent KB（arXiv 2507.06229） | **一致。** pass@1 约 +3 到 +4 个百分点；大的提升来自“失败后重试”的设置。 | A6-R 测的正是同题重试，在 1 比特反馈下没有检出收益。 | 沿用此前记录：正文 |

这三篇的数字和未核实项，见 coding-agent `docs/memory-v2/DIAGNOSIS-AND-LITERATURE.zh-CN.md` 第 2 节；本次没有重读。该文第 6 节算过：要检出 4 个百分点的效应，按 A5 的离散度约需 600 道题。所以本项目只能说“排除了大于约 5 个百分点的提升”，不能说“记忆无效”。

### 2.6 F6：评测管线自身的问题

本项目的问题和出处如下：
- **隐藏测试可见：** dev 集镜像是用“原始代码 + 测试补丁”构建的，agent 的工作区里能看到隐藏测试，并且实际搜到、运行过。出处是 [../sprint/AUDIT-3-CORRECTION.zh-CN.md](../sprint/AUDIT-3-CORRECTION.zh-CN.md)。
- **停止标签错误：** 11 次 `model_parse_error` 实际是单次 1 元上限拒绝了下一次预留。4 次 `context_overflow` 是 harness 按字节估算的准入拦截，模型窗口并没有满。
- **未声明的组间不对称：** 这两项都更容易打到带记录的 R1 组。出处是 A6-R-RESULTS 和 [../sprint/AUDIT-A6-3.zh-CN.md](../sprint/AUDIT-A6-3.zh-CN.md) 的“勘误（2026-11）”。

| 相关工作 | 一致还是相反 | 本项目多出的部分 | 阅读程度 |
| --- | --- | --- | --- |
| OpenAI：Why SWE-bench Verified no longer measures frontier coding capabilities（2026-02-23） | **一致。** 审计了 138 道 o3 64 次都没稳定解出的题，59.4% 的测试或描述有实质问题。所有被测前沿模型都能复述部分题的标准补丁或题面细节。OpenAI 已停止报告这个分数，建议改用 SWE-bench Pro。 | 本项目的 A5、CR-2 都出自这个集合，绝对解出率不能当能力指标（见第 3 节）。 | 全文 |
| SWE-Bench Pro Verified（arXiv 2609.08149） | **一致。** 把隐藏测试从工作区删掉、重建单提交仓库、屏蔽线上来源之后，部分模型的分数明显下降。 | 本项目的隐藏测试泄漏来自镜像构建，属于它说的“本地隐藏信息”这一类。 | 摘要（另读了检索摘录里的“测试制品隐藏”段落） |
| Shortcutting the Fix（arXiv 2609.06780） | **一致。** 标准提示下，SWE-bench Multilingual 上的利用率达 45.1%–82.4%，“本地隐藏信息”是其中一类。 | 本项目的日志里确有 agent 运行 F2P 测试的记录，但没有按这篇的分类统计。 | 摘要 |
| SWE-Bench Pro V2（arXiv 2610.24206） | **一致。** 复查 731 道公开题，修复或下线了 480 道（66%），其中最多的是判分或环境缺陷。规定判分器不在 agent 的沙箱里运行。 | 本项目的 grader 在同一镜像里覆盖测试补丁再判，和它的“干净镜像重判”思路相同。 | 摘要（检索摘录） |
| Harness or Model?（Arjmandi，arXiv 2609.11987，2026-09-08 修订版） | **一致，而且最接近。** 作者自己的遥测把一种 SDK 的缓存 token 口径套到所有 harness 上，缓存被重复计算，原稿的成本结论因此作废。第 5.4 节说，研究截断设置的实验必须逐次记录策略是否真的触发了，否则不变的结果无法解释。 | 本项目的停止标签错误也是“被记成别的原因”。CONTEXT-SIM 逐次重放、确认规则 A 精确复现，正是它要求的触发记录。 | 相关章节（摘要、引言和 §5.1–5.4） |
| The Scaffold Effect in Coding Agents | **一致。** 局限里承认回合预算不对称：OpenCode 无法设回合上限，只受墙钟限制。 | 本项目的不对称是同一 harness 内两组之间，更隐蔽。 | 全文 |

## 3. 方法对照

| 研究 | 任务集 | 模型 | 窗口和预算 | 主要指标 | 成本是否按缓存计价 |
| --- | --- | --- | --- | --- | --- |
| The Complexity Trap | SWE-bench Verified 500；OpenHands 和混合策略用 50 题切片 | Qwen3-32B（含 thinking）、Qwen3-Coder-480B、Gemini 2.5 Flash（含 thinking） | 模型窗口 122K / 256K / 1M；250 回合；遮蔽 M=10 | 解出率、每题美元成本，带 bootstrap 区间 | Gemini 用接口返回的费用；Qwen 按价目事后折算，Qwen3-32B 价目不区分命中 |
| Same Model, Different Harness | Verified 169 题、SWE-bench Pro 316 题、FeatureBench 183 题 | Qwen3.6-35B-A3B 4-bit 本地部署，另 3 个开源模型 | 20,480 / 43,008 / 262,144；480 秒；每题每组 1 条贪心轨迹 | F2PF、完整解出 | 不涉及金额，只报每轮 prompt token |
| The Scaffold Effect | Terminal-Bench Pro 50 题 | Qwen 3.6 Plus、MiniMax M2.5（OpenRouter） | 40 回合（OpenCode 无上限）；900 秒 | 每解出一题的 token、无动作回合 | 否：`tokens_total` 把缓存 token 和其他 token 直接相加 |
| Empirical Study of Harness Design | Verified 500、Terminal-Bench 2.1 89 题 | Nemotron-3 30B / 120B / 550B、Mistral-Medium-3.5 | 32k / 64k / 96k / 128k；300 步；每题 1 次 | 解出率、每题成本 | 否：按 OpenRouter 输入、输出两档定价，没有缓存价 |
| Harness or Model? | 私有 256 题（每组对比 80 题） | Opus 4.8、GPT-5.5，另有 gemini-3.5-flash、deepseek-v3.2 | 墙钟上限 | 解出率、每解出一题成本 | 是：按未缓存输入、缓存读、缓存写、输出四项重新计价 |
| Beyond the Model | SWE-bench Pro 300 题、ProgramBench、GitTaskBench | Qwen、DeepSeek 各 5 个 | 250 或 300 步 | 得分、prompt token | 已读部分只报 prompt token，没有看到缓存计价 |
| The Physical Cost of Context | SWE-rebench V2 派生 450 个定位任务；Verified 100 题 | 本地 SGLang 部署 | 32 次调用；服务端上限 65,536 | 解出率（−5 个百分点非劣界）、未缓存 prefill、能耗 | 计入物理 prefill 和设备命中，不是 API 账单 |
| **本项目** | SWE-bench Verified 子集：A5 和 CR-2 各 56 题；P4 是 6 道 sphinx 仓库任务（任务包不含标准补丁） | deepseek-flash，thinking 关闭 | 准入 96,000 `calibrated_v1`（约 4 万 provider token）；24–40 次调用；单次 1 元；`keep_recent=4` | 修复率，按题配对 bootstrap，预注册阈值；离线重放的输入量、缓存估算费用、准入 | 是：按 DeepSeek 命中、未命中、输出三档价格估算，估算为 provider 实际的 0.92 |

**本项目方法上的短板：**

1. **单一模型，单一价格。** 对照的研究多数用了 2–10 个模型。F1 的费用结论依赖 1/50 的价格比；按 Physical Cost 的盈亏点公式，命中比例低到一定程度时结论会反过来。F3 的“不动手”可能和 thinking 关闭有关，本项目没有测 thinking 打开的情况。
2. **F1 是离线重放，假设换了规则轨迹不变。** Complexity Trap 观察到策略会改变轨迹长度（LLM 摘要让 Gemini 的平均回合从 44 增加到 52）。Physical Cost 的 Mask-5 让调用数多了 2.6%。本项目的费用估算偏向哪个方向，不知道。
3. **公开题被污染。** A5、A6-R、CR-2 都出自 SWE-bench Verified；P4 的 6 道任务也是公开仓库的历史 issue。两组用的是同一个模型，配对比较不会因分组而改变模型见过多少题（Same Model 第 6.4 节的论证），但污染和处理之间有没有交互，测不出来。绝对解出率（如 A5 的 68.5%）不能当能力指标。
4. **样本量小。** 56 题的配对区间半宽约 6.5–7.5 个百分点（A5 [−8.3, +4.8]，CR-2 [−5.4, 9.6]），检不出 3–5 个百分点的效应。
5. **轨迹短，预算紧。** 本项目 24–40 次调用，对照研究多是 250–300 步。上下文管理的作用随轨迹变长而变大，所以 F1、F2 的结论只适用于短轨迹。
6. **准入估计偏保守。** `calibrated_v1` 约是 provider token 的 2.2 倍，而 DeepSeek 官方文档写的模型窗口是 1M。本项目实际上把自己放进了“窄窗口”区间，而 Empirical Study 和 Same Model 都显示，上下文管理的好处主要出现在窄窗口。

**本项目方法上可以讲的地方：**
- 主要实验都预注册了阈值，每题重复 3–5 次，按题配对 bootstrap。Same Model、Empirical Study、Physical Cost 都是每题每组 1 条轨迹。
- 离线重放先验证能精确复现现行规则，再比较其他规则。
- 查出的错误都以勘误节的形式公开，原文不改。

## 4. 对“松绑”实验的建议

### 4.1 别人已经验证过、不必再测的

- **“窗口紧时上下文管理有用，窗口宽时作用消失”。** Empirical Study 测了 4 个窗口、4 个模型：管理和不管理的差距从 32k 的 35.7 个百分点降到 128k 的 2.7 个百分点，主要来自避免溢出。Same Model 在 262,144 窗口下两组接近。
- **“滑动遮蔽破坏前缀缓存”这个机制本身。** Physical Cost 有实测，Break Later or Never 和 TokenPilot 有工程方案。不必再证明，只需在新实验里当作成本护栏来监测。
- **“遮蔽对比 LLM 摘要”。** Complexity Trap 测过：摘要没有稳定优于遮蔽。
- **专门的取回工具。** Empirical Study 显示几乎没人用，准确率也不提升。
- **跨任务记忆。** 文献量级是 3–5 个百分点，本项目的规模检不出，事先就能算出来（见 2.5 节）。
- **只换 harness 带来的成本差异。** Scaffold Effect 测到每解出一题的 token 相差 40 倍，而成功率只差 0–8 个百分点。

### 4.2 最值得问的问题

**建议的主问题：** 在没有被污染的新题上，用同一个 API 计价的模型，把遮蔽从“滑动 4 条”放宽到“不遮蔽”或“规则 C”，同时把准入改成按 provider token 计算。provider 实际账单（含缓存命中）和修复率各变多少？

理由：

1. **机制已被公认，结果好解释。** 不论账单是升是降，都能用已发表的盈亏点公式解释，不会被质疑为偶然。
2. **已读文献里有拼图但没有拼起来。** 在已读文献里，没有找到同时满足以下四点的研究：API 实际账单、命中价远低于未命中价（1/50）、换规则后真实重跑（轨迹允许变化）、修复率带区间。
   - Physical Cost 是实测，但用的是本地 GPU，不是账单；
   - Beyond Token Savings 用的是假设价格的估算；
   - Complexity Trap 的主变量不是缓存；
   - Harness or Model? 计入了缓存，但比较的是 harness，不是上下文规则。
3. **正好补上本项目最大的短板。** F1 现在只是离线反事实，真实重跑能检验“轨迹不变”这个假设。
4. **成本效应大，容易检出。** 离线估计 N 比 A 便宜约 45%，比修复率上可能出现的几个百分点大得多。修复率只做非劣护栏。

**设计上要事先定好的几点**（都来自上面文献踩过的坑）：

- **成本只用 provider 返回的实际用量计算**，命中、未命中、输出分开记，并和账户扣费对一次账（Harness or Model? 第 5.1 节的教训）。
- **逐次记录规则是否触发**：每次请求记录遮蔽是否发生、替换了哪条消息、命中 token 数。否则“结果没变”无法解释（Harness or Model? 第 5.4 节）。
- **准入先按 provider token 校准**，否则 C、N 的超限主要反映估计方法的偏差（CONTEXT-SIM 局限一节）。
- **非劣界要按题量定。** 按 A5 的离散度，56 题的配对区间半宽约 6.5 个百分点。要在真实差为 0 时把下界压到 −5 以内，大约需要 56 ×（6.5 / 5）² ≈ 95 题。这是粗算，正式设计时应按预实验的离散度重算。
- **单次花费上限要对两组一样“宽松”**，或者按组声明，避免再出现 A6-R 那种上限只打到一组的情况。
- **题集要等新题集的接入评估出结果再定。**

**备选问题**：把“定位后不动手”的检测器提醒单独拿出来测。Same Model 第 7.1 节明确把它列为未做的下一步，而本项目已经有分类器和 43/44 的基线。不排第一的原因有两个：本项目的提示时机实验已经是零结果；而且这个问题更依赖模型（thinking 开关），对外说服力不如成本问题。

## 5. 面试可用的表述

下面每句都标了出处，数字以出处文件为准。

1. “我们在 DeepSeek 的 API 价格下，离线重放了 1,872 次 agent 运行。把遮蔽窗口从 4 条加宽到 8 条，输入只多 18%，按缓存估算的费用中位数却多了 58%，因为每次窗口滑动都让后面的前缀失去缓存。这个机制别人在自建 GPU 上也实测到过，我们补的是 API 计价下的数字。”
   - 出处：[CONTEXT-SIM.zh-CN.md](CONTEXT-SIM.zh-CN.md)；The Physical Cost of Context。
2. “失败的运行里，44 次有 43 次是已经找到要改的位置，却到预算用完都没动手。中途插入的行动提示，换了几种时机都没有改变这一点。”
   - 出处：[P4-NO-EDIT.zh-CN.md](P4-NO-EDIT.zh-CN.md)。
3. “直接把正确文件告诉模型，它更快碰到正确文件，但修复率和对照组的差在 −5.4 到 +9.6 个百分点之间，没有检出差异。所以在我们的设置里，定位不是主要瓶颈。这和几篇轨迹分析的观察一致。”
   - 出处：[../retrieval/CR-2-RESULTS.zh-CN.md](../retrieval/CR-2-RESULTS.zh-CN.md)；Understanding Code Agent Behaviour；Beyond Resolution Rates。
4. “两个预注册的记忆实验都没检出收益，区间上限分别是 +4.8 和 +3.8 个百分点，能排除文献报告量级以上的提升。”
   - 出处：coding-agent `docs/A5-FINAL-REPORT.zh-CN.md`、`docs/sprint/A6-R-RESULTS.zh-CN.md`；第 2.5 节的文献。
5. “我们自己的评测管线出过错：镜像构建时把测试补丁带进了 agent 的工作区。审计发现后，我们把那批结果降级为‘测试可见’条件，重建了干净镜像，并公开勘误。同类问题 OpenAI 和 SWE-Bench Pro Verified 今年也报告过。”
   - 出处：[../sprint/AUDIT-3-CORRECTION.zh-CN.md](../sprint/AUDIT-3-CORRECTION.zh-CN.md)；OpenAI 文章；SWE-Bench Pro Verified。

### 不能说的话

- **不能说“我们首次发现遮蔽会破坏前缀缓存”**，也不能说任何“首次”。已读文献里至少有四篇讨论过这个机制。
- **不能说“取消遮蔽能省 45%”。** 这是离线反事实，假设轨迹不变，只有一个模型、一个价格；而且 N 有 163 个请求超过准入上限。
- **不能说“规则 C 更好”或“我们推荐规则 C”。** 它没过预注册的 T2 和 T4，结论是不推荐任何规则。
- **不能说“检索没用”或“告诉模型正确文件没用”。** 只能说修复率上“未检出差异”，区间很宽。
- **不能说“记忆有害”或“已证实锚定效应”。** A6-R 的区间包含 0，锚定是事后提出的假设。
- **不能把行动提示无效说成普遍规律。** 只有一个模型、thinking 关闭、53 次运行。
- **不能把 A5 的 68.5% 等绝对解出率当成能力指标**，也不能和排行榜比。题集被污染，预算也远小于排行榜设置。
- **不能说“Complexity Trap 没考虑缓存”。** 它的第 5.2 节和附录 D 都讨论了缓存，Gemini 的费用也是接口实际返回的。
- **不能说“我们的 harness 比某个 harness 好”。** 本项目没有做跨 harness 比较。

## 6. 文献清单

| # | 文献 | 链接 | 阅读程度 |
| --- | --- | --- | --- |
| 1 | Lindenbauer 等，The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management | https://kairos.jetbrains.com/research/articles/the-complexity-trap ；arXiv 2508.21433 | 全文 |
| 2 | Lewis，Same Model, Different Harness: Different Coding-Agent Results | https://arxiv.org/html/2608.26218 | 相关章节 |
| 3 | Vats、Golev，The Scaffold Effect in Coding Agents | https://arxiv.org/abs/2607.22585 | 全文 |
| 4 | Fan 等，An Empirical Study of Harness Design for Coding Agents | https://arxiv.org/abs/2609.20804 | 相关章节 |
| 5 | Arjmandi，Harness or Model? Isolating the Harness Effect in Agentic Coding with a Contamination-Controlled Private Suite（2026-09-08 修订版） | http://arxiv.org/abs/2609.11987 | 相关章节 |
| 6 | Hu 等，Beyond the Model: Demystifying Harness Effects in Software Engineering Agents | https://arxiv.org/abs/2609.32459 | 相关章节 |
| 7 | OpenAI，Why SWE-bench Verified no longer measures frontier coding capabilities | https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ | 全文 |
| 8 | The Physical Cost of Context: Quality-Constrained Resource Accounting for Coding Agents | https://arxiv.org/abs/2610.34763 | 相关章节 |
| 9 | Cim、Topcu、Kandemir，Break Later or Never: Prefix-Preserving Context Compaction for Coding Agents | https://doi.org/10.6084/m9.figshare.34791231 | 摘要（另读代码仓库 README） |
| 10 | TokenPilot: Cache-Efficient Context Management for LLM Agents | https://arxiv.org/html/2606.17016v2 | 相关章节 |
| 11 | Beyond Token Savings: A Systematic Study of Context Compression in LLM Agents | https://arxiv.org/abs/2609.32961 | 相关章节 |
| 12 | An Empirical Cost Attribution of Context-Compression Gateways in Multi-Turn Coding Agents | https://arxiv.org/abs/2609.22114 | 摘要 |
| 13 | Cuadron 等，The Danger of Overthinking: Examining the Reasoning-Action Dilemma in Agentic Tasks | https://arxiv.org/abs/2502.08235 | 相关章节 |
| 14 | TRAJEVAL: Decomposing Code Agent Trajectories for Fine-Grained Diagnosis | https://arxiv.org/abs/2603.24631 | 摘要 |
| 15 | Mehtiyev、Assunção，Beyond Resolution Rates: Behavioral Drivers of Coding Agent Success and Failure | https://arxiv.org/html/2604.02547v1 | 相关章节 |
| 16 | Understanding Code Agent Behaviour: An Empirical Study of Success and Failure Trajectories | https://arxiv.org/html/2511.00197v1 | 摘要 |
| 17 | SWE-Bench Pro Verified: A Reliable Benchmark for Software Engineering Agents | https://arxiv.org/html/2609.08149 | 摘要 |
| 18 | Shortcutting the Fix: Identifying and Categorizing Agentic Exploits in Software Engineering Benchmarks | https://arxiv.org/html/2609.06780v1 | 摘要 |
| 19 | SWE-Bench Pro V2: A Cleaner, Harder-to-Game Coding Benchmark | https://arxiv.org/html/2610.24206 | 摘要 |
| 20 | ReasoningBank、SWE-Exp、Agent KB | 见 coding-agent `docs/memory-v2/DIAGNOSIS-AND-LITERATURE.zh-CN.md` 第 2、7 节 | 沿用此前的阅读记录，本次未重读 |

检索时看到、但没有读也没有引用的：What Resolve Rate Hides（arXiv 2607.06184）、You don't need long context（arXiv 2611.05306）、CacheWise（arXiv 2606.16824）、Penelope（arXiv 2611.00391）。“长窗口模型是否还需要遮蔽”这个问题，本文的依据是 Empirical Study 和 Same Model 的窗口扫描，没有用这几篇。

## 勘误

**2026-11-07，准入口径。** 开头“松绑”实验的说明和第 3 节对比表的“本项目”一行，都把本项目的准入写成“96,000 `calibrated_v1`（约 4 万 provider token）”。这是错的。

- `configs/sprint/b0-v2.yaml` 没有设 `admission_estimate`，按 schema 默认用的是 `utf8_bytes`，即把序列化字节数（加协议余量）当 token 数。
- 按字节的估计约是 provider token 的 2.5–5.2 倍（A6-R 实测 2.49–5.19）。96,000 的准入线减去 8192 的输出预留，约合 1.7 万–3.5 万 provider token，比原文写的更紧。
- 第 2、4 节据此得出的方向性判断（现行准入远低于模型窗口）不受影响。

松绑试点的 A 组按 b0-v2 原样沿用 `utf8_bytes`，见 `docs/analysis/UNLEASH-PILOT-DESIGN.zh-CN.md` 第 2.5 节。

# 共享契约差异与澄清提案 v0.1

2026-09-21 H5/A5 补充：harness 已保存受控外部注册、read/search dispatcher 和 `real-04` 真实联调证据；下段“暂拒绝外部插件/暂不派发”仅描述早期 H4。A5 新增本仓离线 DTO 和严格 host 收据审计，不改变 H4 wire 0.1、不宣布共享契约升级。requested/selected、prepared view、绑定修复 model operation/attempt 的 actually_injected 继续分开；没有收据不推定实际注入或模型采用。见 [A5 验收](A5-VERIFICATION.md)。

2026-09-21 实现对齐更新：已只读核对 H4 0.1 的实际源码并完成本仓 async adapter，详见 [H4-ADAPTER.md](H4-ADAPTER.md)。C02/C06 的实际限制是 message hash 无正文 resolver、proposals 暂不派发、RunSession 暂拒绝外部插件；宿主 prepared-view metadata 不能直接视作本仓 operation/attempt 注入收据。下文保留原设计提案，不据其早期状态否定本轮实现。

日期：2026-09-18。状态：coding-agent 单方设计提案，未生效、未发送其他任务、未修改共享原件或 coding-harness。基于原始 v0.1（未公开）；本轮没有读对方最新实现，以下对 harness 的影响是接口职责分析，不是对其现状的断言。

总体责任分配无异议：harness 是唯一在线预算权威、有序 run-event 写入器及执行/恢复主体；agent 管理策略和知识状态。本计划选择可选 async Protocol + JSON payload，首版无需 subprocess bridge。

## 需要确认的语义

| ID | 冲突/歧义与证据 | 建议、两侧影响、兼容方案 | 是否阻塞当前离线包 |
|---|---|---|---|
| C01 | 目标允许 diagnostic 使用 expected_files，但公共 TaskEnvelope 禁止字段不区分 mode；当前 TaskSpec 持有全部答案 | 公共 envelope 两种模式都不含 oracle；旧诊断走独立 OracleContext/legacy runner，不能嵌套入 PolicyInput。agent 新旧入口明确分开；harness 无需持有 OracleContext。将来诊断插件要用它须单独受控接口并排除 realistic | 否，TP01 按更严格公共边界实现 |
| C02 | task_family 是公开字段，却可能被填 skill_heavy/memory_heavy；旧 `seed_pair_task_id` 解析命名 | family 仅公开任务类型（例如 code_repair），id 仅关联，禁止解码任务名查表。agent 路由仅看公开 issue/观察；harness 透传不派生类别；evaluator 保留私有 tags。legacy 不迁移 category 作为 family | 否，新增非干扰 fixture |
| C03 | PolicyDecision 有候选和 requested，宿主可裁剪，但回传实际注入的字段/时点未定；旧 `MemorySelection.injected_count` 是选择数 | 每片段 candidate_id + memory_id/revision + content_hash；宿主下次 PolicyInput 回传 receipt（request/actual token、actual hash/range、reject reason、model operation/attempt 关联），以真实发出的请求确认 injected。agent 不自填 receipt。建议 optional minor 扩展，旧宿主无 receipt 时 actual/采用为 unknown | 否；真实归因/联调前需确定 |
| C04 | WorkspaceAdapter/ModelClientProtocol 同步，而宿主异步；契约只给 action proposal 语义 | `async decide(public_input)->decision`，纯决策提交 read/search/model proposals，宿主检查后执行、用 observation 反馈。proposal_id 本地关联，宿主分配 operation_id/attempt_id，插件不得发起 direct HTTP/subprocess 或嵌套 asyncio.run。legacy 同步 runner 保留在独立入口，异步 shim 不包装整个旧 GraphRunner | 否；A4 adapter 前冻结签名 |
| C05 | ArtifactRef 有 sensitivity，但公开/私有评测数据 ACL、代码读取与 grader 引用可见性未给具体枚举 | 建议区分 public_observation、policy_state、evaluation_private、quarantined；宿主 resolver 校验归属/权限；agent 只拿允许 ref 和 hash，非宿主任意绝对路径。private result 不回灌 builder。可先由 fixture profile 声明权限，枚举最终值联调前冻结 | 否；真实隔离验收依赖宿主 |
| C06 | 插件状态 restore 与外部 memory revision 漂移的关系未定义 | state 含 plugin/schema version、public_task_hash、repo_revision、memory_snapshot_id/hash、evidence refs、待 proposal IDs、上次 receipt cursor。restore 是纯状态恢复，不重放工具/重写 store；缺 snapshot/artifact 拒绝或 policy_error 降级，不能自动加载“最新”记忆。harness 负责顺序、锁及副作用恢复 | 否；A4 故障联调前确定 |
| C07 | 未知 minor 兼容与 realistic 禁止任意额外字段有张力 | wire decoder 识别版本和扩展槽、oracle 字段始终拒绝；映射到严格公开内部 DTO。未知 optional 字段可保留在不可注入审计或丢弃并记记录，不能自动流入 prompt。major 不兼容明确拒绝 | 否；TP01 做 fixture，升级为正式 schema 前同步 |
| C08 | RunResult/EvaluationResult、Usage 与现有 GraphMetrics/TestResult/默认 0 费用不等价 | 新 adapter 不把 public exit=0 映射 grader pass；unknown usage 用 null+status，全部模型 attempt 分类入账，legacy 估计数仅 legacy 字段。harness 输出规范 result，agent 加记忆专用维度，不覆盖结果语义 | 否；A4 集成映射必须测试 |

这些主要是 v0.1 的澄清和可选字段补齐，而不是已决定的破坏性改动。正式 schema 版本号由协调后确定；本计划示例内部 schema_version=1.0 不表示共享契约已升级 1.0。若对方已有不兼容实现，创建新版本 change 记录：旧/新字段映射、两仓受影响 adapter、弃用周期、双版本 fixture；不悄悄改字段含义。

## 现有能力到公共契约的映射

| 当前 agent | 目标 | 当前状态 |
|---|---|---|
| TaskSpec / AgentContext | TaskEnvelope / PolicyInput | legacy，需白名单新 DTO |
| SkillSelection / MemorySelection | PolicyDecision + receipt | 有候选/计数，缺版本/宿主实际注入确认 |
| TraceEvent/TraceWriter | 候选事件 → RunEvent | 无 schema/seq/operation/attempt；禁止用于新宿主独立落盘 |
| GraphMetrics.success / TestResult.passed | RunResult + EvaluationResult | public exit code 代理，不是独立 grader |
| ModelClientProtocol.complete_json | 宿主受控 model proposal | legacy direct HTTP，新路径禁止 |
| WorkspaceAdapter | 宿主受控 tool proposal/observation | 同步本地接口，不能证明取消/隔离 |
| MemoryHint/旧 store | agent 自有版本化 MemoryRecord/store | 部分来源，无 validity；与 runtime event store 分开 |

## 最小 fixture 套件提案

未来放本仓 `tests/fixtures/upgrade/contract-v1/`，每项保存 input/expected 和版本/hash；协调后两边采用相同副本，不创建跨仓内部 import。当前尚未创建这些执行 fixture。

| Fixture | agent 验证责任 | harness 验证责任 |
|---|---|---|
| F01 read→patch→public test→grader | proposals/来源和结果映射、公开/私有分开 | 真实模型/工具/artifact/独立验证完整关联 |
| F02 非法参数 | 不把 rejected 当 observation success | schema 拒绝且工具未执行 |
| F03 cancel 后无写 | 状态不再产生后续 proposal；能力 unavailable 不报成功 | 正在执行进程取消、清理保证 |
| F04 写后未落盘崩溃 | restore 不重发已确认/unknown 写；缺证据返回 unknown | reconcile/副作用控制，避免重复追加 |
| F05 valid→API drift invalid | record/理由/拒绝/receipt 关联 | requested/actually_injected 记录 |
| F06 oracle sentinel | DTO/export/index/prompt/memory source 无泄漏 | agent tools 实际读不到私有树 |
| F07 超长候选 | 配额/top-k/零条和无增强 fallback | 总 token/context 裁剪和真实注入收据 |
| F08 schema 版本 | known minor 投影和 unknown major 拒绝 | 两端同样兼容/拒绝规则 |

单仓通过 fixture 只证明各自责任，不自动证明 F03/F04/F06 的宿主保证；首次真实连接前各自提交“已支持/stub/未支持”矩阵及代码版本和环境，锁定相同 fixture hash。policy_error 可降级 B0，但结果标 method_degraded，不混入正常 B2/B3 收益。

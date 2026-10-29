# P2-B：公开验证服务交付记录

日期：2026-10-30（Asia/Shanghai）。实施基点：`d0bdf6f8302a8a8b65a806730724bd81e25a3375`。分支：`codex/p2-public-validation`。唯一写入工作区：`C:/Users/Administrator/.codex/worktrees/8571/coding-harness`。实现与本报告在同一交付提交，SHA 见本会话最终回执；没有 merge/push。

依据：[P1 接口](P1-INTERFACES.zh-CN.md) §2、5–7、9–11；共享 [contracts](../../src/workspace/contracts.py) 与 [fixture](../../tests/fixtures/workspace_contracts/fixture.py) 原样使用。按 AGENTS 查看参考项目 `D:/crq/src/learn-claude-code-main/agents/s01_agent_loop.py` 的工具/循环边界，没有引入新 agent 循环，也未使用 research-workflow。

## 1. 交付范围与结论

已实现可在循环外调用的公开验证模块：冻结 spec、两阶段基线绑定、环境/check/record 身份、pytest 结构化 runner 与保守 parser、注入式副本执行协议、验证次数/反馈次数/时间及产物预留、幂等和未知操作拒绝重跑。可用共享人工 fixture 实际演示“基线失败→候选通过”；回归覆盖不会被升级为目标修复结论。

生产 Docker capture/materialize 没有在本模块另写一套：通过 `ReplicaExecutor` 注入，由基础层负责。实现中仅包含一个明确受限的 `LocalFixtureExecutor`，用于已有依赖下的可信小 fixture 检查。它不是任意本地仓库执行器、Docker 隔离实现或恶意子进程安全边界。

本轮没有 Docker、GPU、solver/model API、下载安装、隐藏 grader 调用。没有修改 workspace 共享契约、runtime/session/factory/config/execution、旧文档或历史运行产物。

## 2. API 与文件

| 文件 / API | 作用与接入条件 |
| --- | --- |
| [contracts.py](../../src/validation/contracts.py) `PublicValidationSpec` | 深不可变公开 provenance、整个冻结材料清单、选择器、固定 argv/pytest 参数、runner/parser/policy 版本、覆盖与限额；拒绝私有评分字段、任意 shell/参数替换、PYTEST_ADDOPTS；`spec_id` 绑定配置 |
| 同上 `ValidationEnvironment` | 固定 image digest/ID、架构、解释器及 pytest 版本、依赖指纹、非秘密 env 白名单、资源/profile/初始化规则和逻辑 cwd；`environment_id` 使用共享域隔离 hash |
| 同上 `BaselineBinding` | 绑定 spec/environment、基础树和基础 candidate（含规则身份）、完整 nodeids 与 phase/outcome；`check_id` 排除审计 record_id，避免基线 provisional ID 的循环引用 |
| 同上 `ValidationRecord` / `ValidationAttempt` | 已启动记录与未运行尝试分开；无 capture 的 attempt 不编造 candidate_id；record 不可变、含来源/覆盖/原进程状态/计数/证据/预算；`record_id` 与共享 `validation_key`；可校验 `to_dict/from_dict` |
| [pytest_runner.py](../../src/validation/pytest_runner.py) | 独立宿主脚本，采集 collection_finish、collection_error、deselected、setup/call/teardown、异常类型、xfail/xpass、sessionfinish、pytest.main 返回码、解释器/pytest 版本和模块来源；原子发布 JSON；无 grader 或 JUnit 摘要判分 |
| [parser.py](../../src/validation/parser.py) `classify` | 严格 UTF-8/字段/重复键/数量/phase 顺序与集合检查；保守区分 passed、behavior_failed、inconclusive；从不读取 stdout 当 verdict |
| [replica.py](../../src/validation/replica.py) `ReplicaExecutor` / `ReplicaRequest` / `ReplicaResult` | 生产执行边界，见下节；runner 原始字节通过 `RUNNER_BYTES/RUNNER_SHA256/RUNNER_ID` 提供，不从模型参数取命令 |
| 同上 `LocalFixtureExecutor` / `FixtureMaterializer` | 只接受 local_fixture 与当前解释器；独立临时 workspace/control、固定 Python argv、最小 env、流限额、期限/取消、输入及 runner 前后确认和清理。材料化/manifest 核对由可信 fixture oracle 注入 |
| [service.py](../../src/validation/service.py) `ValidationService` | 构造注入 `base_entries, blobs, artifacts, executor, events`；可加载 `committed` records 与 `pending` operation→key；不创建模型或账本数据库 |
| 同上 `ValidationControl` | 注入绝对 monotonic deadline、cancel、clock、ValidationLedger、共享 ByteBudget；默认最终时间预留 162 秒、取消/期限后的清理宽限最多 12 秒；`agent_deadline` 不延长 run deadline |
| [ledger.py](../../src/validation/ledger.py) `ValidationLedger` / `ByteBudget` | 基线 1、候选 3、反馈 2；普通 explicit_tool 只能用前 2 个候选槽；最后槽供 natural_end/hard_stop；字节预留供 P3 与普通工具共用，禁止普通生产者耗尽最终证据空间 |

主要调用顺序：

```python
service = ValidationService(base_entries=base_entries, blobs=reader,
    artifacts=durable_writer, executor=replica_executor, events=durable_events)
base_record = await service.validate(base_capture, spec, environment,
    trigger="baseline", operation_id=baseline_operation, control=control)
binding = service.bind_baseline(spec, environment, base_record)
record_or_attempt = await service.validate(candidate_capture, spec, environment,
    trigger="explicit_tool", operation_id=tool_operation, control=control,
    baseline=binding)
# 自动结束同样调用 validate；trigger 为 natural_end 或 hard_stop。
```

基线执行先使用 spec/environment 的 provisional check ID；成功分类并绑定后生成最终 check ID。真实 regression_only 基线必须通过；fixture 才允许配置精确的公开已知失败节点，未修候选仍是 behavior_failed。换 spec、环境、基础候选规则或基础源码不能借旧基线继续认证。

`PublicValidationSpec` 的覆盖只有 `regression_only` 与明确人工 `fixture`；前者禁止 target_evidence 或已知目标失败列表。报告中的 `coverage.kind`、范围与 `runner_is_candidate` 始终保留，不生成 solved 字段。

## 3. 与基础层交接的执行协议

`await executor.execute(request, started) -> ReplicaResult` 的不可省略责任：

1. 调用 `started(unique_owned_replica_id)` **先于**创建容器/副本等外部副作用；该回调同步写 validation.started，然后标记次数已启动。副本预检没开始就拒绝，可释放预留；一旦开始，失败/超时/未知均占一次。
2. 使用同一固定环境和真实基础清单；安全 materialize base+delta，重新核对完整 candidate manifest 与冻结材料。不得拿宿主 checkout 替代实际捕获，不调用隐藏 grader，不重跑整个 SWE runner。
3. 使用 spec 的固定 argv 模板和 runner bytes，严格 env 白名单；实际副本路径只由宿主填槽。Sphinx 目标模块从工作区、pytest 从安装位置导入；pytest 仓库适配必须声明 runner_is_candidate。
4. 实际核对 before_manifest_id、after_manifest_id、environment_id；保证候选/冻结输入变化可以被发现，返回完整、有界 stdout/stderr 和原始协议 bytes；runner/control 文件也须核对，返回 runner_unchanged。
5. 执行期间服从 request.deadline_mono/cancel 和每阶段期限/字节限额；任意路径均清理 owned 副本与进程，明确返回 cleaned 或 cleanup_failed。服务外围会有界等待，无法确认清理时只给 inconclusive 并保留未知状态。

服务自身在副作用之前调用共享 `capture.validate_against_base(base_entries)` 与 `capture.validate_blobs(blobs)`；核对 protected_manifest 与实际基础 entries、当前候选相等，并拒绝新增测试/全局 conftest、sitecustomize/usercustomize、pytest 配置和 .pth。它不把共享契约的内部一致性校验当作“已经证明静止捕获”。生产捕获屏障、完整政策判定与材料化仍归基础层。

ArtifactWriter.publish(bytes) 必须在完整持久发布后返回正确 ArtifactRef；注入 BlobReader 必须能读 capture 和 evidence 的受控引用。服务再次检查发布引用及其 bytes，另有 `replica-attestation-v1` 见证 artifact，保留副本 ID、执行状态、前后 manifest、环境、runner 与清理状态，避免这些核对仅存在于服务内存。之后才提交 record。写盘失败不返回 passed，并保守占用整份证据字节预留，避免未知的部分写入换来免费重复额度。

EventSink.emit(type, payload) 必须原子持久化或抛错；它是 RunSession/ArtifactStore 的后续适配点。validation.committed 单个 payload 同时含完整 record/attempt、已结算验证 ledger 和字节账本。不额外建立日志数据库或模型账本。

## 4. 通过、幂等、预算和未知状态

通过要求真实进程 succeeded/exit=0、runner 协议和 operation/hash 匹配、完整 collection/sessionfinish、返回码一致、冻结节点集合及逐节点 setup/call/teardown 完整、有测试体通过、无 skip/xfail/xpass/deselect、输入和环境不变、证据完整持久、清理确认。setup/collection/import/未知异常保守为 inconclusive；完整断言失败才为 behavior_failed，可供未来门禁反馈。

- 相同 operation_id 返回原结果；身份变化报 ContractError。相同 validation key 的已提交完整 passed/behavior_failed 可以复用，不重扣次数，写 validation.reused 并引用原 record_id。返回的是原 record，其 operation_id 仍是原操作；新操作关联由 reused 事件表达。
- 复用前重新校验 evidence 引用。inconclusive 默认阻止同 key 自动重试；pending 或提交失败的未知 key，即使用新 operation ID 也不重新启动副本。not_run 不作为有效验证缓存。
- `committed` 与 `pending` 是调用者恢复出的持久状态，不是扫描磁盘猜测。P3 必须一起恢复 ValidationLedger、ByteBudget 与这些状态；不能加载 record 却给一个全新的免费 ledger。Docker 中断续跑仍按 P1 拒绝，这里的恢复仅用于记录幂等/检查。
- baseline 1 次单列。候选默认最多 3 次；前 2 次可显式验证，最后一次 explicit_tool 返回 final_slot_reserved。自然结束优先普通槽；使用最后槽的 record 带 final_slot=true，不能再反馈继续编辑。
- 未创建副本的拒绝只记 attempt；启动后的失败、取消和清理未知都占一次。普通验证不侵占最终时间窗口；natural_end/hard_stop 可使用窗口，但不能延长总期限。
- ByteBudget 先预留 report+双流日志+执行见证的完整限额，普通消费者不能使用 final_reserve。P3 还需给捕获/delta/manifest 预留其自身空间，并把现有普通工具的输出/ArtifactStore 配额接入同一预留策略；本模块没有改旧 RunOutputMeter 或旧工具。
- `note_feedback(record)` 保证同记录一次、最多 2 次、有剩余验证槽、非最终槽和明确行为失败。P3 的 CompletionController 仍须检查模型/上下文/时间及后续完整产物空间，再把 feedback 与其 ledger 状态原子写入 completion.feedback；B 不负责发送模型消息。
- 取消/总期限后不开始新副本。服务中在途执行收到取消，最多使用声明清理宽限；拒不退出的 executor 只记录 cleanup_failed/未知，不无限等待，也不免费再跑。

## 5. 验证证据

解释器命令统一使用主树现有 `D:/crq/src/coding-harness/.venv/Scripts/python.exe`，从本 worktree cwd 启动，并使用 `-B`。预先确认 `src.workspace.contracts.__file__` 指向本 worktree，而不是主树。实际宿主是 Windows、Python 3.13.9、pytest **9.1.1**。

```powershell
D:/crq/src/coding-harness/.venv/Scripts/python.exe -B -m pytest tests/test_public_validation_parser.py tests/test_public_validation_service.py tests/test_public_validation_fixture_executor.py tests/test_workspace_contracts.py -q -p no:cacheprovider --basetemp=.tmp-p2-b
```

结果：**141 passed in 7.73s**，其中新增公开验证 68 项、原共享契约 73 项。该结果来自最终实现（含执行见证与有界清理）的定向回归。

| 类别 | 实际检查 |
| --- | --- |
| 真实本地 fixture | 失败基线绑定→修复通过；测试自改 data 输入；os._exit(0) 无报告；实现通过 pytest.skip 跳过测试体；巨大失败输出触及截断；实际超时、清理临时目录；无编辑输入回灌 |
| parser 负例 | 打印 passed/JUnit/无报告、重复 JSON 键、漏/重/错序 phase、缺节点/零测试/deselect、部分及全 skip、xfail/strict xpass、collection/setup/未知异常、退出码/正常结束/版本/模块来源矛盾、输入或清理不完整 |
| 服务与账本 | 基线/候选身份与规则变化、冻结测试/新 conftest/坏 blob、同 op 并发幂等、同 key 复用、基线 1/候选 3/反馈 2、最终槽及时间/字节预留、未开始释放与已开始扣次、在途取消/async cancel/期限、提交失败/证据损坏/pending 拒重跑、持久记录反序列化校验、拒不清理的 executor 有界返回 |
| 兼容与范围 | 原共享契约 73 项通过；新增模块没有修改旧入口；没有 Docker 或模型测试 |

pytest8-v1 与 pytest45-v1 在此只验证了**合成协议 receipt**的版本/判定规则；没有实际运行 Sphinx pytest 8.4.2 或 pytest 仓库 4.5.0，也没有在新 runner 下复跑 P0 的两个真实模块。pytest9-fixture-v1 明确只允许 coverage=fixture，不能伪装为首批生产 profile 已适配。

可信测试 oracle 仅在专属 [support fixture](../../tests/fixtures/public_validation/support.py) 中，根据共享人工文件和独立 JSON/hashlib 参考实现核对。Windows fixture 的 mode 固定为虚拟 0644；不声称验证了 Linux mode、Docker quiescence 或原生链接。

## 6. 尚缺的生产与 P3 验收

1. 基础层提供 production ReplicaExecutor，真实 base+delta materialize、静止捕获、全树/冻结文件前后身份核对、实际 image/interpreter/dependency 及模块来源校准、所有 owned 进程和容器的清理证据。B 不复制这些实现。
2. 在批准的串行 Docker 时段实际跑 Sphinx pytest 8.4.2，然后验证 pytest 4.5.0 的 hook/异常/xfail/来源差异和候选框架篡改边界。当前只是协议适配准备，不能由合成 receipt 放行。
3. P3 接事件/reducer、ArtifactStore/输出 meter、配置/环境 fingerprint、validate_patch 工具和全退出路径完成控制；将宿主 finalization 的时间/空间预留传入普通模型/工具路径。当前没有 gate/DONE/session 接线，没有反馈模型调用，也没有 Docker 中断续跑。
4. 该设计针对正常开发错误及显式覆盖绕过做保守验证；同一个 Python 解释器内的任意恶意候选仍可能操纵 pytest hooks。runner_is_candidate 保留在记录中；它不是隐藏评分器的替代品，也不声称抗恶意评分证明。

这些是接线和生产环境验收待办，不影响模块的注入式接口和人工 fixture 机制检查；真实仓库修复收益仍无新证据。


## 7. c72d7c7 独立审查后的定向修正

本节保留上文首次交付的历史 141 项结果，记录独立审查发现及后续修正。后续提交仅修改 B 服务、相关回归与本报告，不接生产 Docker；提交 SHA 见本会话本次最终回执。

审查指出两项 P1 和一项 P2：候选只信任声明的 BaselineBinding；相同 operation 快速重放跳过当前资格/证据检查；执行器已完成时可能漏掉其单次期限。先在未修改的 `c72d7c7` 上运行新增审查回归，**11 failed in 0.33s**，复现无真实基线却通过、篡改 outcomes/nodeids/record_id、基线证据损坏、同 operation 的证据/资格/正文失效，以及完成/启动越过单次期限等行为。

修正如下：

- 新增按 record_id 索引的已提交历史记录。候选执行、operation 重放和 key 复用前，均按 binding.record_id 查找已提交 baseline，验证其全部 evidence，并重新用 parser 核对基线的完整性、状态/计数。随后从真实 record 重建绑定并完整比较，缺失、损坏或矛盾分别拒绝为 baseline_unavailable、baseline_evidence_unavailable、baseline_mismatch，不启动副本。
- operation 重放移至共享当前 capture/spec/基线资格检查之后，并与 key 复用使用同一证据验证函数。不同 validation key 仍拒绝复用同一 operation ID；同 key 也不能绕过 admissible、blob、取消/期限或基线来源等当前条件。拒绝返回新的 not_run attempt，但通过 source_record_id 引用历史，原 operation outcome/record 不被覆盖。不自动重跑、不重复扣次；原 not_run 也不会因更换 trigger 自动变成新执行。
- 仅恢复候选 record 不足以复用：调用者必须同时恢复其真正的基线 record。更新原恢复用例以明确这一新约束，另有“缺基线时同 operation/新 operation 都拒绝”回归。
- 一次计算 request_deadline，started 回调在其前后持久事件边界核对该期限；超时启动被拒绝且不产生副本副作用。执行监控在 task 已完成后仍检查 request deadline，服务在结果处理及证据提交前再次核对。到达/越过期限的结果只能 inconclusive/timeout，不因任务总 deadline 仍有余量而通过。未启动的超时尝试释放槽位，已启动的超时仍占一次。

本次最终定向命令：

```powershell
D:/crq/src/coding-harness/.venv/Scripts/python.exe -B -m pytest tests/test_public_validation_review.py tests/test_public_validation_service.py tests/test_public_validation_fixture_executor.py -q -p no:cacheprovider --basetemp=.tmp-p2-b-review
```

结果：**47 passed in 7.16s**（新增审查 14 项、原服务 27 项、真实本地 fixture 6 项）。其中正常同 key 不同 operation 并发仍只执行一次，原计数/取消/证据失效检查仍通过。新 [审查回归](../../tests/test_public_validation_review.py) 保留旧错复现；没有无变化重跑全部 141 项，也没有把旧总数当作本次新提交的完整复验。Python 3.13.9 / pytest 9.1.1 与首次交付相同，Docker/pytest8/pytest4.5/生产接线边界仍按第 6 节保留。

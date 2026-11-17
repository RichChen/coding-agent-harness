# FIX-14 无模型准备状态

日期：2026-11-13

## 已完成

- 新增 `configs/rebench/fix14-t8.yaml`。它使用 thinking enabled、reasoning effort high 和 `max_tokens: 8192`。
- 新增 `configs/rebench/fix14-o32.yaml`。它关闭 thinking，使用 `max_tokens: 32768`。
- 生成 `scripts/rebench/out/fix14_factorial_tasks.json`。计划包含 30 道 FIX-13 新题、2 个新增格子、每题两次，共 120 个名额，使用固定种子 20261130。
- 新增本地计划测试 `tests/test_fix14_factorial_plan.py`。
- 新增 `scripts/rebench/run_fix14.py`，复用 FIX-13 的账本、停止和恢复逻辑，但使用独立的 FIX-14 子进程入口和 `runs/fix14` 输出目录。
- 新增 `scripts/rebench/fix14_preflight.py`，为 T8 和 O32 各运行一个真实 Docker 无模型接线 case，并把证据写入 `runs/fix14-preflight`。

## 已验证

计划生成脚本成功运行，输出 120 个名额和源计划哈希。两份配置可被现有配置加载器读取。计划测试覆盖两组完整性、每题两次重复和确定性。

`run_fix14.py --preflight` 已通过，确认 120 个名额、模型名和两组配置差异。计划和 runner 测试通过。真实 Docker 无模型预检的单例状态如下：

- T8 retry1 已生成完整事件、候选和 grader，收尾结果为 `cleanup_failed`/`DockerUnavailable`，证据在 `runs/fix14-preflight/e2e/vprusso__toqito-1298/T8/wiring.retry1`。
- T8 retry3 和轻量题目的单例重试在 workspace/container 启动阶段卡住，现场保留在 `runs/fix14-preflight/e2e/vprusso__toqito-1298/T8/wiring.retry3` 及 `runs/fix14-preflight/e2e/ome-zarr-models__ome-zarr-models-py-206/T8`。
- O32 没有通过的独立 case。预检记录的 `model_calls=0`、`provider_calls=0`。

当前状态为**预检阻塞，不得启动正式批次**。身份检查、价格绑定和正式启动暂缓，已有证据不覆盖 O32 的正常入口，也不足以证明 FIX-14 的 Docker 收尾可靠。

## 主会话需要审阅的事项

1. 接受 T8 和 O32 作为拆分 UT 的两个新增格子。
2. 接受使用 FIX-13 的 30 道新题完成 120 个名额。
3. 由执行会话补齐真实 Docker 无模型预检和正式启动器接线后，再决定是否启动付费运行。

现有固定 60 题支持这轮问题。只使用其中已经拥有 U、E、UT、ET 同条件证据的 30 道新题，保留旧 30 题的历史材料，避免不同运行条件混在同一比较中。

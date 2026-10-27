# AUDIT CR-2c 第一次停止

日期：2026-10-27。审计：Claude Code。对象：[CHECKPOINT-CR-2c](CHECKPOINT-CR-2c.md)，r2 冻结 `cr2-freeze-r2`（`b902b93`）。

结论：停止本身符合冻结规则，r2 的模型名修正也生效了。停止原因是一条分类规则写得不对，需要做一次 r3 修订后续跑，见合同第 9 节。

## 1. 三次失败其实是同一个原因

`scikit-learn__scikit-learn-12585/n/r4` 的三次运行：

| 运行 | `snapshot_error` |
|---|---|
| `r4.infra1` | `DockerUnavailable: Docker control output quota exceeded` |
| `r4.infra2` | `ValueError: delta_too_large` |
| `r4` | `ValueError: delta_too_large` |

第一次的报错看起来不同，但原因相同。`export_delta` 给这次 Docker 调用的输出上限是 `DELTA_BYTE_LIMIT + 1 MiB` = 5 MiB（`src/execution/baseline_image_backend.py:151`）。导出内容是 base64 编码的，原始体积超过约 3.75 MiB 时，输出就会先撞到这个上限，而不是由脚本报出 `delta_too_large`。所以三次都是工作区的改动太大，导不出候选补丁。

## 2. 按现行规则，这算 infra；但这条规则本身有矛盾

- A5-MAIN-FREEZE 第 124 行写的是：`delta_too_large` "是程序结果，不是 Docker 控制错误，重试也不会变"。
- `run_infra_reason`（`scripts/run_swe_task.py:635`）却把任何 `snapshot_error` 都判为 infra_error。CHECKPOINT-6 当时已经指出 `delta_too_large` 可能是 agent 自己造成的，只是 A5 里没有出现，这个矛盾就没有被处理。
- CR-2 沿用了"infra_error 退役重跑，每个槽位最多 2 次"。这次 3 次失败都来自同一个槽位，而且在运行早期，分母只有 28，于是触发了"至少 3 次且超过 10%"。

## 3. 判定

**改为任务结果，不改停止条件的计数方式。** 前提是先确认改动确实是 agent 自己的命令产生的（合同 9.1）。理由：

1. 这是 agent 的行为造成的。它在工作区里跑出了大量文件，导致候选补丁无法导出。和 agent 把仓库改坏一样，这是任务的结果，重跑只会重复：三次里有两次一模一样。
2. **主指标不受影响。** 首次触碰 gold 文件的步数只看 `events.jsonl`，不需要候选补丁。所以这个槽位的主指标完全有效，修复率记为未解出。如果把它当 infra 反复重跑，最后保留下来的仍是一个没有修复率的槽位，反而白白丢掉了有效数据。
3. 新规则只看错误类型，不看 solved，也不看组别。四组适用同一规则，不会偏向某一组。
4. 停止条件的计数方式不改。真正的 infra 错误在早期集中出现，恰恰说明有系统性问题，应该停下。

不选另外两种做法的原因：

- 只统计最终运行，或等完成数够多再检查：这次的问题是分类错了，不是计数口径的问题；改计数会削弱对真正 infra 的保护。
- 人工放行后继续：这道题还有 18 个槽位没跑，同样的情况很可能再出现，还会再停。

## 4. 还需要核实的

- **改动是不是 harness 自己写进工作区的。** 如果是 harness 在工具超时等路径上往 `/workspace` 写了文件，那就是 harness 的问题，必须算 infra，上面的判定也就不成立。所以续跑前要先免费重放一遍（合同 9.1），找出是哪一步、哪些文件。
- **02:14:53 那次来源不明的启动。** 我这边没有启动过。r3 给 runner 加上单实例锁。
- **系统待机。** 上一轮 prompt 说"已经关闭"，实际没有关。Cursor 已经恢复原值，续跑前要重新关闭。
- **模型名。** DeepSeek 返回的就是别名 `deepseek-flash`，所以同一别名下的版本变化看不出来。r3 同时记录响应里的 `system_fingerprint`（如果有），只做报告，不作为停止条件。

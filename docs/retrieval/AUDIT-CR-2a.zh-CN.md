# AUDIT CR-2a

日期：2026-10-26。审计：Claude Code。对象：[CHECKPOINT-CR-2a](CHECKPOINT-CR-2a.md)、[CR-2-FREEZE](CR-2-FREEZE.md)，提交 `50c39dc`..`e72fd2c`。

结论：**通过**。项目作者在审计后决定扩大规模：加第四组 O，重复次数改为 5。这些变化发生在任何真实运行之前，所以重新冻结即可，见合同第 7 节。

## 1. 核对了什么

| 项 | 方法 | 结果 |
|---|---|---|
| 改动范围 | `git diff --stat ace00b3 cr2-freeze` | 17 个文件，都在合同第 0 节放开的范围内；已有的 `src/retrieval/*.py`、`cr_eval.py`、`cr_index.py` 都没动 |
| 三个已有文件 | 读 diff | `factory.py`：可选参数、白名单加一个名字、注册时没有服务就报错。`run_swe_task.py`：三个带默认值的参数。`tool_errors.py`：工具列表。都是增量 |
| 兼容性 | 我重跑 `cr2_compat.py` | b0-v2 的 schema sha256 `19be6f65…0abd`、runtime_fingerprint `4db95abc…c4c5` 与改动前相同 |
| 测试 | 我重跑 CR-2 的 4 个测试文件和隔离测试 | 76 passed |
| 工具 | 读 `src/tools/retrieve.py`、`serve.py` | 只读 CR 缓存；参数二次校验；异常时写 traceback，会触发停止；编码器加锁，并发安全 |
| 预取 | 读冻结清单和样例 | 56 题前 5 文件与 CR-1 test 排名相同；630–1001 字节；只给路径、symbol、行号 |
| 付费闸门 | 读 `check_authorized` | 不带 `--mock` 必须给与常量一致的 `--authorized`，否则拒绝启动 |

## 2. 对取舍的判定

- 第 7 条，模型调用超时不重跑：**同意**。A6 只重跑 HTTP 结算失败，超时归为任务结果；三组一样对待，不会产生偏差。
- 第 8 条，步数包含 provider 重试：同意。与预算计数和 A5/A6 的 `first_edit_step` 口径一致。
- 第 9、10 条，触碰按 `tool.intent` 计，不论成败：同意。
- 第 13 条，开跑前读过一次标签，只输出计数：可以接受。48/56 题的预取前 5 含 gold 文件，这也说明 P 组的信息量足够。
- 第 1、2、11、12、15–17 条：接受。

## 3. 需要注意的地方

- 预取前 5 有 48/56 含 gold 文件，比 CR-1 test 全体的 85.8% 还高。这 56 题偏简单，定位本来就不难。所以 P/T 对主指标的作用可能有限，O 组正好可以回答这一点：定位做到完美时修复率能高多少。
- `deepseek-flash` 是服务端别名，可能在运行期间变化。四组交错运行可以避免偏向某一组，但报告里要写上运行起止日期和返回的模型名。

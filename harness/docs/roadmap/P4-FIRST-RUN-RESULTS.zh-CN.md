# P4 首轮真实任务结果：补丁通过公开验收，运行触及步数上限

日期：2026-10-30。依据：已批准清单与预算、[公开检查预检与修正](P4-01-02-RESULTS.zh-CN.md)。运行代码提交：`1e9a3c2`，仅本地，未 push。

**新入口完成了一次真实模型修复，最终候选通过四项冻结公开目标检查及既有回归检查。运行本身在第 24 次模型调用后触及 `step_limit`，没有自然结束。** 候选在预算耗尽后仍被封存、验证和导出，原始运行的 `success=false` 保持不变，不能用 CLI 退出码 0 或公开检查通过把它改写成自然成功结束。

本轮已停在约定检查点：2 次协议请求 + 1 题 1 次尝试，没有补跑、换模型或增加题目。以 provider 用量按官方高峰、无缓存价格估算 **0.426664 元**，在批准的 5 元预算内；这不是供应商账单实扣，未核验账户账单。开发与审查会话用量不含在此金额中。

## 1. 实际结果

| 项目 | 结果 |
| --- | --- |
| 任务 | Sphinx 公开 issue #8720，本地资产 `sphinx-doc__sphinx-8721`；固定历史基点，开发任务 |
| 模型 | DeepSeek 官方 `deepseek-flash`，非思考模式；请求和返回模型名一致 |
| 版本观察 | 26 次请求返回同一 `system_fingerprint=bc19ba97cbe016479903222bb1578db6`；这不等于可永久锁定版本 |
| 协议检查 | 恰好 2 次真实请求：工具请求 → 固定工具结果 → 最终续答；usage 完整 |
| 修复运行 | 24 次模型请求，约 123.04 秒；`failure_reason=step_limit`，无自动重试 |
| 修改 | 仅 `sphinx/ext/viewcode.py`，增加 EPUB 关闭条件的页面生成防护；无删除文件 |
| 公开目标检查 | 4/4 passed，约 33.55 秒；同一冻结检查在原始基点为 2 passed / 2 behavior_failed |
| 既有 B 回归 | 最终候选 `passed`，覆盖仍为 `regression_only`；基线 8 passed |
| 宿主失败反馈 | 0 次；不能声称本轮验证了“真实失败反馈后再修复”的收益 |
| 工具使用 | `run_command` 23 次、`list_dir` 1 次、`search_replace` 1 次；没有主动调用 `retrieve_code` 或 `validate_patch` |
| 用量合计 | 输入 203,968，输出 2,341，总计 206,309 token；包含协议检查和逐轮重复输入 |
| 收尾 | 唯一末尾 `run.finished`，186 条事件；无 pending 模型/工具/验证；运行与目标检查的 4 只容器均确认不存在 |

判据在模型运行前公开且冻结。结果表示这个候选满足四个公开场景，不表示 SWE-bench 隐藏 grader 通过；`independent_evaluation=not_evaluated`、`solved=null` 保持原样。一道已用于开发的历史题不提供泛化、修复率增益、检索增益或记忆增益证据。

## 2. 模型实际上怎样完成任务

下面的序号按真实模型请求计数，不把同一响应中的多次工具调用算成额外模型请求。

| 模型请求 | 关键动作 | journal 证据 |
| --- | --- | --- |
| 1 | 直接 `cat sphinx/ext/viewcode.py`，同时列目录；一开始就找到了后来修改的文件 | `tool.intent` seq 15、17 |
| 2–22 | 阅读和定位 builder、页面生成与 viewcode 代码，没有修改或构建复现 | 各次工具意图与原始输出 |
| 8、14、15、21、22 | 多次重新读取 viewcode 的相同区域；`get_outdated_docs` 也出现重复查询 | seq 66、108、115、157、164 等 |
| 23 | 唯一一次 `search_replace`：在 `collect_pages` 中对 EPUB 且未启用 viewcode 的情况提前返回 | seq 171 |
| 24 | 在容器 `/tmp` 创建公开复现项目并做 HTML/EPUB、自测关闭/开启行为；命令退出 0 | seq 178，结果含 `html=0`、`epub=0`、`fresh=0`、`true=0` |
| 预算结束后 | harness 捕获最终候选，完成既有公开回归并封存交付；之后宿主独立运行冻结目标检查 | 最终 capture、validation、delivery、target report |

23 次命令中 21 次退出 0；两次非零分别涉及 grep 无匹配和引用不存在的文件。没有看到 schema、权限或工具接线阻止模型读取源码。主会话也核对了实际送回模型的工具消息，包含真实源码和构建输出。

因此不能把这次说成“修好后还一直测试、不肯停”：**第 23 次才修改，第 24 次做合理自测，随后没有下一次自然答复的额度。** 更直接的观察是，前期重复阅读占用了调用预算。Masking 与重读有相关性，但本轮没有做对照，不能认定 masking 是原因，也不能据此断言换一种上下文策略会更好。

这轮值得保留的工程展示是：预算耗尽时仍交付了明确身份的候选及验证记录，且运行状态与候选验收状态分开表达。它不证明如果没有新增模块，这个问题就不能被修好。

## 3. 准备、审查与证据绑定

- 公开检查包括新鲜默认 EPUB、HTML→EPUB 默认关闭、HTML→EPUB 显式 False、EPUB 显式 True。必须构建成功并产生有效 EPUB，才判断模块源码页是否存在。
- 模型接入新增默认关闭的审计/停机开关；本次启用。计费未知、中断或截断会保留结算证据并阻止后续请求/不完整工具执行。真实运行没有出现未知 usage。
- 独立 Astra 审查发现读取器误用默认 2 MiB 限额；修正为继承 session 原声明配额。没有扩大限额；新增边界回归和独立复审通过。
- 当前检查身份为 `public-target-v1:586c07f4bb300d01eb18dbf75cdb34fba69b8589d45bf3b4f6efc00f43569e61`。新身份的原始基点和旧 P3 封存路径均重新核验；旧 01 证据保留原身份。
- 主会话联合检查 **91 passed，22.06s**。独立复审另跑 26 项；这些集合有重叠，不相加。没有运行整个仓库测试。
- 运行前核对了 17 项审查文件哈希、完整公开输入、配置模板和源码指纹。付费任务的源代码没有在运行中改变。

最终候选身份：

```text
candidate-v1:40070069778b06d7b6580117e1d6dac54ed1cd106079c4e4ceb3dcbdcb12c785
```

最终 B 验证记录：

```text
validation-record-v1:639201939168863100183078770ddba7865a4a2e5fe638ee9097da755d7cbe1a
```

源码指纹：`7cbc503ff081b010268d47fb70bd07da135fcaf5ae89b19f86782c54f552b6ae`。任务输入 SHA-256：`2d1bbdd9404cb463b3f6ba407a3f3086c68167c5c02dd70f8d22b75f2ebe1196`。目标检查前后真实 manifest 一致，并与最终 session/export 的候选身份匹配。

启动前曾因文本读取自动转换换行而触发输入哈希拒绝；拒绝发生在尝试标记、子进程和任务模型请求之前。改为解码已校验的原始字节后启动，冻结输入未变，真实任务仍只有一次。此记录保留在 `prelaunch-note.json`，不算模型失败或额外尝试。

## 4. 作者从哪里查看

提交的小证据：[summary.json](evidence/p4-first-run/summary.json)。完整本地产物根：`D:/crq/artifacts/p4-sphinx8721-20261030-01/`。

| 文件 | 用途 |
| --- | --- |
| `run-manifest.json`、`task.txt`、`config-template.yaml` | 授权范围、冻结输入和不含凭证的配置模板 |
| `probe/attempts.json`、`probe/budget.json` | 两次真实协议请求的 metadata、usage 与预算 |
| `solve/result.json`、`solve/session/events.sqlite3` | 完整运行状态、模型尝试与工具轨迹 |
| `solve/delivery/`、`model.patch` | 封存候选、依赖证据与实际模型补丁 |
| `target/result.json`、`target/target-report.json` | 独立公开目标检查及逐构建原始输出 |
| `usage-and-journal-audit.json`、`container-cleanup-audit.json` | 调用/费用估算、事件完整性和容器清理核验 |
| `tool-actions.json` | 模型调用序号与工具事件 seq 的对应清单 |

只读查看，不会重新调用模型或启动容器：

```powershell
.\.venv\Scripts\python.exe -B scripts/run_workspace.py --inspect D:/crq/artifacts/p4-sphinx8721-20261030-01/solve/session
```

## 5. 留给下一次讨论的决定

当前有证据支持的改进问题是 **前期重复阅读与调用预算分配**。先检查关键事实在模型视图中如何保留，再考虑对既有 masking/提示做一个小对照；不直接追加检索平台或记忆系统，也不把增加一轮“完成”答复当作能力增益。

下一步是否继续两题、选哪些任务、是否比较上下文策略，均留待作者审阅这一条真实轨迹后决定。本轮预算余量不继续使用，A5/A6/CR 冻结资产保持原样，未夸大 README 的效果表述。

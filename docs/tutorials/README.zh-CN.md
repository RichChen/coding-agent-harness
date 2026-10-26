# 两个项目，从这里开始

这组教程解决的是“知道项目在做什么，但不知道怎么启动、怎么看轨迹、怎么对应到代码”。先跑，再看数据，再读指定函数；第一遍不用研究实现细节。

| 顺序 | 教程 | 第一遍的目标 |
|---|---|---|
| 1 | [coding-harness 上手教程](/D:/crq/src/coding-harness/docs/tutorials/HARNESS-START-HERE.zh-CN.md) | 跑修复任务，查看每轮输入输出，把 loop、工具、session 对上 |
| 2 | [GRPO 上手教程](//wsl.localhost/Ubuntu/home/crq/agentic-rl/docs/tutorials/GRPO-START-HERE.zh-CN.md) | 跑 episode，查看隐藏判分，再接到 batch、更新和真实训练日志 |

**今天只做 harness 的第 1～3 节。** 跑通并能解释“读文件的结果如何进入下一轮输入”，已经完成第一轮目标。然后做失败对照，最后再碰恢复、训练、评测细节。

第一条启动命令，在 PowerShell 中：

```powershell
Set-Location D:\crq\src\coding-harness
.\.venv\Scripts\python.exe scripts/tutorial_walkthrough.py run --case fix
```

GRPO 的第一条启动命令，也可以直接从 PowerShell 执行：

```powershell
wsl -d Ubuntu -- bash -lc 'cd /home/crq/agentic-rl && .venv/bin/python scripts/tutorial_walkthrough.py run --case fix'
```

两份教程都提供 `show flow`、`turn N`、`show files`、`where`，不用自己搜运行目录。

教学运行使用固定模型回复/策略，核心循环和工具真实执行。GRPO 的环境判分使用真实 bwrap 和 grader；harness 的最终检查是简单的源文件比较。教程里明确分开教学实验和正式成绩，所有输出都写到新建的 `runs/tutorial-*` 目录。

后续学习顺序：

1. 跑通、读轨迹。
2. 把每一步输入输出指到代码块。
3. 先预测失败案例，再运行核对。
4. 理解恢复/判分/训练的边界。
5. 再深入参数校验、token 对齐、loss、沙箱等实现。

每一轮只给自己一个验收要求：能否拿着自己刚运行的文件，说明“这个值是谁产生、传给谁、失败时在哪儿看”。不要以背下所有模块名作为进度。

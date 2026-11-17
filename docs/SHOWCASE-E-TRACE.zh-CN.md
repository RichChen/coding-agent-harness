# E 线逐题轨迹展示

组级报告已经说明 U-new、N、E-new 的整体行为差异；这份补充材料把同一道题的两次运行并列出来，方便面试时展示“定位之后是否继续编辑、是否重复调用”的具体变化。

运行：

```powershell
python scripts/analysis/e_trace_showcase.py `
  --input docs/analysis/fix15/runs.json
```

默认选择任务 ID 排序后第一个同时有 U-new、N、E-new 记录的任务；也可以明确指定：

```powershell
python scripts/analysis/e_trace_showcase.py `
  --input docs/analysis/fix15/runs.json `
  --task avaiga__taipy-2797
```

报告展示 `located_request`、`first_edit_request`、`edit_calls`、`tail_repeat`、状态、解出结果、请求数和费用。这些字段来自已经审阅过的 FIX-15 运行摘要，是轨迹的行为摘要代理，不是完整逐步对话，也不是对模型内部思维的观测。

命令只读取显式导出，不读取原始 session，不重新评分，不调用模型或 Docker。数据仍然来自固定题集、单一模型和每题两次运行；它适合展示机制线索，不构成新的独立验证。

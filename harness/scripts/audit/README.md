# scripts/audit

`case_mem.py` 是审计侧写的一次性脚本，`docs/sprint/A5-CASE-DATA.md`、`docs/sprint/CHECKPOINT-7.md` 里引用的 `.tmp/case_mem.py` 就是它（`table` / `follow` / `task` 三个子命令）。复跑时把它复制回 `.tmp/`，或者直接从这里运行。

- 它不维护，不保证在当前代码上还能运行。
- 它需要把 coding-agent 仓库加进 `sys.path`，用本仓库的 `.venv`，并按原两仓库布局检出。
- 审计时写的其他一次性脚本只留在本地，不发布。

`errata_2026_11/` 是 2026-11 勘误的复算脚本，勘误里的数字都出自这里。每个脚本旁边的同名 `.json` 是提交时的输出。

- `e1_a5_card_visibility.py`：A5 主实验 B2 的记忆卡只出现在第 0 步的视图里。
- `e2_cny_cap_parse_errors.py`：A6-R 和 A5 的 `model_parse_error` 是单次运行 1.000 元上限拒绝预留。
- `e3_p4_scale54_final.py`：P4 提示时机 54 次实验 v2/v3/v4 三批的最终结果和费用。
- `e4_fact_checks.py`：README 里测试数、Docker 校验项、事件库结构、截断配置的现值。
- 都不调用模型、不启动 Docker，只读 `runs/` 和本地产物目录（脚本里的 `ARTIFACTS` 常量）下的产物。`runs/` 不提交，复跑需要本地有这些目录；E1、E2 还要求 coding-agent 按两仓库布局检出在旁边。

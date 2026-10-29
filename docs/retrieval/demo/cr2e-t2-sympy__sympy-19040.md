# 代码检索演示：sympy__sympy-19040

由 `scripts/cr_demo.py` 离线生成，不调用模型。运行：`runs/cr2e/round2/sympy__sympy-19040/r1`。

## Issue

- 题号：`sympy__sympy-19040`
- 仓库：`sympy/sympy`
- base_commit：`b9179e80d2daa1bb6cba1ffe35ca9e6612e115c9`

````text
Factor with extension=True drops a factor of y-1
I guess this related (or a duplicate of?) #5786

This is from stackoverflow:
https://stackoverflow.com/questions/60682765/python-sympy-factoring-polynomial-over-complex-numbers
```julia
In [9]: z = expand((x-1)*(y-1))                                                                                                                

In [10]: z                                                                                                       ……（截断，共 1885 字）
````

## 检索（查询 = issue 全文）

来源：base_commit 快照，`cr1-freeze`（jina-code @516f4baf，hybrid k=60、w_emb=2、文件级融合、BM25 块聚合）。 快照共 790 个文件。

| 名次 | BM25 | embedding | hybrid |
|---:|---|---|---|
| 1 | `sympy/utilities/exceptions.py` | `sympy/polys/polytools.py` | `sympy/core/expr.py` |
| 2 | `release/fabfile.py` | `sympy/core/expr.py` | `sympy/ntheory/factor_.py` |
| 3 | `sympy/integrals/risch.py` | `sympy/polys/factortools.py` ★ | `sympy/core/numbers.py` |
| 4 | `sympy/ntheory/factor_.py` | `sympy/functions/special/error_functions.py` | `sympy/functions/combinatorial/factorials.py` |
| 5 | `sympy/core/singleton.py` | `sympy/polys/compatibility.py` | `sympy/polys/polytools.py` |

hybrid 前 5 个文件各自的前 2 个块（即 `retrieve_code` 返回的内容）：

1. `sympy/core/expr.py`
   - `Expr.factor`，第 3586–3589 行
   - `Expr.equals`，第 727–857 行
2. `sympy/ntheory/factor_.py`
   - `_factorint_small`，第 876–974 行
   - `is_perfect`，第 2315–2392 行
3. `sympy/core/numbers.py`
   - `One.factors`，第 2689–2695 行
   - `Integer._eval_power`，第 2300–2403 行
4. `sympy/functions/combinatorial/factorials.py`
   - `subfactorial`，第 294–378 行
   - `FallingFactorial`，第 636–766 行
5. `sympy/polys/polytools.py`
   - `factor`，第 6258–6356 行
   - `_symbolic_factor`，第 5957–5969 行

## Gold 文件（--show-gold）

| gold 文件 | BM25 名次 | embedding 名次 | hybrid 名次 |
|---|---:|---:|---:|
| `sympy/polys/factortools.py` | 216 | 3 | 11 |

★ 标记 gold 文件。名次从 1 开始。

## 运行

- run 目录：`runs/cr2e/round2/sympy__sympy-19040/r1`
- 变体：`CR2E-T2`，模型调用 40 次，停止原因 `max_model_calls`，费用 0.575 元

### 轨迹

| 步 | 工具 | 参数摘要 | 成功 |
|---:|---|---|---|
| 1 | `retrieve_code` | query: factor with extension=True drops a factor, multivariate factor over extension，k=5 | 是 |
| 1 | `search_code` | pattern: def factor_list\|def _factor\|extension in sympy/polys/factortools.py | 是 |
| 2 | `read_file` | sympy/polys/factortools.py (offset=1120, limit=80) ★ | 是 |
| 2 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * x,y=symbols(' | 是 |
| 3 | `read_file` | sympy/polys/factortools.py (offset=1060, limit=60) ★ | 是 |
| 3 | `search_code` | pattern: def dmp_trial_division\|def dup_trial_division in sympy/polys | 是 |
| 4 | `search_code` | pattern: trial_division in sympy/polys | 是 |
| 5 | `read_file` | sympy/polys/factortools.py (offset=1120, limit=60) ★ | 是 |
| 6 | `read_file` | sympy/polys/factortools.py (offset=78, limit=50) ★ | 是 |
| 7 | `run_command` | cd /workspace && python -c " from sympy import * x,y=symbols('x y') z=expand((x- | 是 |
| 8 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.polytools impo | 是 |
| 9 | `search_code` | pattern: extension in sympy/polys/polytools.py | 是 |
| 10 | `search_code` | pattern: def _factor\|def factor_list\|def _generic_factor_list\|def _symbolic_factor_list in sympy/polys/polytools.py | 是 |
| 11 | `run_command` | cd /workspace && grep -n "def _factor\\|def factor_list\\|def _generic_factor_list | 是 |
| 12 | `read_file` | sympy/polys/polytools.py (offset=5894, limit=130) | 是 |
| 13 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.polytools impo | 否 |
| 14 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.polytools impo | 是 |
| 15 | `run_command` | cd /workspace && grep -n "def factor_list" sympy/polys/polytools.py && sed -n 32 | 是 |
| 16 | `run_command` | cd /workspace && grep -n "def factor_list" sympy/polys/polyclasses.py | 是 |
| 17 | `read_file` | sympy/polys/polyclasses.py (offset=780, limit=30) | 是 |
| 18 | `run_command` | cd /workspace && grep -n "def dmp_factor_list\b" -A 60 sympy/polys/factortools.p | 是 |
| 19 | `run_command` | cd /workspace && grep -n "def dmp_ext_factor" -A 90 sympy/polys/factortools.py | 是 |
| 20 | `run_command` | cd /workspace && grep -n "def dmp_trial_division" -A 30 sympy/polys/factortools. | 是 |
| 21 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 是 |
| 22 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.polytools impo | 是 |
| 23 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 是 |
| 24 | `run_command` | cd /workspace && grep -n "def dmp_ext_factor" -A 95 sympy/polys/factortools.py | 是 |
| 25 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 否 |
| 26 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 是 |
| 27 | `run_command` | cd /workspace && grep -n "def dmp_sqf_part" -A 25 sympy/polys/sqfreetools.py | 是 |
| 28 | `run_command` | cd /workspace && sed -n 254,275p sympy/polys/sqfreetools.py | 是 |
| 29 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 否 |
| 30 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 否 |
| 31 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 否 |
| 32 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 是 |
| 33 | `run_command` | cd /workspace && grep -n "def dmp_sqf_part" -A 30 sympy/polys/sqfreetools.py | 是 |
| 34 | `run_command` | cd /workspace && grep -n "def dmp_gcd" -A 40 sympy/polys/euclidtools.py | 是 |
| 35 | `run_command` | cd /workspace && grep -n "def dmp_inner_gcd" -A 45 sympy/polys/euclidtools.py | 是 |
| 36 | `run_command` | cd /workspace && grep -n "def _dmp_inner_gcd" -A 40 sympy/polys/euclidtools.py | 是 |
| 37 | `run_command` | cd /workspace && grep -n "def dmp_ff_prs_gcd" -A 40 sympy/polys/euclidtools.py | 是 |
| 38 | `run_command` | cd /workspace && grep -n "def _dmp_ff_trivial_gcd" -A 30 sympy/polys/euclidtools | 是 |
| 39 | `run_command` | cd /workspace && sed -n 938,1000p sympy/polys/euclidtools.py | 是 |
| 40 | `run_command` | cd /workspace && python -c " from sympy import * from sympy.polys.domains import | 是 |

### retrieve_code 的返回

1. query：factor with extension=True drops a factor, multivariate factor over extension（k=5）
   - `sympy/polys/factortools.py` ★：`dmp_ext_factor` 1138–1165；`dmp_gf_factor` 1180–1182
   - `sympy/polys/polytools.py`：`factor` 6258–6356；`_generic_factor` 6016–6022
   - `sympy/core/expr.py`：`Expr.factor` 3586–3589；`Expr.as_ordered_factors` 1084–1086
   - `sympy/polys/polyclasses.py`：`DMP.factor_list` 794–797；`GenericPoly._perify_factors` 26–38
   - `sympy/integrals/risch.py`：`DifferentialExtension._log_part` 549–598；`DifferentialExtension._exp_part` 442–547

### 最终改动的文件

（没有改动）

### grader 结论

- 解出：否（status `fail`，grader_status `fail`）

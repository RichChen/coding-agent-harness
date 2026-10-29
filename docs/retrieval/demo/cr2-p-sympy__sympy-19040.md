# 代码检索演示：sympy__sympy-19040

由 `scripts/cr_demo.py` 离线生成，不调用模型。运行：`runs/cr2-main/sympy__sympy-19040/p/r1`。

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

- run 目录：`runs/cr2-main/sympy__sympy-19040/p/r1`
- 变体：`CR2-P`，模型调用 40 次，停止原因 `max_model_calls`，费用 0.579 元

### 轨迹

| 步 | 工具 | 参数摘要 | 成功 |
|---:|---|---|---|
| 1 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * x,y=symbols(' | 是 |
| 1 | `search_code` | pattern: def _symbolic_factor\|def factor\( in sympy/polys/polytools.py | 是 |
| 2 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def factor\\|def _symbolic_factor\\|de | 是 |
| 3 | `read_file` | sympy/polys/polytools.py (offset=5899, limit=130) | 是 |
| 4 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * x,y=symbols(' | 否 |
| 5 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * x,y=symbols(' | 否 |
| 6 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * x,y=symbols(' | 否 |
| 7 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * x,y=symbols(' | 否 |
| 8 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * x,y=symbols(' | 是 |
| 9 | `read_file` | sympy/polys/polytools.py (offset=3273, limit=30) | 是 |
| 9 | `read_file` | sympy/polys/polytools.py (offset=6241, limit=130) | 是 |
| 10 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def _generic_factor\b\\|def _generic_ | 是 |
| 11 | `read_file` | sympy/polys/polytools.py (offset=5899, limit=160) | 是 |
| 12 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 否 |
| 13 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 否 |
| 14 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 是 |
| 15 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def factor_list" sympy/polys/polytoo | 是 |
| 16 | `read_file` | sympy/polys/polytools.py (offset=3273, limit=30) | 是 |
| 17 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def factor_list" sympy/polys/rings.p | 是 |
| 18 | `read_file` | sympy/polys/rings.py (offset=2460, limit=40) | 是 |
| 19 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def dmp_factor_list\b" -A 60 sympy/p | 否 |
| 20 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -rn "def dmp_factor_list\b" sympy/polys/ | 是 |
| 21 | `read_file` | sympy/polys/factortools.py (offset=1258, limit=70) ★ | 是 |
| 22 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def dmp_ext_factor" -A 80 sympy/poly | 是 |
| 23 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 否 |
| 24 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 否 |
| 25 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 否 |
| 26 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 否 |
| 27 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 是 |
| 28 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def dmp_ext_factor" -A 80 sympy/poly | 是 |
| 29 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 是 |
| 30 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def dmp_sqf_part" -A 25 sympy/polys/ | 是 |
| 31 | `read_file` | sympy/polys/sqfreetools.py (offset=252, limit=20) | 是 |
| 32 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; python -c " from sympy import * from sympy.po | 否 |
| 33 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; timeout 300 python -c " from sympy import * f | 否 |
| 34 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; timeout 300 python -c " from sympy import * f | 是 |
| 35 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; timeout 300 python -c " from sympy import * f | 是 |
| 36 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; grep -n "def dmp_sqf_part" -A 25 sympy/polys/ | 是 |
| 37 | `read_file` | sympy/polys/sqfreetools.py (offset=254, limit=10) | 是 |
| 38 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; timeout 300 python -c " from sympy import * f | 否 |
| 39 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; timeout 300 python -c " from sympy import * f | 是 |
| 40 | `run_command` | cd /workspace 2>/dev/null \|\| cd .; timeout 300 python -c " from sympy import * f | 是 |

### retrieve_code 的返回

本次运行没有调用 `retrieve_code`。

### P 组预取列表（附在首条消息之后）

1. `sympy/core/expr.py`
2. `sympy/ntheory/factor_.py`
3. `sympy/core/numbers.py`
4. `sympy/functions/combinatorial/factorials.py`
5. `sympy/polys/polytools.py`

### 最终改动的文件

（没有改动）

### grader 结论

- 解出：否（status `fail`，grader_status `fail`）

# CR-1 结果：SWE-bench Verified 离线代码检索（test 400 题）

日期：2026-10-26。合同：CR-1-CONTRACT。冻结配置：[CR-1-FREEZE](CR-1-FREEZE.md)（tag `cr1-freeze` = `4faaa53`，检索代码提交 `f1d1b92`）。过程记录：CHECKPOINT-CR-1c。

本文所有数字都来自 test 划分的 400 题，冻结配置只跑了一次。dev 上的数字只出现在第 9 节（说明 embedding 是怎么选出来的），不是结果。

- 题数：test 400 题，全部可检索（没有 unretrievable 题），每个检索器都对 400 题打分。
- 检索器：`random`、`path_mention`（路径提及参照，无提及时退回 BM25）、`bm25`（块聚合，`k1=1.2, b=0.75`）、`embedding`（`jinaai/jina-embeddings-v2-base-code` @`516f4baf`，最大长度 512，fp16）、`hybrid`（文件级 RRF，`k=60`，embedding 权重 2）、`hybrid_mention`（对 hybrid 排序做提及优先重排）。
- 区间：按题 paired bootstrap 是主口径；按仓库重抽的 cluster bootstrap 作为稳健性参照。都是 10000 次、种子 `20261018`、95% 区间。差值 = 前者 − 后者。
- 表述：区间跨 0 的写"未检出差异"。
- 数据文件：逐题结果 [`cr1-test-per-instance.jsonl`](cr1-test-per-instance.jsonl)（400 题 × 6 个检索器 = 2400 行：id、repo、是否保留题、各指标、前 10 个文件）；全部统计 [`cr1-test-summary.json`](cr1-test-summary.json)。两者都由 `scripts/cr_report.py` 从冻结运行的输出生成，不重新检索。

## 1. 主比较（预先确定）：hybrid 对 BM25，文件级

| 比较 | n | 按题 paired [95%] | 按仓库 cluster [95%] |
|---|---|---|---|
| hybrid − bm25 hit@5 | 400 | +0.182 [+0.140, +0.225] | +0.182 [+0.138, +0.298] |
| hybrid − bm25 MRR | 400 | +0.165 [+0.129, +0.199] | +0.165 [+0.128, +0.251] |

hit@5：hybrid 0.858，BM25 0.675。MRR：hybrid 0.684，BM25 0.520。两种口径下区间都不含 0。

## 2. 次要比较

### 2.1 文件级

| 比较 | n | 按题 paired [95%] | 按仓库 cluster [95%] | 表述 |
|---|---|---|---|---|
| embedding − bm25 hit@5 | 400 | +0.175 [+0.128, +0.225] | +0.175 [+0.112, +0.337] | 区间不含 0 |
| embedding − bm25 MRR | 400 | +0.145 [+0.099, +0.190] | +0.145 [+0.103, +0.251] | 区间不含 0 |
| hybrid − embedding hit@5 | 400 | +0.007 [−0.022, +0.037] | +0.007 [−0.048, +0.039] | 未检出差异 |
| hybrid − embedding MRR | 400 | +0.020 [−0.007, +0.046] | +0.020 [−0.017, +0.045] | 未检出差异 |
| hybrid_mention − path_mention hit@5 | 400 | +0.145 [+0.107, +0.185] | +0.145 [+0.114, +0.231] | 区间不含 0 |
| hybrid_mention − path_mention MRR | 400 | +0.125 [+0.098, +0.152] | +0.125 [+0.102, +0.182] | 区间不含 0 |
| hybrid_mention − hybrid hit@5 | 400 | −0.025 [−0.045, −0.005] | −0.025 [−0.052, −0.004] | 区间不含 0，hybrid_mention 更低 |
| hybrid_mention − hybrid MRR | 400 | −0.014 [−0.037, +0.010] | −0.014 [−0.036, +0.011] | 未检出差异 |

后两组比较来自合同 11.3。提及优先重排之后，hit@5 比不重排低 0.025；MRR 未检出差异。

### 2.2 函数级（块排名的 hit@5、hit@10）

| 比较 | n | 按题 paired [95%] | 按仓库 cluster [95%] |
|---|---|---|---|
| hybrid − bm25 fn_hit@5 | 400 | +0.203 [+0.155, +0.253] | +0.203 [+0.162, +0.287] |
| hybrid − bm25 fn_hit@10 | 400 | +0.175 [+0.125, +0.225] | +0.175 [+0.132, +0.274] |
| embedding − bm25 fn_hit@5 | 400 | +0.128 [+0.070, +0.185] | +0.128 [+0.080, +0.227] |
| embedding − bm25 fn_hit@10 | 400 | +0.125 [+0.068, +0.180] | +0.125 [+0.069, +0.242] |
| hybrid − embedding fn_hit@5 | 400 | +0.075 [+0.037, +0.115] | +0.075 [+0.035, +0.096] |
| hybrid − embedding fn_hit@10 | 400 | +0.050 [+0.018, +0.083] | +0.050 [+0.021, +0.075] |

hybrid 对 embedding 在文件级未检出差异，在函数级区间不含 0（fn_hit@5 +0.075，fn_hit@10 +0.050）。函数级的 hybrid 是块级 RRF，用的是同一组 `k` 和权重（见 CR-1-FREEZE）。

## 3. 全部题

| 检索器 | n | hit@1 | hit@5 | hit@10 | recall@10 | MRR | fn_hit@5 | fn_hit@10 | 仓库宏平均 hit@5 | 仓库宏平均 MRR |
|---|---|---|---|---|---|---|---|---|---|---|
| random | 400 | 0.007 | 0.022 | 0.043 | 0.036 | 0.021 | 0.005 | 0.018 | 0.057 | 0.050 |
| path_mention | 400 | 0.425 | 0.688 | 0.782 | 0.746 | 0.546 | 0.443 | 0.545 | 0.550 | 0.449 |
| bm25 | 400 | 0.400 | 0.675 | 0.770 | 0.733 | 0.520 | 0.415 | 0.527 | 0.535 | 0.425 |
| embedding | 400 | 0.530 | 0.850 | 0.915 | 0.888 | 0.665 | 0.542 | 0.652 | 0.824 | 0.614 |
| hybrid | 400 | 0.545 | 0.858 | 0.932 | 0.905 | 0.684 | 0.618 | 0.703 | 0.898 | 0.605 |
| hybrid_mention | 400 | 0.540 | 0.833 | 0.922 | 0.895 | 0.671 | 0.583 | 0.657 | 0.879 | 0.601 |

仓库宏平均对 12 个仓库等权。按宏平均 MRR，hybrid（0.605）低于 embedding（0.614）；按宏平均 hit@5，hybrid（0.898）高于 embedding（0.824）。宏平均没有做区间。

## 4. 按仓库（MRR / hit@5）

| 仓库 | n | random | path_mention | bm25 | embedding | hybrid | hybrid_mention |
|---|---|---|---|---|---|---|---|
| astropy/astropy | 17 | 0.015 / 0.000 | 0.491 / 0.647 | 0.544 / 0.706 | 0.779 / 0.941 | 0.711 / 1.000 | 0.599 / 0.882 |
| django/django | 181 | 0.003 / 0.000 | 0.593 / 0.751 | 0.590 / 0.751 | 0.684 / 0.851 | 0.704 / 0.873 | 0.690 / 0.851 |
| matplotlib/matplotlib | 26 | 0.005 / 0.000 | 0.342 / 0.462 | 0.298 / 0.423 | 0.574 / 0.923 | 0.488 / 0.769 | 0.534 / 0.769 |
| mwaskom/seaborn | 1 | 0.042 / 0.000 | 0.125 / 0.000 | 0.125 / 0.000 | 0.143 / 0.000 | 0.200 / 1.000 | 0.200 / 1.000 |
| pallets/flask | 1 | 0.031 / 0.000 | 0.167 / 0.000 | 0.167 / 0.000 | 0.500 / 1.000 | 0.250 / 1.000 | 0.250 / 1.000 |
| psf/requests | 6 | 0.167 / 0.333 | 0.395 / 0.667 | 0.367 / 0.667 | 0.561 / 1.000 | 0.617 / 1.000 | 0.700 / 1.000 |
| pydata/xarray | 17 | 0.173 / 0.235 | 0.480 / 0.765 | 0.415 / 0.647 | 0.634 / 1.000 | 0.662 / 1.000 | 0.735 / 1.000 |
| pylint-dev/pylint | 8 | 0.008 / 0.000 | 0.546 / 0.500 | 0.546 / 0.500 | 0.878 / 0.875 | 0.770 / 0.750 | 0.770 / 0.750 |
| pytest-dev/pytest | 17 | 0.108 / 0.059 | 0.547 / 0.824 | 0.533 / 0.765 | 0.589 / 0.765 | 0.699 / 0.882 | 0.683 / 0.941 |
| scikit-learn/scikit-learn | 28 | 0.013 / 0.036 | 0.728 / 0.821 | 0.720 / 0.893 | 0.778 / 0.964 | 0.841 / 0.964 | 0.772 / 0.893 |
| sphinx-doc/sphinx | 37 | 0.019 / 0.000 | 0.429 / 0.541 | 0.302 / 0.459 | 0.651 / 0.865 | 0.662 / 0.784 | 0.618 / 0.703 |
| sympy/sympy | 61 | 0.010 / 0.016 | 0.543 / 0.623 | 0.490 / 0.607 | 0.596 / 0.705 | 0.656 / 0.754 | 0.655 / 0.754 |
| **仓库宏平均** | 12 个仓库 | 0.050 / 0.057 | 0.449 / 0.550 | 0.425 / 0.535 | 0.614 / 0.824 | 0.605 / 0.898 | 0.601 / 0.879 |

按仓库的数字没有做区间。除 django 外，每个仓库的题数在 1 到 61 之间，seaborn 和 flask 各只有 1 题。表里能看到的情况：

- hybrid 比 embedding 低的仓库：MRR 上有 astropy（0.711 对 0.779）、matplotlib（0.488 对 0.574）、pylint（0.770 对 0.878）、flask（1 题，0.250 对 0.500）；hit@5 上有 matplotlib（0.769 对 0.923）、pylint（0.750 对 0.875）、sphinx（0.784 对 0.865）。
- hybrid 对 BM25：12 个仓库中，hybrid 的 MRR 和 hit@5 都不低于 BM25。
- hybrid_mention 对 hybrid（MRR）：更低的有 astropy（0.599 对 0.711）、scikit-learn（0.772 对 0.841）、sphinx（0.618 对 0.662）、django（0.690 对 0.704）、pytest（0.683 对 0.699）、sympy（0.655 对 0.656）；更高的有 xarray（0.735 对 0.662）、requests（0.700 对 0.617）、matplotlib（0.534 对 0.488）；其余 3 个仓库相同。
- BM25 比 path_mention 低的仓库：MRR 上有 matplotlib、requests、xarray、pytest、sphinx、sympy、django、scikit-learn；astropy 是 BM25 更高。path_mention 在没有提及的题上退回 BM25，所以两者只在有提及的题上不同。

### 4.1 django（181 题，占 test 的 45%）

| 检索器 | n | hit@1 | hit@5 | hit@10 | recall@10 | MRR | fn_hit@5 | fn_hit@10 |
|---|---|---|---|---|---|---|---|---|
| random | 181 | 0.000 | 0.000 | 0.006 | 0.001 | 0.003 | 0.000 | 0.000 |
| path_mention | 181 | 0.464 | 0.751 | 0.840 | 0.804 | 0.593 | 0.519 | 0.608 |
| bm25 | 181 | 0.464 | 0.751 | 0.834 | 0.798 | 0.590 | 0.525 | 0.619 |
| embedding | 181 | 0.558 | 0.851 | 0.917 | 0.892 | 0.684 | 0.591 | 0.680 |
| hybrid | 181 | 0.564 | 0.873 | 0.939 | 0.918 | 0.704 | 0.685 | 0.735 |
| hybrid_mention | 181 | 0.552 | 0.851 | 0.934 | 0.912 | 0.690 | 0.646 | 0.702 |

| 比较 | 子集 | n | 按题 paired [95%] | 按仓库 cluster [95%] |
|---|---|---|---|---|
| hybrid − bm25 hit@5 | django | 181 | +0.122 [+0.061, +0.182] | 单仓库，不适用 |
| hybrid − bm25 MRR | django | 181 | +0.114 [+0.063, +0.166] | 单仓库，不适用 |
| hybrid − bm25 hit@5 | 非 django | 219 | +0.233 [+0.174, +0.292] | +0.233 [+0.163, +0.329] |
| hybrid − bm25 MRR | 非 django | 219 | +0.206 [+0.159, +0.254] | +0.206 [+0.159, +0.274] |

主比较的差值在 django 上（MRR +0.114）比在其余 11 个仓库上（MRR +0.206）小；两个子集的区间都不含 0。django 上 BM25 本身较高（MRR 0.590，非 django 0.462）。非 django 子集的完整表见 `cr1-test-summary.json` 的 `subsets.non_django`。

## 5. 62 道保留题

这 62 题全部在 test 里，CR-2 从这里选题。id 列表在 `cr1-test-summary.json` 的 `subsets.holdout.ids`，逐题结果在 `cr1-test-per-instance.jsonl` 中 `holdout: true` 的行。

| 检索器 | n | hit@1 | hit@5 | hit@10 | recall@10 | MRR | fn_hit@5 | fn_hit@10 | 仓库宏平均 hit@5 | 仓库宏平均 MRR |
|---|---|---|---|---|---|---|---|---|---|---|
| random | 62 | 0.016 | 0.048 | 0.113 | 0.090 | 0.041 | 0.032 | 0.065 | 0.049 | 0.042 |
| path_mention | 62 | 0.403 | 0.661 | 0.774 | 0.735 | 0.529 | 0.452 | 0.532 | 0.660 | 0.527 |
| bm25 | 62 | 0.339 | 0.645 | 0.726 | 0.686 | 0.471 | 0.339 | 0.484 | 0.642 | 0.467 |
| embedding | 62 | 0.581 | 0.887 | 0.903 | 0.873 | 0.706 | 0.581 | 0.726 | 0.886 | 0.706 |
| hybrid | 62 | 0.613 | 0.855 | 0.903 | 0.869 | 0.713 | 0.677 | 0.790 | 0.854 | 0.713 |
| hybrid_mention | 62 | 0.548 | 0.839 | 0.887 | 0.853 | 0.665 | 0.613 | 0.694 | 0.838 | 0.665 |

| 比较 | n | 按题 paired [95%] | 按仓库 cluster [95%] |
|---|---|---|---|
| hybrid − bm25 hit@5 | 62 | +0.210 [+0.097, +0.339] | +0.210 [+0.129, +0.283] |
| hybrid − bm25 MRR | 62 | +0.243 [+0.146, +0.338] | +0.243 [+0.142, +0.354] |

在保留题上，embedding 的 hit@5（0.887）高于 hybrid（0.855），MRR 两者为 0.706 和 0.713。这一子集上 hybrid 对 embedding 没有做区间。

## 6. 路径提及

- 400 题中，213 题的 issue 正文至少提到了一个语料中存在的文件路径；其中 122 题的提及里包含 gold 文件（占 400 题的 30.5%，占有提及题的 57.3%）。
- 其余 187 题没有提及路径。path_mention 在这些题上退回 BM25，hybrid_mention 在这些题上等于 hybrid，所以下表中它们的数字分别相同。

没有提及路径的 187 题：

| 检索器 | n | hit@1 | hit@5 | hit@10 | recall@10 | MRR | fn_hit@5 | fn_hit@10 |
|---|---|---|---|---|---|---|---|---|
| path_mention（= bm25） | 187 | 0.374 | 0.674 | 0.786 | 0.726 | 0.508 | 0.401 | 0.519 |
| bm25 | 187 | 0.374 | 0.674 | 0.786 | 0.726 | 0.508 | 0.401 | 0.519 |
| embedding | 187 | 0.519 | 0.845 | 0.909 | 0.874 | 0.659 | 0.545 | 0.658 |
| hybrid | 187 | 0.561 | 0.872 | 0.925 | 0.885 | 0.697 | 0.631 | 0.711 |
| hybrid_mention（= hybrid） | 187 | 0.561 | 0.872 | 0.925 | 0.885 | 0.697 | 0.631 | 0.711 |

hybrid − bm25：hit@5 +0.198 [+0.134, +0.262]（cluster [+0.150, +0.343]），MRR +0.190 [+0.140, +0.239]（cluster [+0.146, +0.308]）。

有提及路径的 213 题：

| 检索器 | n | hit@1 | hit@5 | hit@10 | recall@10 | MRR | fn_hit@5 | fn_hit@10 |
|---|---|---|---|---|---|---|---|---|
| path_mention | 213 | 0.469 | 0.700 | 0.779 | 0.763 | 0.580 | 0.479 | 0.568 |
| bm25 | 213 | 0.423 | 0.676 | 0.756 | 0.739 | 0.531 | 0.427 | 0.535 |
| embedding | 213 | 0.540 | 0.854 | 0.920 | 0.900 | 0.670 | 0.540 | 0.648 |
| hybrid | 213 | 0.531 | 0.845 | 0.939 | 0.924 | 0.673 | 0.606 | 0.695 |
| hybrid_mention | 213 | 0.521 | 0.798 | 0.920 | 0.905 | 0.648 | 0.540 | 0.610 |

hybrid − bm25：hit@5 +0.169 [+0.113, +0.225]，MRR +0.142 [+0.093, +0.191]。2.1 节中 hybrid_mention 比 hybrid 低的 hit@5 全部来自这 213 题（hybrid_mention 0.798，hybrid 0.845）：提及的文件被排到最前，但其中 91 题的提及不含 gold 文件。

## 7. issue 长度与 embedding 截断（只描述，不检验）

embedding 模型最多读 512 token，超出部分截掉；BM25 读 issue 全文。按冻结 embedding 的 tokenizer 计 issue 长度（含特殊 token），test 中 118 题（29.5%）超过 512。CR-1b 检查点记录的 dev 比例是 38–43%（三个候选模型各自的 tokenizer）。

| 组 | n | embedding hit@5 | bm25 hit@5 | 差 | embedding MRR | bm25 MRR | 差 |
|---|---|---|---|---|---|---|---|
| 超过 512 | 118 | 0.831 | 0.602 | +0.229 | 0.621 | 0.434 | +0.187 |
| 不超过 512 | 282 | 0.858 | 0.706 | +0.152 | 0.683 | 0.556 | +0.127 |

两组中 embedding 的 hit@5 和 MRR 都高于 BM25。长 issue 组两种方法的绝对值都低于短 issue 组，BM25 低得更多。这里只做描述，没有检验组间差异，也不能把它归因于截断本身：长 issue 和短 issue 在仓库构成、问题类型上也可能不同。

## 8. 例子

选法（预先写进 `cr_report.py`）：按每题的 hybrid MRR − BM25 MRR 排序，取最大的 3 题作为"hybrid 赢"，最小的 3 题作为"hybrid 输"；差值相同时按 instance_id 排序。按 MRR，400 题中 hybrid 高于 BM25 的 213 题，相等 141 题，低于 46 题。

"首个 gold 排名"指 gold 文件在文件排名中第一次出现的位置。

### hybrid 赢

| 题 | gold 文件 | 首个 gold 排名 bm25 / embedding / hybrid | BM25 前 3 | hybrid 前 3 |
|---|---|---|---|---|
| sympy__sympy-13798 | `sympy/printing/latex.py` | 31 / 2 / 1 | `sympy/core/function.py`、`sympy/integrals/integrals.py`、`isympy.py` | `sympy/printing/latex.py`、`sympy/physics/quantum/spin.py`、`sympy/core/numbers.py` |
| sphinx-doc__sphinx-8621 | `sphinx/builders/html/transforms.py` | 24 / 1 / 1 | `sphinx/transforms/post_transforms/images.py`、`doc/usage/extensions/example_numpy.py`、`doc/usage/extensions/example_google.py` | `sphinx/builders/html/transforms.py`、`sphinx/writers/text.py`、`sphinx/roles.py` |
| sphinx-doc__sphinx-10614 | `sphinx/ext/inheritance_diagram.py` | 16 / 2 / 1 | `sphinx/cmd/quickstart.py`、`doc/conf.py`、`sphinx/application.py` | `sphinx/ext/inheritance_diagram.py`、`sphinx/cmd/quickstart.py`、`sphinx/ext/graphviz.py` |

issue 开头：

- sympy-13798："latex() and mul_symbol The `latex()` pretty-printing function accepts a `mul_symbol` kwarg that must be one of four choices. I would like to be able to supply my own choice which is not in the list."
- sphinx-8621："kbd role produces incorrect HTML when compound-key separators (-, + or ^) are used as keystrokes"
- sphinx-10614："inheritance-diagram 404 links with SVG … I have created some SVG inheritance diagrams using the `sphinx.ext.inheritance_diagram` plugin."

三题中 embedding 都把 gold 文件排在第 1 或第 2，BM25 排在 16 到 31。

### hybrid 输

| 题 | gold 文件 | 首个 gold 排名 bm25 / embedding / hybrid | BM25 前 3 | hybrid 前 3 |
|---|---|---|---|---|
| pytest-dev__pytest-7205 | `src/_pytest/setuponly.py` | 1 / 13 / 7 | `src/_pytest/setuponly.py`、`src/_pytest/runner.py`、`src/_pytest/fixtures.py` | `src/_pytest/python.py`、`src/_pytest/runner.py`、`src/_pytest/config/__init__.py` |
| django__django-13344 | `django/contrib/sessions/middleware.py`、`django/middleware/cache.py`、`django/middleware/security.py` | 1 / 10 / 6 | `django/middleware/cache.py`、`django/core/handlers/asgi.py`、`django/http/response.py` | `django/core/handlers/base.py`、`django/core/handlers/asgi.py`、`django/utils/deprecation.py` |
| django__django-12039 | `django/db/backends/ddl_references.py` | 1 / 5 / 4 | `django/db/backends/ddl_references.py`、`django/db/models/indexes.py`、`django/db/backends/postgresql/schema.py` | `django/db/backends/base/schema.py`、`django/db/backends/postgresql/schema.py`、`django/db/models/indexes.py` |

issue 开头：

- pytest-7205："BytesWarning when using --setup-show with bytes parameter With Python 3.8.2, pytest 5.4.1 … and this file: ```python import pytest @pytest.mark.parametrize"
- django-13344："Coroutine passed to the first middleware's process_response() instead of HttpResponse. … using ASGI (+ uvicorn in my case), the first middleware …"
- django-12039："Use proper whitespace in CREATE INDEX statements … Creating an index through: index = Index( fields=['-name’], name='idx' ) Will generate the valid but no…"

三题中 BM25 都把 gold 文件排在第 1，embedding 排在 5 到 13；融合后 hybrid 排在 4 到 7。

## 9. embedding 模型的选择接近噪声

这一节的数字来自 dev（100 题，也就是用来选择的那 100 题），只说明选择过程，不是结果。

| 模型 | dev MRR（全部题平均） | dev MRR（仓库宏平均，11 个仓库） |
|---|---|---|
| Qwen3-Embedding-0.6B | 0.656 | 0.701 |
| jina-embeddings-v2-base-code | 0.692 | 0.643 |
| CodeRankEmbed | 0.683 | 0.707 |

合同 11.2 规定按 dev 全部题平均 MRR 选，所以选了 jina。三个模型的全部题平均 MRR 相差不到 0.04；换成仓库宏平均，jina 排最后。这个选择本身接近噪声。本文的 embedding 结果只说明"这一个冻结的 jina 配置"在 test 上的表现，不能据此说 jina 是最好的代码 embedding 模型，也没有在 test 上比较三个模型。

## 10. 局限

- **gold 文件不等于需要读的全部文件。** gold 文件是参考补丁改动的、在 base commit 已存在的非测试 Python 文件。修复一个问题通常还要读调用方、基类、配置等文件，这些文件被检索到不计分；另一方面，排到 gold 文件也不保证能写出正确补丁。
- **模型和 BM25 都可能见过这些仓库。** 这 12 个仓库及其 issue、补丁都是公开的，可能出现在 jina 的训练数据里，数字可能比在未公开仓库上偏高。BM25 用正则分词，`k1=1.2, b=0.75` 是通用默认值，本身没有在这些数据上训练过；但 BM25 和 embedding 共用同一套语料和切块，这些设计是在了解这些公开仓库的前提下做的。
- **只覆盖 Python 文件。** 语料只包含非测试的 `.py` 文件，gold 文件也一样；补丁对文档、配置、C 扩展、测试文件的改动以及新建的文件都不计入。
- **dev 指标是在选择用的 100 题上算的。** embedding 模型和 hybrid 配置都是按 dev 选出来的，dev 上的数字偏乐观；test 400 题才是结果。
- **检索输入只有 issue 正文。** 查询只用 `problem_statement`，不含 `hints_text`（issue 下的讨论）。
- **embedding 截断。** 只有 embedding 截断到 512 token，BM25 用全文。test 中 118 题（29.5%）的 issue 超过 512 token，尾部（常见的是报错栈或复现代码）没有进入 embedding。第 7 节是这两组的描述。
- **按仓库的数字题数少。** 除 django、sympy 外每个仓库不到 40 题，有两个仓库只有 1 题；按仓库的表没有区间。cluster bootstrap 只有 12 个簇，其区间比按题的宽，也不对称。
- **单次运行。** 冻结配置在 test 上只跑了一次，没有多次运行的方差；embedding 在 fp16 下编码，换 GPU 或驱动可能有微小数值差异。

## 11. 复现

- 冻结代码：tag `cr1-freeze`（`4faaa53`）；检索代码与 `f1d1b92` 相同。依赖版本见 `pyproject.toml` 的 `retrieval` 组和 CR-1-FREEZE。
- 运行时需要 `HF_HUB_OFFLINE=1`，且 `HF_HUB_CACHE` 未设置（或指向的 parquet sha256 为 `a45b1fe4…6dcd`）。
- **clone 可能需要代理。** 12 个仓库直接 clone 时大仓库会连接重置；CR-1a 是在命令行加 `git -c http.proxy=http://127.0.0.1:7890 clone --bare` 完成的，`repo_cache.ensure_clone` 本身不带代理参数（CR-1a 检查点第 7 节第 1 条）。
- **HF 权重来源。** 直连 huggingface.co 不通。jina 和 Qwen 的 `model.safetensors` 从 ModelScope 下载，其他文件和 CodeRankEmbed 走本机代理下载；每个大文件都核对了 HF 上对应 revision 的 LFS sha256 和大小，一致后才放进 HF 缓存的 `snapshots/<revision>/`（CR-1b 检查点第 6 节第 1 条）。
- 命令：

```powershell
$env:HF_HUB_OFFLINE='1'
.\.venv\Scripts\python.exe scripts/cr_index.py --embed jina-code --split test --allow-test
.\.venv\Scripts\python.exe scripts/cr_eval.py frozen --split test --allow-test
.\.venv\Scripts\python.exe scripts/cr_report.py
```

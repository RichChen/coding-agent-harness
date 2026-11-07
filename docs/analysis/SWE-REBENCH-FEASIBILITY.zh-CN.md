# SWE-rebench 接入可行性评估

日期：2026-11-07。全程没有调用任何模型，费用为 0。Docker 只用来构建镜像、做隔离扫描，以及给空补丁和标准补丁判分。

## 结论

- **能接，改动量可控。** 试点的 5 道题来自 5 个不同仓库，全部通过了干净镜像构建、答案隔离检查和判分校准。空补丁 3 次都让 F2P 失败，标准补丁 3 次都让 F2P 通过，三次结果一致。
- **官方镜像可以直接当底座。** 里面的 `/testbed` 停在 `base_commit`，没有打测试补丁或标准补丁，git 里没有任何 ref、reflog 或不可达对象。只需要加一层约 1–3 MB 的薄派生层，把布局改成现有 harness 的 `/opt/repo` 和空 `/workspace`，不用像 `sprint5_prepare_main.py` 那样从源码重建。
- **现有判分器不能原样用。** 原样的 `ImageGrader` 在 5 道题里只有 1 道完全可用。原因有两个：
  - 它强制禁用 pytest 插件自动加载，而 SWE-rebench 的仓库普遍依赖 `pytest-socket`、`pytest-mock`、`pytest-asyncio` 这类插件；
  - SWE-rebench 有一部分节点 ID 在空格处被截断了。

  这两点在 `scripts/rebench/grader.py` 里都已解决。要进主代码，需要给 `src/eval/image_grader.py` 加一个开关，这一步要先讨论。
- **污染只能部分规避。** 按 DeepSeek 官方给出的“约不晚于 2025 年 5 月”，860 道题里有 533 道在之后创建。但官方同时写了爬虫采集期是 2024–2026 年，后训练还使用了 agent 环境合成；而 SWE-rebench 全量语料本身就是公开的 RL 训练数据。按保守口径，也就是以模型发布日 2026-09-10 为界，数据集里没有一道题晚于截止日。
- **适合的子集约 500 道。** 静态筛选后，截止日之后还剩 497 道、263 个仓库。现有的 CR-2 是 56 道题，这是近 10 倍的题量。
- **建议两者都用。** SWE-rebench 作为新实验的主题库，CR-2 的 56 题保留作连续性锚点。下一步先做难度校准试点，第一阶段约 60 次运行，按 CR-2 计费估算账本约 29 元、实际扣费约 7 元。前提是插件自动加载这处 `src/` 改动获得同意。

## 1. 数据集

**版本与下载。** 用的是 `nebius/SWE-rebench-leaderboard`，也就是排行榜所用的精选子集，不是全量语料。
- 修订号 `34d5a58864acf91613740a09ec5d205228dcfa39`，最后修改于 2026-07-28T09:59:05Z。
- 由 `scripts/rebench/fetch.py` 通过 Hugging Face 的 parquet 接口下载，文件哈希记在 `runs/rebench/data/fetch.json`。
- `test` 分割共 860 行，等于 15 个月度分割之和，ID 没有重复。来源：HF API、`scripts/rebench/out/inventory.json`。

数据集卡片还提示，2026 年 7 月的评测分割只发布在 Harbor Hub 上，不在 HF。来源：数据集 README，副本在 `runs/rebench/sources/leaderboard-README.md`。本评估没有包含这部分。

**许可。** 数据集本身是 CC-BY-4.0。卡片说明每道题还要遵守所在仓库的许可，并且“为每个实例提供 `license_name`”，但这份 leaderboard parquet 里实际没有这一列。全量接入时，需要从全量 SWE-rebench 数据集或 GitHub API 补上。

**字段对比。** SWE-bench 的 12 个字段全部都有：
- `instance_id`、`repo`、`base_commit`、`patch`（标准补丁）、`test_patch`；
- `problem_statement`、`hints_text`、`created_at`、`version`；
- `FAIL_TO_PASS`、`PASS_TO_PASS`、`environment_setup_commit`。

额外字段有：
- `meta`：是否 lite、改动文件数、未通过的 lite 校验项；
- `install_config`：环境规格，包括 `python`、`install`、`pre_install`（apt）、`packages`、`pip_packages`、`reqs_path`、`env_yml_path`、`test_cmd`、`log_parser`；
- `docker_image`、`image_name`、`interface`；
- `harbor_*` 资源字段，均为空。

环境规格由 `install_config` 取代了 SWE-bench 的 `MAP_REPO_VERSION_TO_SPECS` 常量表，官方 SWE-bench fork 就是读这个字段来建环境的。有 185 道题的 `environment_setup_commit` 和 `base_commit` 不同；有 170 道题的 `hints_text` 非空。来源：数据集 README、`inventory.json`。

**分布。**
- 仓库：860 道题分布在 413 个仓库，其中 249 个仓库只有 1 道题。题最多的是 `tobymao/sqlglot` 41 道、`sympy/sympy` 28、`astropy/astropy` 17、`BerriAI/litellm` 16、`pandas-dev/pandas` 13、`zarr-developers/zarr-python` 12。
- 语言：全部是 Python。
  - Python 版本：3.12 有 291 道，3.9 有 203，3.11 有 193，3.13 有 112，3.10 有 57，3.8/3.6/3.5 合计 4 道。
  - 测试：`log_parser` 全部是 `parse_log_pytest`；858 道的 `test_cmd` 是 pytest，另外 1 道用 hatch，1 道用仓库自带脚本。
- 创建时间：从 2025-01-03 到 2026-05-12，按月如下表。

| 月份 | 题数 | 月份 | 题数 | 月份 | 题数 |
|---|---|---|---|---|---|
| 2025-01 | 109 | 2025-07 | 30 | 2026-01 | 48 |
| 2025-02 | 76 | 2025-08 | 52 | 2026-02 | 57 |
| 2025-03 | 62 | 2025-09 | 50 | 2026-03 | 46 |
| 2025-04 | 40 | 2025-10 | 51 | 2026-04 | 43 |
| 2025-05 | 40 | 2025-11 | 47 | 2026-05 | 21 |
| 2025-06 | 40 | 2025-12 | 48 | | |

其他特征：
- F2P 中位数 1 个，P2P 中位数 28 个；
- 有 86 道题是 lite；
- 有 180 道题需要 apt 预装；
- 有 16 道题用 conda 的 `environment.yml` 建环境。

## 2. 环境

**官方镜像。** 860 道题每道都有一个预构建镜像，统一放在 Docker Hub 的 `swerebench/` 命名空间下，命名形如 `swerebench/sweb.eval.x86_64.<owner>_1776_<repo>-<n>:latest`。全部只有 amd64 架构。另有 49 道题带 `interface` 字段，对应镜像名带 `_interface` 后缀。来源：数据集 README、`docker_image` 列。

**大小。** 用 Docker Hub API 随机抽样 100 个镜像，没有实际拉取，结果见 `scripts/rebench/out/image_sizes.json`：
- 压缩后中位数 1.53 GB，均值 1.91 GB，P90 2.67 GB，最大 7.22 GB；
- 按均值外推，全量约 1.64 TB（压缩后）。

本机拉下来的 5 个镜像解压后是 4.46–4.85 GB，不过它们共享 conda 基础层。5 个官方镜像加 5 个派生镜像，让 Docker 数据盘（`E:\Docker\wsl\DockerDesktopWSL\disk\docker_data.vhdx`）从 105.1 GB 涨到 116.3 GB，平均每题约 2.2 GB。拉取时间上，第一个镜像冷启动用了 18 分钟，后面 4 个一共 8.5 分钟。

注意，镜像都打的是 `:latest` 标签，最后更新时间是 2026-04-23 或 2026-05-17，说明官方会在同一个标签上重新推送。所以必须按 digest 固定引用；`prepare.py` 会记录 `RepoDigests`。

**能不能直接用。** 不能直接用，但只差一层薄改造，不需要从源码重建。逐项检查了 5 个镜像，结果见 `scripts/rebench/out/pilot/*.json` 里的 `official_git_state`：
- `/testbed` 的 HEAD 都等于数据集给的 `base_commit`；
- 工作区没有改动，stash 也是空的；
- 都是 `refs=0`、`reflog=0`，`git fsck --unreachable` 也是 0，说明 git 历史只到 base 为止，看不到未来的提交；
- 镜像里没有残留的 `.patch`、`.diff` 或 `eval.sh`。

按 `docs/sprint/AUDIT-3-CORRECTION.zh-CN.md` 的“干净”标准，也就是 agent 看得到的内容里不能有测试补丁和标准补丁，官方镜像在答案层面是干净的。剩下的差异只在布局上：源码在 `/testbed`（带 `.git`），Python 是 `/opt/conda/envs/testbed/bin/python`，被测包以 editable 方式装在 `/testbed`。

派生层（`scripts/rebench/prepare.py` 里的 `dockerfile()`）做了这些事：
- 把 `/testbed` 去掉 `.git` 后复制到 `/opt/repo`；
- 把 `/testbed` 改成指向 `/workspace` 的符号链接，这样 editable 安装会指向 agent 正在改的那份副本；
- `/workspace` 留空，`/usr/local/bin/python` 链接到 conda 的 Python；
- 记录原生 `.so` 产物和比归档多出来的文件，例如 `*.egg-info`；
- 不再设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD`（原因见第 3 节）。

构建一个约 1 分钟，每个派生层只增加 1–3 MB。

## 3. 与现有 SWE 路径的差距

| 组件 | 能否复用 | 需要做什么 | 工作量 |
|---|---|---|---|
| 公开任务文件（`runs/sprint5-main-public/` 格式） | 原样复用 | `prepare.py` 按同样的字段写出（`task_id`、`issue_text`、`issue_sha256`、`repo_ref`），只含 `problem_statement`，不含提示和补丁 | 已完成 |
| 私有任务文件（`runs/sprint5-main-private/` 格式） | 格式原样复用 | 节点 ID 需要先修复截断、排除沙箱不兼容的用例，结果写进清单行的 `grader_nodeids` / `p2p_excluded`，这两个字段 `run_swe_task.py` 已经支持 | 0.25 天 |
| 答案隔离检查（`build_clean_baseline.py` 里的 `source_only_check`、`filesystem_scan`） | 原样复用 | 5/5 通过；另外加了布局检查（`/workspace` 为空、`/opt/repo` 没有 `.git`）和官方镜像的 git 状态检查 | 已完成 |
| 干净镜像构建（`sprint5_prepare_main.py`） | 不需要 | 改为在官方镜像上加派生层，`swebench_specs` 常量表也用不上了 | 已完成 |
| `scripts/a5_pytest_observer.py` | 不适用 | 这是 pytest 仓库专用的旧观察器（写死了 `testing`、`hypothesis`），现有路径里也只作 legacy 用 | — |
| `scripts/a5_django_observer.py` | 不适用 | SWE-rebench 全部用 pytest，没有 Django 的 runtests 形式 | — |
| 通用 pytest 观察器加 `ImageGrader`（`src/eval/image_grader.py`） | 需要改 | 允许插件自动加载，并传入 `test_cmd` 里的选项（如 `-W`、`-p`）。原型在 `scripts/rebench/grader.py`，进主代码要改 `src/` | 0.5–1 天，需讨论 |
| 节点 ID 选择（`nodeid_selection`） | 需要改 | 现有逻辑会把括号不配对的 ID 直接丢掉；要改成先收集再用前缀匹配修复，原型是 `grader.py` 里的 `repair_nodeids` | 已有原型 |
| `scripts/run_swe_task.py` | 部分复用 | 加一个 `rebench` family，包括 `TEST_COMMANDS`、`PYTHON_PATHS`（`.` 和 `src`）；生成 receipt（`private_source_binding`、测试和标准补丁的路径、镜像 digest、源码归档）和暂存树；`run_task` 里写死了 `ImageGrader`，需要接上面那个开关 | 1 天 |
| agent 执行后端（`BaselineImageBackend`） | 原样复用 | 它没有强制关闭插件自动加载，派生镜像里 agent 自己跑 pytest 是正常的。内存上限 1 GiB，对 pandas、astropy 这类重仓库可能不够，要观察 | 0.5 天（观察和排除） |

**为什么现有判分器不能原样用。** 试点里对每道题都用原样 `ImageGrader` 跑了一次对照，记录在 `calibration.existing_grader`：
- `pypa__twine-1309`：仓库的 `pytest.ini` 写了 `addopts = --disable-socket`。插件不加载时，pytest 报“unrecognized arguments”，退出码 4，一个用例都没收集到。
- `holoviz__param-1117`：配置里开了 `filterwarnings = ["error"]`，又带有 `pytest-asyncio` 的配置项。插件不加载时，这个配置项产生“Unknown config option”警告，被升级成错误，pytest 报 INTERNALERROR。
- `pypa__build-1027`：大量用例 setup 失败，报 `fixture 'mocker' not found`，因为 `pytest-mock` 没有加载。标准补丁下 F2P 是 0/3。
- `delgan__loguru-1451`：F2P 正常，但有 4 个 P2P 因为 ID 截断被丢掉了。
- `pycqa__isort-2491`：完全正常。

在数据集层面，安装命令里显式写了常见 pytest 插件的题至少有 122 道，这是下限，因为插件也可能通过 extras 或 requirements 间接装进来。

**节点 ID 截断。** SWE-rebench 用 `parse_log_pytest` 从 `-rA` 输出中解析 ID，参数化 ID 里一旦带空格就会被截断，例如 `test_http_exception_handling[599-Unknown`，完整的 ID 是 `[599-Unknown Error]`。有 33 道题的 F2P 含截断 ID；在第 5 节的子集里，F2P 有截断的是 19 道，P2P 有截断的是 88 道。

修复的做法是：在叠加了测试补丁的工作区里先收集一次，再把截断的 ID 映射到“截断文本加一个空格”开头的收集结果。试点里共修复 19 个 ID，其中 F2P 2 个，全部找到了匹配。有一个截断 ID 匹配到了 3 个收集结果（`test_check_dependency[False-missing_dep;`），这时全部纳入，和官方解析器把它们当作同一个 key 的行为一致。

## 4. 污染

**截止日期的官方说法。** 来源是 DeepSeek 官方的《Public Summary of Training Content for DeepSeek-V4.1》，最后更新于 2026-09-24，副本在 `runs/rebench/sources/deepseek-v41-training-summary.pdf`：
- “Latest date of data acquisition/collection for model training: The data used to train DeepSeek-V4.1(Flash) includes different datasets from varying time periods, with some data collected approximately no later than May 2025.”
- 爬虫一节：“Period of data collection: Approximately 2024 — 2026.”
- 后训练：“interaction data and feedback … are used to construct agent training environments”，并且使用了自家模型生成的合成数据，用于 coding 等方向。

另外：
- 模型 2026-09-10 上市；
- `deepseek-flash` 现在服务的是 DeepSeek-V4.1-Flash-1017，于 2026-10-17 升级。来源：DeepSeek API 更新日志（<https://api-docs.deepseek.com/zh-cn/updates.md>）和 API 首页（<https://api-docs.deepseek.com/>）。
- CR-2 在 10-27 到 10-29 运行，用的是 1017 版本。

所以“2025 年 5 月”说的只是“部分数据”的截止，不是整体的硬截止。这里分两档计数：

| 口径 | 阈值 | 晚于阈值的题数 | 仓库数 |
|---|---|---|---|
| 官方表述（部分数据约不晚于 2025-05） | created_at ≥ 2025-06-01 | 533 / 860 | 284 |
| 保守（爬虫采集到 2026 年，以模型上市日为界） | created_at ≥ 2026-09-11 | 0 / 860 | 0 |

按官方口径，晚于阈值的题按仓库数前几名是：
- `tobymao/sqlglot` 28、`BerriAI/litellm` 16、`sympy/sympy` 14；
- `astropy/astropy` 10、`schemathesis/schemathesis` 10、`getmoto/moto` 9；
- `tox-dev/tox` 7、`pandas-dev/pandas` 6、`koxudaxi/datamodel-code-generator` 6；
- `conan-io/conan` 5、`deepset-ai/haystack` 5。

完整列表见 `inventory.json` 的 `after_threshold`。

还有一个额外风险：SWE-rebench 全量语料（`nebius/SWE-rebench`）是公开的，本身就是为 agent 的 RL 训练准备的。DeepSeek 写明了会做 agent 环境合成，但没有披露具体用了哪些数据，所以无法排除这些题直接进了后训练。

按保守口径，要拿到真正没见过的题，只能用 2026-09 以后创建的题。HF 上的数据集和 Harbor 的 2026-07 分割都早于这个日期，只能等后续的月度分割。所以 SWE-rebench 带来的是“被记住的可能性更低、题目更多更分散”，不是“确定没被污染”。

## 5. 适合的子集

静态筛选的结果在 `inventory.json` 的 `subset` 字段：

| 筛选条件 | 剩余题数 | 仓库数 |
|---|---|---|
| 全部（都是 Python） | 860 | 413 |
| 直接调用 pytest（不经过 `uv run`、hatch、tox 包装） | 827 | 393 |
| F2P 全部是 pytest 节点 ID | 824 | 392 |
| 不需要 conda `environment.yml` | 808 | 383 |
| 创建于官方截止日之后（≥ 2025-06-01） | 497 | 263 |

没加进筛选条件的有：
- apt 预装：子集里有 49 道，官方镜像已经装好了，不影响；
- ID 截断：已能修复。

还有几项静态看不出来，只能在接入时逐题校准：
- 测试是否必须联网；
- 2 GiB 内存够不够；
- 有没有用例和沙箱冲突。沙箱条件是 uid 1000、只读根文件系统、`/tmp` 带 noexec。`build-1027` 就有 1 个 P2P 在沙箱里失败，root 身份下单独跑是通过的。

试点 5/5 通过，但这 5 道是挑过的纯 Python 小题，不能直接外推。全量校准的通过率我主观估计在 60%–85%，也就是 300–420 道可用。这个区间没有实测依据，要在第 8 节的第一阶段接入后核实。

## 6. 试点结果

5 道题来自 5 个仓库，都是纯 Python，创建时间都晚于官方截止日。其中 `pypa__build-1027` 是特意选的，它依赖 `pytest-mock`。

判分用 `scripts/rebench/grader.py` 跑，空补丁和标准补丁各 3 次，同时用原样 `ImageGrader` 各跑 1 次作对照。

隔离检查的结果每道题都一样：
- `source_only_check` 一致，即归档里的每个文件在 `/opt/repo` 下哈希都相同；
- `filesystem_scan` 用 12 条特征行扫描全镜像，命中 0 次；
- `/workspace` 为空，`/opt/repo` 没有 `.git`；
- 官方镜像的 git 状态都是干净的。

| 题目 | 创建 | 镜像来源 | 原样 `ImageGrader`（空 / 标准） | 空补丁 F2P ×3 | 标准补丁 F2P ×3 | 标准补丁 P2P | 遇到的问题 |
|---|---|---|---|---|---|---|---|
| `pypa__twine-1309` | 2026-04-17 | 官方镜像加派生层 | 退出码 4，0 个用例 | 0/1 | 1/1 | 3/3 | `--disable-socket` 依赖插件；F2P 和 1 个 P2P 被截断，已修复 |
| `delgan__loguru-1451` | 2026-03-25 | 同上 | 0/2 / 2/2，但丢了 4 个 P2P | 0/2 | 2/2 | 22/22 | 4 个 P2P 被截断，已修复 |
| `pycqa__isort-2491` | 2026-03-12 | 同上 | 0/1 / 1/1，P2P 73/73 | 0/1 | 1/1 | 73/73 | 没有 |
| `holoviz__param-1117` | 2026-03-25 | 同上 | 退出码 3，INTERNALERROR | 0/2 | 2/2 | 94/94 | `filterwarnings=error` 加上 pytest-asyncio 配置项 |
| `pypa__build-1027` | 2026-04-10 | 同上 | 标准补丁 F2P 0/3，`mocker` 缺失 | 0/4 | 4/4 | 63/64 | 13 个 ID 被截断，已修复；1 个 P2P 在沙箱里失败，应排除 |

另外：
- 3 次判分里，每个 F2P 的结果都完全一致；
- 每道题的流程（派生构建、隔离扫描、7 次判分）在镜像已拉好的情况下约 2 分钟。

## 7. 全量接入工作量

| 任务 | 工作量 | 说明 |
|---|---|---|
| 把 `prepare.py` 做成批处理：选题、断点续跑、按 `docs/sprint/clean-images`、`main-calibration` 格式写记录、生成清单行、receipt 和暂存树 | 1 天 | 镜像、隔离和校准部分已经验证过 |
| 判分器：给 `ImageGrader` 加插件自动加载和 `test_cmd` 选项的开关，补测试 | 0.5–1 天 | 改 `src/`，需要先讨论 |
| `run_swe_task.py` 接入 `rebench` family | 1 天 | `TEST_COMMANDS`、`PYTHON_PATHS`、receipt、判分器开关 |
| 沙箱兼容：标准补丁下没通过的 P2P 自动排除；观察 1 GiB 和 2 GiB 内存上限 | 0.5 天 | 沿用 sprint5 的 `p2p_excluded` |
| 补齐仓库许可（`license_name`） | 0.25 天 | 从全量数据集或 GitHub API 取 |
| 用 `--mock`（不调用模型）跑 5 道端到端，复核隔离 | 0.5 天 | |
| 机器时间：接入 60 道题 | 约 4–5 小时 | 每题拉取约 2 分钟加处理约 2 分钟；磁盘约 130 GB（E 盘剩 600 GB 以上） |

合计约 4–5 人日，外加约半天机器时间（按 60 道题算）。接入 500 道全量需要约 1.1 TB 磁盘，超过了 E 盘剩余空间，要分批接入、校准完就清理镜像，或者只保留选中的题。

## 8. 难度校准试点方案

**先验。** SWE-rebench 官方榜单（<https://swe-rebench.com/>）在 2026-05-15 到 2026-07-01 这个窗口上：
- DeepSeek-V4 Pro（thinking high）解出率 40.2%，每题约 400 万 token；
- 第三方汇总给 DeepSeek V4 Flash 的是 38.4%（2026-03 到 05 的窗口，<https://agmodb.com/models/deepseek-v4-flash>）。

我们的 harness 条件差得多：关闭 thinking，最多 40 次调用，单次 1 元上限，准入约 4 万 provider token。参照一下 SWE-bench Verified 上的表现：同一套 harness 在 CR-2 的 N 组是 45.7%。所以推测 SWE-rebench 子集上的解出率在 15%–35%，有可能低于 20% 的下限。

**难度代理指标。** 子集里改动只涉及单个文件的有 102 道；单文件、补丁不超过 2.5 KB、不需要 apt 的有 66 道（57 个仓库）。子集的补丁大小中位数是 2.5 KB。

**方案。**
1. **第一阶段**：30 道题，分两层。
   - 15 道从上面那 66 道“易”题里抽，15 道从子集其余部分抽，每个仓库最多 1 道；
   - 每题跑 2 次 N 配置，共 60 次。
   - 30 道题时，解出率的 95% 区间半宽约 ±15–17 个百分点，足够判断落在 20–60% 区间的哪一侧，也能看出两层之间的差别。
2. **按第一阶段结果决定**：
   - 总体落在 20–60%：扩到 60 道、每题 3 次，共 180 次，作为固定题池；
   - 低于 20%：只从“易”层取题；
   - 高于 60%：去掉“易”层的限制。

**费用。** 按 CR-2 实测计费（`docs/retrieval/CR-2-RESULTS.zh-CN.md` 的分组费用表和第 10 节）：
- N 组账本口径 0.422 元/次；
- 实际扣费 137.95 元，是账本 547.39 元的 1/3.97，折合约 0.106 元/次；
- 退役重跑约占运行数的 14%。

| 阶段 | 运行次数 | 账本口径（含 14% 重跑） | 实际扣费估算 | 单次 1 元上限时的最坏情况 |
|---|---|---|---|---|
| 第一阶段 | 60 | 约 29 元 | 约 7 元 | 68 元 |
| 第二阶段（如果进入） | 180 | 约 87 元 | 约 22 元 | 205 元 |

SWE-rebench 的题可能更难，跑满 40 次调用的比例可能更高，单次费用可能高于 CR-2 的均值。所以第一阶段要先记录实际的单次费用，再批第二阶段的预算。第一阶段之前，需要先接入约 30 道题，机器时间约 2 小时，磁盘约 70 GB。

## 9. 建议：两者都用

**SWE-rebench 作为新实验的主题库。** 理由：
- **题量**：约 500 道，分布在 263 个仓库，CR-2 只有 56 道。A5、A6 和 CR-2 都已经说明，56 道题测不出中等大小的差异，更大的题量可以直接缓解这个问题；
- **时间**：题目更新，被预训练记住的可能性更低；
- **接入成本**：镜像现成而且干净，接入成本已经被试点验证过。

**CR-2 的 56 道题保留作锚点。** 理由：
- 它有完整的历史可以对比（A5 → CR-2 已经观察到 −22.7 个百分点的版本漂移）；
- 每次换模型或换 harness，都可以用它做回归检查。

**风险要写清楚。** SWE-rebench 不能宣称“没有污染”。按保守口径，现有的题全部早于模型上市；全量语料又是公开的 RL 数据。报告结论时应该把创建月份作为协变量，或者等 2026-09 以后的月度分割出来，再做一次“确定没见过”的检验。

**下一步。** 先讨论第 3 节里 `src/eval/image_grader.py` 的改动，也就是插件自动加载开关。同意之后，接入 30 道题，跑第一阶段校准。

## 复现

```
python scripts/rebench/fetch.py          # 下载数据集到 runs/rebench/data
python scripts/rebench/inventory.py      # 统计、污染计数、子集 -> scripts/rebench/out/inventory.json
python scripts/rebench/image_sizes.py --sample 100   # Docker Hub 抽样 -> out/image_sizes.json
docker pull swerebench/sweb.eval.x86_64.<owner>_1776_<repo>-<n>:latest   # 试点 5 个
python scripts/rebench/prepare.py --repeats 3        # 派生、隔离、校准 -> out/pilot/*.json
```

数据集、镜像、构建上下文和任务文件都在 `runs/rebench/` 下，不入库。

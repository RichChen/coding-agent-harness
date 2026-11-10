# FIX-PLAN-12 任务参照位置表（问题二准备）

> 状态更新（2026-11-10）。本文保留 11-09 历史记录。问题二已由离线 gold 参照分类和人工抽样复核取代，当前结果见 [FIX-12-NO-EDIT-GOLD.zh-CN.md](FIX-12-NO-EDIT-GOLD.zh-CN.md)。事后离线使用 gold 合法，须与模型运行环境隔离。下阶段已选 U、重复提醒、一句话解释、UT 四组，仅授权设计，尚未批准实验。
> 口径更正。三组每次运行费用上限均为 4 元，U 未放宽费用上限；费用为 usage 按峰价核算，未核实实际扣款。UT 的约 241 低于预定报告阈值 500，该阈值并非天然的有效思考界线。


日期：2026-11-09

这份表只使用公开 issue 文本、题目基线源码、公开测试线索和 FIX-12 运行中可见的文件路径。没有读取 gold patch、PR diff 或答案文件。它是 P4 “找到后没有编辑”分类器的准备材料，不是最终分类结果。

可靠程度的含义如下。高表示 issue 直接点名了类、函数或公开复现路径，并且基线源码中能找到对应定义。中表示 issue 和源码能把范围缩小到少数函数，但仍需要人工核对真正修改点。低表示目前只能定位到模块或功能区域，不能用于严格的“找到函数后没有编辑”判断。

| 任务 | 候选文件和函数 | 依据 | 可靠程度 | 主要不确定性 |
|---|---|---|---|---|
| BrianPugh/cyclopts-609 | `cyclopts/bind.py`：`_parse_kw_and_flags`、`_preprocess_positional_tokens`；`cyclopts/argument/_argument.py`：参数匹配 | issue 直接讨论 `--` 结束选项和 `*args` 接收 `--help`，基线有对应解析入口 | 中 | 需要确认最终行为是否还经过 App 分派层 |
| Kozea/WeasyPrint-2494 | `weasyprint/layout/column.py`；必要时 `weasyprint/layout/block.py` | issue 是多栏分页和文本布局错误，源码有独立 column 布局模块 | 低 | issue 没有给出函数名，可能跨 column、block 和分页逻辑 |
| agronholm/anyio-935 | `src/anyio/lowlevel.py`：`RunVar.__init__`、`get`、`set`、`reset` | issue 直接点名同名 RunVar 的键冲突，源码中有唯一 RunVar 实现 | 高 | 仍需确认后端包装是否参与行为 |
| apache/iceberg-python-3295 | `pyiceberg/expressions/__init__.py`：`And.__new__`、`Or.__new__`、`Not.__new__` | issue 直接点名三种表达式和 `copy.deepcopy`，源码定义位置明确 | 高 | 需要确认 Pydantic 深拷贝路径是否在表达式基类之外 |
| astropy/astropy-18818 | `astropy/io/fits/connect.py`：表写出入口；`astropy/io/fits/column.py`：列转换 | issue 直接指向 masked integer arrays 写 FITS，源码有表到 FITS 的连接和列转换 | 低 | 当前材料不能把问题缩到单一函数 |
| aws/bedrock-agentcore-sdk-python-279_interface | `bedrock_agentcore/memory/integrations/strands/session_manager.py`：`AgentCoreMemorySessionManager`；上下文标签生成处 | issue 直接讨论 `<user_context>` 与 `<retrieved_memory>` 标签和插入位置，测试也出现 `AgentCoreMemorySessionManager` | 中 | 需要确认标签常量和消息拼装是否分散在多个文件 |
| cal-itp/benefits-3330 | `benefits/enrollment_littlepay/enrollment.py`：`enroll`、`_get_group_funding_source`；相关 Littlepay client | issue 和基线异常都指向 funding source 与 concession group 关联 | 中 | 需要确认 409 的重复注册分支和 API client 分层 |
| conan-io/conan-19604 | `conan/internal/model/cpp_info.py`：`_Component.serialize`、`CppInfo.serialize` | issue 直接点名 `_Component`、`PackageType` 和序列化，源码定义明确 | 高 | 需要确认兼容字符串转换是否在调用方完成 |
| dask/fastparquet-961 | `fastparquet/writer.py`：`convert`；时间单位转换表 | issue 直接给出 `convert(data, se)` 和 TIMESTAMP_MICROS，源码定义明确 | 高 | 需要确认追加写入的调用路径是否另有转换 |
| feder-observatory/stellarphot-526 | `stellarphot/table_representations.py`；单位赋值和 magnitude 列构造 | issue 给出 `mag_inst`、`mag_error` 及单位关系，源码模块可定位 | 低 | 没有足够材料确定真正修改函数 |
| jsonpickle/jsonpickle-587 | `jsonpickle/pickler.py`：`_flatten_obj_attrs`、`_flatten_newstyle_with_slots` | issue 直接涉及 `__slots__` 和 `_jsonpickle_exclude`，运行日志也读到 `_flatten_obj_attrs` | 高 | 需要区分属性收集和 exclude 传播两个层次 |
| marshmallow-code/marshmallow-2925_interface | `src/marshmallow/fields/number.py`：`Number` 及具体数字字段基类；字段实例化检查 | issue 直接要求抽象数字字段不可直接实例化 | 中 | 具体抽象判定可能在字段元类或通用 Field 中 |
| microsoft/Agents-for-python-264 | `libraries/microsoft-agents-hosting-core/.../app/oauth/_sign_in_state.py`：`_SignInState`；状态序列化/存储路径 | issue 直接点名 `_SignInState`、`Activity` 和 JSON 序列化，源码均可找到 | 中 | 需要确认修复应在状态模型序列化还是 storage adapter |
| microsoft/fabric-cicd-394 | 平台文件处理和 logical ID 替换模块 | issue 直接说明空 `logicalId` 导致替换失败 | 低 | 目前只能定位到功能区，尚未取得可靠函数级参照 |
| microsoft/qcodes-8039 | `qcodes/instrument/base.py` 及属性访问/错误包装路径 | issue 直接讨论 Instrument property 抛错时的异常链 | 低 | 可能涉及 descriptor、Instrument 或日志格式化多个层次 |
| newton-physics/newton-1653 | `newton/_src/solvers/mujoco/solver_mujoco.py`：geom 参数同步；MJCF 导入路径 | issue 直接点名 `geom_margin`、`geom_rbound`、`geom_aabb`，源码和测试都有对应字段 | 中 | 需要区分初始转换和运行时更新两个路径 |
| omni-us/jsonargparse-770 | `jsonargparse/_typehints.py`：Callable 类型检查和导入；类路径解析 | issue 直接讨论 `torch.argmax` 作为 class path 与 Callable 类型检查 | 中 | 真实修改点可能在类型检查器或 class path resolver |
| openai/openai-agents-python-2144 | `src/agents/models/chatcmpl_stream_handler.py`：reasoning summary 处理；`openai_chatcompletions.py`：请求映射 | issue 直接点名 LiteLLM 缺少 reasoning.summary，源码有对应流处理逻辑 | 高 | 需要确认 LiteLLM 差异是否在 provider adapter 而非通用 handler |
| openatx/adbutils-188 | `adbutils/_adb.py`：`Device.reverse_list`；`adbutils/_device_base.py` 同名路径 | issue 直接点名 `reverse_list`、`parts` 和 `ReverseItem`，两个实现可比对 | 高 | 需要确认运行时使用哪个 Device 类 |
| optuna/optuna-6428 | `optuna/samplers/_partial_fixed.py`：`PartialFixedSampler`；`optuna/trial/_trial.py`：`suggest_float`；TPE sampler | issue 直接点名 PartialFixedSampler、TPE 和 suggest_float | 高 | 需要确认错误状态来自 sampler 交互还是 trial 分发 |
| py-pdf/pdfly-219 | pdfly 的 `2-up` 命令和页面合并模块 | issue 直接点名命令和奇数页丢页 | 低 | 尚未取得稳定的函数级参照 |
| pyccel/pyccel-2454 | Pyccel 列表指针/重分配转换模块 | issue 讨论列表重分配后指针行为，当前只能定位到编译器指针处理区域 | 低 | 需要更多公开测试或基线失败信息 |
| pycqa/isort-2491 | `isort/identify.py`：注释与 import 识别；`isort/comments.py`：注释解析；格式化路径 | issue 给出带 alias 和行尾 comment 的具体语法，源码有对应解析函数 | 中 | 需要确认错位发生在识别、排序还是输出格式化 |
| pydata/xarray-10838 | `xarray/core/merge.py`：`combine_attrs`/冲突属性处理；NetCDF 写出路径 | issue 直接点名 `drop_conflicts`、NaN 属性和 concat | 中 | 需要区分属性合并与 NetCDF 序列化的责任边界 |
| python-wheel-build/fromager-653 | `docs/develop.md` 及文档构建配置 | issue 本身是补充构建文档，参照区域是文档而非单一 Python 函数 | 低 | 不适合套用“找到函数后不编辑”分类 |
| scrapy/scrapy-7351_interface | `scrapy/pqueues.py`：`DownloaderAwarePriorityQueue.pop`、`_active_downloads` | issue 直接点名队列、公平轮转和 active-download tie，源码定义明确 | 高 | 需要确认 tie-break 状态是否在队列初始化或 slot 列表生成处维护 |
| stan-dev/cmdstanpy-801 | `cmdstanpy/utils/stancsv.py`：`csv_bytes_list_to_numpy`；`cmdstanpy/stanfit/mcmc.py`：draws 组装 | issue 直接点名无 draws、iter_sampling=0 和两个失败位置 | 高 | 需要区分 CSV 解析和二维 draws 分配两个修复点 |
| stanfordnlp/dspy-9478_interface | DSPy embedding retriever 的 `Embeddings` 类及返回值组装 | issue 直接要求返回相似度分数，公开模块可定位 | 中 | 需要确认接口定义和具体 retriever 实现是否分离 |
| sympy/sympy-28738 | `sympy/concrete/summations.py`：空范围求和；矩阵表达式与 `ZeroMatrix` | issue 直接给出 `Sum(...).doit()` 和 `ZeroMatrix` 期望 | 中 | 需要确认空范围判定在 Sum、矩阵加法或 simplification 路径 |
| tobymao/sqlglot-5233 | Athena 方言解析器和字符串/注释字面量处理 | issue 直接给出 Athena 反斜杠转义 comment 语法 | 中 | 需要区分词法、方言 parser 和 SQL 生成器 |

## 当前能做什么

这 30 道题都有候选模块。约 22 道题已经有中或高可靠度的函数级候选，8 道题目前只能到模块级或文档级。这个数量只是人工复核前的工作清单，不能直接作为 P4 分类器的严格参照表。

问题二的最小下一步是逐题人工复核这 22 个中高可靠候选，确认每题最多一到两个参照函数，并把依据、可靠程度和表的哈希保存下来。低可靠题目单独作为“只能做模块级分析”的分层。复核完成后才能迁移 P4 分类器，统计每个配置的第一次编辑、找到后不编辑、资源不足、没有找到和其他结束原因。

本表没有读取 gold patch、PR diff 或答案文件，也没有修改运行目录、配置、rebench 脚本或源码。

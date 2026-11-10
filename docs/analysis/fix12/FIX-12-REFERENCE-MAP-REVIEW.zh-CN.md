# FIX-PLAN-12 参照表有界复核

> 状态更新（2026-11-10）。本文保留 11-09 历史记录。问题二已由离线 gold 参照分类和人工抽样复核取代，当前结果见 [FIX-12-NO-EDIT-GOLD.zh-CN.md](FIX-12-NO-EDIT-GOLD.zh-CN.md)。事后离线使用 gold 合法，须与模型运行环境隔离。下阶段已选 U、重复提醒、一句话解释、UT 四组，仅授权设计，尚未批准实验。
> 口径更正。三组每次运行费用上限均为 4 元，U 未放宽费用上限；费用为 usage 按峰价核算，未核实实际扣款。UT 的约 241 低于预定报告阈值 500，该阈值并非天然的有效思考界线。


日期：2026-11-09

本复核只依据 `FIX-12-REFERENCE-MAP.zh-CN.md` 已写出的 issue、基线源码定位和公开测试线索。未扫描 runs，未读取 gold patch、PR diff 或答案文件，未调用模型。

## 已确认可进入人工复核的任务 ID

以下 22 个任务在原表中已有中或高可靠的函数级候选，可进入逐题人工确认：

- BrianPugh/cyclopts-609
- agronholm/anyio-935
- apache/iceberg-python-3295
- aws/bedrock-agentcore-sdk-python-279_interface
- cal-itp/benefits-3330
- conan-io/conan-19604
- dask/fastparquet-961
- jsonpickle/jsonpickle-587
- marshmallow-code/marshmallow-2925_interface
- microsoft/Agents-for-python-264
- newton-physics/newton-1653
- omni-us/jsonargparse-770
- openai/openai-agents-python-2144
- openatx/adbutils-188
- optuna/optuna-6428
- pycqa/isort-2491
- pydata/xarray-10838
- scrapy/scrapy-7351_interface
- stan-dev/cmdstanpy-801
- stanfordnlp/dspy-9478_interface
- sympy/sympy-28738
- tobymao/sqlglot-5233

其中高可靠候选为：agronholm/anyio-935、apache/iceberg-python-3295、conan-io/conan-19604、dask/fastparquet-961、jsonpickle/jsonpickle-587、openai/openai-agents-python-2144、openatx/adbutils-188、optuna/optuna-6428、scrapy/scrapy-7351_interface、stan-dev/cmdstanpy-801。

## 降级任务 ID

以下任务的原表依据只能稳定定位到模块、功能区域或文档区域，不能支持严格的“找到函数后没有编辑”判断，保留为低可靠：

- Kozea/WeasyPrint-2494
- astropy/astropy-18818
- feder-observatory/stellarphot-526
- microsoft/fabric-cicd-394
- microsoft/qcodes-8039
- py-pdf/pdfly-219
- pyccel/pyccel-2454
- python-wheel-build/fromager-653

## 仍需人工决定的项目

以下 12 个任务虽可进入人工复核，但原表明确保留了跨函数、跨层或调用路径不确定性。人工复核时应决定最终最多一到两个参照函数，并确认是否需要保留相邻适配层：

- BrianPugh/cyclopts-609：解析入口与 App 分派层的边界
- aws/bedrock-agentcore-sdk-python-279_interface：标签常量与消息拼装是否分散
- cal-itp/benefits-3330：重复注册分支与 API client 分层
- marshmallow-code/marshmallow-2925_interface：抽象判定所在的字段元类或通用 Field 层
- microsoft/Agents-for-python-264：状态模型序列化与 storage adapter 的责任边界
- newton-physics/newton-1653：初始转换与运行时更新路径
- omni-us/jsonargparse-770：类型检查器与 class path resolver 的责任边界
- pycqa/isort-2491：识别、排序与输出格式化的具体错位点
- pydata/xarray-10838：属性合并与 NetCDF 序列化的责任边界
- stanfordnlp/dspy-9478_interface：接口定义与具体 retriever 实现是否分离
- sympy/sympy-28738：Sum、矩阵加法与 simplification 的空范围处理边界
- tobymao/sqlglot-5233：词法、Athena 方言 parser 与 SQL 生成器的责任边界

无其他任务可从原表现有证据提升为函数级候选。

## 无泄漏声明

本复核没有读取或推断 gold patch、PR diff、答案文件或 runs 内容，没有调用模型，也没有修改原表、runs、configs、scripts、rebench 或源码。仅新建本复核文档。

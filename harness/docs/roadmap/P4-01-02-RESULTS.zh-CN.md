# P4-01/02：公开目标检查冻结与基点复现

日期：2026-10-30。主工作区 `D:/crq/src/coding-harness`，接任务时 HEAD 为 `b02d64f`。本报告只覆盖已批准的零求解 API 准备；没有启动协议 probe、真实求解、GPU、下载或安装，没有写 Sphinx 修复补丁，也没有 git add/commit/reset/stash/push。

## 结论

原始基点已实际复现公开 issue：HTML→EPUB 在默认关闭和显式 False 下都成功构建，却仍在 EPUB 目录和 EPUB 归档中包含模块源码页。新鲜默认 EPUB 与显式 True 两个对照成立。既有 B 回归基线另在新副本实际执行，8 passed，成功绑定。三只本次 owned 容器均清理成功，随后按精确 ID inspect 确认不存在。

因此 P4-01/02 的目标输入、冻结检查、基点失败与候选入口可交给主会话集成审查；不表示 P4-03/04 已完成，更不自行放行付费调用。目标检查与 B 的 regression_only、SWE solved 保持分开。

## 文件和公开输入

- [public_target.py](../../src/validation/public_target.py)：独立宿主编排；使用固定 SphinxProfile 的环境探测、共享 CaptureProvider、materialize、WorkspaceCoordinator 与干净镜像。没有修改生产 B 的契约、selector、completion 或 loop。
- [check_public_target.py](../../scripts/check_public_target.py)：冻结、完整任务组装、原始基点与终态候选 CLI。
- [公开材料目录](../../tests/fixtures/p4_sphinx8721/)：task.md、runner.py、conf.py、index.rst、p4_example.py、check-manifest.json。普通 example 模块只供文档生成源码页，不是给 Sphinx 注入故障或预写修复。
- [test_public_target.py](../../tests/test_public_target.py)：无模型/Docker 的目标判据、EPUB 结构、终态来源和冻结/输入测试。
- [小证据](evidence/p4-01-02/preflight-summary.json)：逐场景结果、身份、耗时、回归记录与三只 owned 容器的缺失核验。

来源只有已分离的 [公开 JSON](../../runs/sprint5-main-public/sphinx-doc__sphinx-8721.json)、[原始 issue #8720](https://github.com/sphinx-doc/sphinx/issues/8720) 和固定 revision 的公开源码。没有读取 runs/*private、gold/test patch、隐藏测试或历史求解补丁；8721 是本地资产沿用的关联 PR 编号。

`--print-task --task-output` 写出新的 UTF-8 文件，内容为：原始 issue_text 原文、标明宿主新增来源的任务约束与三类验收、冻结 check_id、完整 runner.py/conf.py/index.rst/p4_example.py。不是让模型读取宿主路径，也没有将宿主材料塞进候选树。原始需求与追加的公开验收说明明确分开。

实际已组装输入：`D:/crq/artifacts/p4-sphinx8721-public-input-20261030-01/task.txt`，11,103 bytes，SHA-256：

```text
d3098b501ddcf1f5446607638564c17784586661b2daa997876f249098cc5d14
```

冻结包 [check-manifest.json](../../tests/fixtures/p4_sphinx8721/check-manifest.json) 包含逐文件 SHA、原始公开 JSON SHA、宿主编排与 CLI SHA、镜像/revision、场景名、180 秒/12 秒/4 MiB 限额。以下为首次冻结身份，仅归属于下述 01 证据；配额修正后的当前身份与 02 重验见文末：

```text
public-target-v1:808126f84fcb32127d42b3af332a8ad4c21073c7bb880270e90d6b974157dd5e
```

此身份在基点与封存入口检查中相同。任一冻结材料或本模块/CLI 变动会使入口在 Docker 工作之前拒绝；若集成审查要求修改它们，需要显式重新冻结并重新核验受影响证据，不能悄悄沿用本次身份。

## 执行语义及终态来源

三类公开要求具体落实为四个独立 case：fresh_default、html_epub_default、html_epub_false、epub_true。各 case 有独立源项目/输出目录；HTML→EPUB 用两个新的解释器进程、相同 doctrees 和不同输出目录，没有 `-E` 重置来绕开问题。控制源材料位于容器 `/tmp/p4-public-target-*`，构建产物在各 case 下，候选只从 `/workspace` 导入。

每次构建核对真实退出码、实际 Sphinx 路径和 Python 版本；HTML 对照要求非空源码页；EPUB 要求有效 ZIP、正确 mimetype、container.xml、OPF、非空 index.xhtml，再检查磁盘与归档内的源码页。构建/导入失败、空产物、缺失证据、输出超限或清理未知只给 inconclusive。宿主从完整四 case 证据重新计算目标判据，不以 stdout 的 passed 字样判分。

副本运行使用同一绝对 180 秒期限，不按阶段续期；清理最多 12 秒。共享 coordinator 在失败操作中已经进行过清理时，不再给予第二份清理宽限。目标 stdout/stderr 加结构化报告的宿主收集限额为 4 MiB，原始报告超过限额不给通过。捕获正文使用共享捕获层自身的 blob/wire 限额，不混称成目标 stdout。

执行前后真实清单、candidate 身份、环境身份相同，控制文件 SHA 再次匹配；候选物化仍使用基础层的真实基树和政策检查。当前边界与 B 相同，面向正常开发错误，不声称可抵御同 uid 恶意候选主动伪造检查或操纵控制文件后恢复。

候选 CLI 要求 `--session` 的持久终态 journal：核验所有已提交嵌套产物、最终 completion.sealed capture、终态 delivery 身份、非未知最终候选、无 pending model/tool/validation，且运行清理成功。不提供任意脏目录/裸 capture 入口。可选 `--export` 必须同时提供源 session，并与其最终 capture、报告及所有所需 blob/hash 相符；单独 export manifest 不能证明 journal 终态，故拒绝仅传 export。

## 真实基点结果

原始目录：`D:/crq/artifacts/p4-sphinx8721-preflight-20261030-01/`。CLI exit 1 是已确认的公开行为失败，不是基础设施错误。工作及清理共 39.407 秒，原始 stdout/stderr、target-report.json、result.json、冻结 manifest、checked-capture.json、捕获 blobs 均保留，没有覆写。

| case | 构建 | 目标结果 | EPUB 目录 / 归档 |
| --- | --- | --- | --- |
| fresh_default | EPUB exit 0 | passed | 均无源码页；有效 EPUB 11,152 bytes |
| html_epub_default | HTML/EPUB 都 exit 0，HTML 源码页存在 | behavior_failed | 两处均有 `_modules/index.xhtml` 和 `_modules/p4_example.xhtml`；EPUB 12,426 bytes |
| html_epub_false | HTML/EPUB 都 exit 0，HTML 源码页存在 | behavior_failed | 同上；EPUB 12,425 bytes |
| epub_true | EPUB exit 0 | passed | 两处均有预期源码页；EPUB 12,426 bytes |

身份：

```text
candidate-v1:70e83bcd6a2e4a6a52d74bec174bb2c31348dcfb59213092573f5891dfbf9f3f
tree-v1:58a43a82c35f2f1ec34a83629d87a7562c282eb44ce7a671ab24beaff4b54928
environment-v1:495792f1db316b881b428f5cc6ab1ca1355d359df700ccdd307ce316b911ea8f
```

before/after manifest 相同；Python 3.9.25、pytest 8.4.2、Sphinx 来源 `/workspace/sphinx/__init__.py`，固定镜像 digest 和实际 image ID 相等。原始目标报告 SHA-256：`1a513b61fbc1d0cf32ae8ee186b1b14fc6f7b6f55837fb391e7ae95f0b31caf4`。各 builder 原始日志包含旧依赖警告但退出均为 0，没有用警告或导入错误替代行为失败。

## B 回归与封存入口实测

1. `D:/crq/artifacts/p4-sphinx8721-preflight-regression-20261030-01/`：复用现有 SphinxProfile/build_spec、ValidationService、DockerReplicaExecutor，在另一个新副本实际运行 tests/test_util.py。**8 collected / 8 passed / 0 skip / 0 error**，cleanup=cleaned，bind_baseline 成功；候选身份与上述原始基点相同。完整事件、环境、record、executor audit 均保留。记录 ID 为 `validation-record-v1:c05b7ad4164d135ef4c3d525931cac00040b9dce707caad36d4a47842f47a231`。没有改 B 生产代码或把目标失败加入该基线。
2. `D:/crq/artifacts/p4-sphinx8721-preflight-sealed-path-20261030-01/`：读取已有 P3-07 的终态 session 与 delivery，核验/物化其封存候选 `candidate-v1:8a58fbf0ef47f4b99fa69eb7af8d19b467c33439f55e1cda13dfac677b352a30`。检查完整跑通，33.834 秒，原始问题仍为同样的两个 behavior_failed、两个 passed，前后 manifest 相同、cleanup=true。**这是已有脚本演示候选，仅用于证明终态来源与物化 CLI；不是本轮自主修复、不是新的真实任务尝试。** 未读其历史求解补丁来选题或编写修复。

所有 raw 目录均为新建。本次三只容器的精确 ID 及 inspect “No such container” 结果见小证据；没有清理或扫杀其他任务容器，没有常驻容器。

## 命令与交接

下面的输出目录须换成新的唯一目录，CLI 不允许覆写。冻结文件已存在，不应重复 `--freeze`；审查无改动时主会话直接使用现有 manifest。

```powershell
Set-Location D:/crq/src/coding-harness
.venv/Scripts/python.exe -B scripts/check_public_target.py --print-task --task-output D:/crq/artifacts/p4-sphinx8721-public-input-<unique>/task.txt
.venv/Scripts/python.exe -B scripts/check_public_target.py --base --output-dir D:/crq/artifacts/p4-sphinx8721-preflight-<unique>
.venv/Scripts/python.exe -B scripts/check_public_target.py --session <finished-run>/session --export <finished-run>/delivery --output-dir D:/crq/artifacts/p4-sphinx8721-target-<unique>
```

主会话从 `task.txt` 读取完整字符串后传给正常 run_workspace 的 `--task`/run_entry；文件名本身不是任务正文。CLI exit 0=目标全部通过，1=完整公开行为失败，2=无法判定；不会自动把结果回灌求解模型或追加尝试。

首次交付的针对性测试命令：

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_public_target.py -q -p no:cacheprovider --basetemp=D:/crq/artifacts/p4-public-target-unit-02 --tb=short
```

**20 passed in 0.57s**；包括无效构建/来源/空档案/缺场景/无 HTML 对照/禁用 opt-in、磁盘与 ZIP 内容区分、无终态/无 seal/未知候选/pending/清理失败拒绝，以及组装输入完整性和冻结后材料变化拒绝。另有上述真实基点、B 回归和终态入口三条 Docker 检查；没有以人造 Sphinx 修复来制造目标通过。

剩余事项：主会话审查新增入口/来源边界与检查冻结，再与并行 P4-03 集成并冻结全局源码、配置和预算清单。最后实际自主候选的目标检查尚未执行；CPU/构建耗时只是本机单次观察，不构成性能保证。本次目标检查没有使用 B ValidationRecord 表达目标 verdict，不改 independent_evaluation.solved，也不宣称检索/修复率收益。

## 独立审查后的单件配额修正与当前冻结身份

Astra 找到一个 P2 边界问题：load_candidate 的首次 session 检查会恢复声明的配额，但随后新建的只读 ArtifactStore 又采用默认单件 2 MiB，导致允许且已封存的 2,097,156 字节源码在第二次读取时被拒绝。没有改变候选策略或扩大配额；修正为对该只读 store 复用正常 RunSession 回放初始化，从原 run.started 恢复其单件和总产物声明。所有 ArtifactStore 原始长度、SHA、所有权和路径校验仍执行。

新增 6 个实际文件/SQLite journal 回归：单件声明分别为 3 MiB 和 8 MiB，源码大小分别为 2,097,152 与 2,097,156 bytes；大源码保留在 changed 中，并按语料限额排除出 corpus_blobs。另检查 hash 正确但超过原声明上限的 orphan 仍被拒绝，以及原 blob 长度/哈希损坏仍被拒绝。未 mock session 检查或 blob reader。修前该组 **2 failed / 4 passed**，修后目标测试全组 **26 passed in 6.58s**；Astra 提供的原始复现 session 也已只读加载成功，源码精确为 2,097,156 bytes。

当前冻结 [check-manifest.json](../../tests/fixtures/p4_sphinx8721/check-manifest.json) 身份为：

```text
public-target-v1:586c07f4bb300d01eb18dbf75cdb34fba69b8589d45bf3b4f6efc00f43569e61
```

它取代当前入口使用的旧身份 `public-target-v1:808126f84fcb32127d42b3af332a8ad4c21073c7bb880270e90d6b974157dd5e`。变化来自宿主读取器修正，task.md、runner.py、conf.py、index.rst、p4_example.py 的 SHA 全部保持不变，目标判据没有改变。所有旧 `01` 原始目录、旧 task.txt 和其 check-manifest 保留；前述 01 运行仍归旧身份，未改写为新身份。

已在新身份下完成要求的两条重验，细节见 [配额修正小证据](evidence/p4-01-02/preflight-quota-fix-summary.json)：

| 新原始目录 | 真实结果 | 身份和清理 |
| --- | --- | --- |
| `D:/crq/artifacts/p4-sphinx8721-preflight-20261030-02/` | 40.764 秒；fresh_default/epub_true passed，两个 HTML→EPUB case behavior_failed；所有构建成功 | 基点 candidate 仍为 `70e83bcd…`，前后 manifest 一致，cleanup=true |
| `D:/crq/artifacts/p4-sphinx8721-preflight-sealed-path-20261030-02/` | 34.946 秒；同样 2 passed / 2 behavior_failed | P3 封存 candidate 仍为 `8a58fbf0…`，前后 manifest 一致，cleanup=true |

两条新 result/manifest 均记录当前 `586c07f4…` check_id。两只新 owned 容器已逐 ID inspect 确认不存在；未重跑 B 的 8 项回归，也没有改写其 01 记录。这里仍只验证旧 P3 脚本候选的来源与物化，不把它记成本轮自主修复。

主会话应改用新组装文本：`D:/crq/artifacts/p4-sphinx8721-public-input-20261030-02/task.txt`，11,103 bytes，SHA-256：

```text
2d1bbdd9404cb463b3f6ba407a3f3086c68167c5c02dd70f8d22b75f2ebe1196
```

原始 issue 和完整公开检查源码仍包含在该文本中；变化是其标注的当前 check_id。已对文本与 assemble 输出逐字节核对，并再次验证冻结材料。最终针对命令：

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_public_target.py -q -p no:cacheprovider --basetemp=D:/crq/artifacts/p4-quota-review-unit-final --tb=short
```

本次修改仅为读取器、对应边界回归、当前冻结 manifest、本报告和新增修正小证据。未调用求解 API、改候选、改其他生产模块或 commit；交付后停止写入，等待主会话安排原审查者定向复审。

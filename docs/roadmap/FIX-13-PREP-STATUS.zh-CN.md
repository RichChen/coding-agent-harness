# FIX-13 首五个新题位置接入状态

2026-11-10，首批完成。五个预定新题位置均有通过题，实际检查六个候选，发生一次规则排除、一次已修复的工程故障。接入于北京时间 16:04:25 完成，模型调用为 0。选题、镜像下载与接入原材料保留，FIX-12 记录保持原有版本。

## 固定计划和边界

- 30 题计划为 `scripts/rebench/out/fix13_tasks.json`，四组 240 个预定名额；这里只执行其随机题区组顺序的前五个位置。
- 暴露快照为 `scripts/rebench/out/fix13_exposure.json`，记录现有任务 manifest、FIX-12 接入尝试、rebench 公私任务/工作目录及运行任务目录来源。排除其中 54 个仓库标识，候选队列有 212 个仓库。这个范围不等于所有外部浏览历史，也不能证明模型训练未见。
- 选题和顺序种子沿用草案 20261113–20261116。两个文件已固定，恢复接入读取原快照和计划，不重新选题。首五位置自然为四道 easy、一道 other，没有依据接入难度改配比。
- 同层替补沿冻结队列取下一未尝试且仓库未占用的任务，其余 25 个正选仓库同样预留。镜像尚未预载、网络与 Docker 临时错误保持 pending，不因这些故障替补。
- 首批接入另设有界停止规则。同一位置累计三个候选被原规则拒绝时，保留已有结果并回到主会话讨论题池与排除率，停止下载第四候选。本批仅位置 5 的原题被拒绝一次，未触及停止边界。

## 当前进度

五个位置完整通过，pending 和 unfilled 均为 0。line 接口题按原规则排除，同层替补 venvstacks 经下述 P1 窄工程修复后，使用新镜像完整重做检查并通过。实际接入顺序为位置 2、3、4、5 原题，再恢复位置 1 和位置 5 替补；固定选样位置与未来运行顺序保留原计划。

| 随机位置 | 题目 | 层 | 源码原始大小 | 下载时间 | 接入状态 |
|---|---|---|---|---|---|
| 1 | `pasqal-io__Pulser-936` | easy | 9.06 MB | 2.70 秒 | 完整通过，15:42 完成 |
| 2 | `packit__packit-2684` | easy | 6.96 MB | 2.40 秒 | 完整通过，14:28 完成 |
| 3 | `vprusso__toqito-1298` | easy | 0.57 MB | 1.72 秒 | 完整通过，14:56 完成 |
| 4 | `spdx__tools-python-855` | easy | 0.69 MB | 1.70 秒 | 完整通过，15:03 完成 |
| 5 | `line__line-bot-sdk-python-981_interface` → `lmstudio-ai__venvstacks-197` | other | 原题 24.36 MB | 原题 4.71 秒 | 原题按条款 2 排除，替补完整通过，16:04 完成 |

本批有一次排除和一次替补。最终批次 JSON 的 accepted 为 5，pending、unfilled 均为空，逐题证据为 `state.json` 和 `runs/fix13-prep/stages.jsonl`，批次记录为 `scripts/rebench/out/fix13_ingest.json`。

| 最终题目 | 空补丁 F2P | gold F2P | P2P | 内存 / 工作区 |
|---|---|---|---|---|
| Pulser | 0/1 | 1/1 | 22/22 | 1 GiB / 256 MiB 通过 |
| packit | 0/3 | 3/3 | 51/51 | 1 GiB / 256 MiB 通过 |
| toqito | 0/1 | 1/1 | 26/26 | 1 GiB / 256 MiB 通过 |
| spdx | 0/1 | 1/1 | 0/0 | 1 GiB / 256 MiB 通过 |
| venvstacks（P1） | 0/5 | 5/5 | 48/48 | 1 GiB / 256 MiB 通过 |

空补丁与 gold 各重复三次，五个通过题均无 P2P 排除。每题的 `grader_spec`、`resolve_image`、`rebench_calibration` 入口绑定检查通过，记录位于 `runtime_bindings` 事件。

packit 隔离最后的 layout 容器退出 0 后曾等待 Docker 自动回收，最终在原截止前自然完成，未重做扫描或放宽验收。观测证据位于 `runs/fix13-prep/cleanup-observation.json`。

line 接口题源码一致，三次空补丁 0/4、gold 4/4，但扫描命中 `future/tests/base.py`，现有 `provenance-v2` 未能证明该文件可豁免，按既定条款 2 排除。真实泄露与否仍未确证，规则保持原样。

替补严格为冻结 other 队列的 rank 16，未根据接入容易程度挑选。官方 manifest 检查显示镜像压缩量 1.287 GB，其中约 0.846 GB 有共享缓存候选，新增约 0.440 GB；下载器会重新核验共享 blob 的哈希。容量和共享检查记录在 `runs/fix13-prep/venvstacks-download-plan.json`，下载/导入使用既有已验证 helper，日志为 `runs/fix13-prep/venvstacks-image-fetch.log`。

venvstacks 原镜像的隔离通过，校准在测试执行前因 `cp` 不能访问 `/opt/repo/.pdm-build` 失败。只读核验发现此目录不在基线源码归档中，在官方构建产物中为 root 所有的 0700 目录，仅含 `.gitignore` 和 `venvstacks.pth`；运行用户为 1000:1000。该失败未计作第二次规则拒绝，未自动下载后续候选。旧隔离结果、错误、构建输入/日志与权限观测保存在该题工作目录的 `before-pdm-r1/`，修复依据与文件哈希位于 `pdm-repair-r1.json`。

### 接入修订 P1，生成目录读取权限

主会话审阅上述证据后，在现有无模型接入工程授权内采纳窄修复，先记录规则再构建。

- 适用对象仅为 `lmstudio-ai__venvstacks-197`。必须再次确认基线归档的目录与文件条目均不包含 `.pdm-build` 或其子路径，官方镜像 digest 与原记录一致。
- 镜像内目标必须恰为真实目录 `/opt/repo/.pdm-build`，属主 0:0、原权限 0700，且仅有已观测的 `.gitignore`、`venvstacks.pth` 两个普通文件。两个文件的内容哈希、权限与属主在构建前冻结并在构建中复核，任一目标变化即停止。
- 唯一修改为该目录的权限 `原权限 | 0555`，本例从 0700 变为 0755。保留文件内容、文件权限、属主和其他路径，不递归操作，不新增写权限。源码、依赖、网络、uid、工作区和评分标准继续采用原设置。
- 原 state、构建输入/日志和旧镜像 digest 留存，旧镜像另加保留标签。新 Dockerfile 记录独立的 `build_input_sha256`。新 digest 重新执行完整隔离、三次空补丁/三次 gold、内存和实际工作区检查，再决定接受。
- 此修订针对镜像构建产物的可读性，只进入本轮接入适配，FIX-12 和通用 harness 权限规则保持原有实现。若需要扩大路径、权限或其他行为范围，回到主会话审阅。

P1 已执行并完成新镜像全流程校验。旧 clean digest 为 `0972caa8498fc4ad1446be349415b3198383cd23888b4eccba6e9ea79e67ee1b`，新 digest 为 `1ebf73c476f123292d90d2ae5fdc7cd2a0c849d066c4474984c4ed146d1f0794`。旧构建输入哈希为 `c07c6272d1ec3705919b65b31ff6910984818bb2ca2765b9521c27c8c83b8177`，新值为 `c8416dd8f38f4f048d9c19c1e9ec545ad84004a273a2ce04e722fbd81b39b0c5`。旧镜像保留标签为 `coding-harness-rebench-clean:lmstudio-ai-venvstacks-197-before-pdm-r1`，旧隔离结果仅作为历史证据留存。

## 复用入口和日志

`scripts/rebench/fix13_sample.py` 生成固定计划，已有文件时拒绝覆盖。`scripts/rebench/fix13_prepare.py` 复用原 `prepare` 与 `pilot_ingest` 规则，只处理这五个位置，强制 Docker context 为 `desktop-linux`。缺少本地官方镜像时保留 pending，禁用自动长时间 pull。

源码使用已实测的 codeload 直连，连接超时 15 秒、单次 120 秒，连续 30 秒低于 1 MiB/s 则中止，最多一次重试；原始下载保留。每个实际执行阶段用单调时钟计时并追加 JSON 日志，已有阶段可恢复。

- 独立 manifest 为 `configs/rebench/fix13-tasks.yaml`，现已写入五个通过题。
- 公私任务、源码、staging、receipt/state、clean record 均位于 `runs/fix13-prep/`，未复用 FIX-12 输出目录。
- 已核对未来运行入口按 manifest 的 `receipt`、`clean_record` 和 receipt 同目录的 state 读取。五个通过题均已调用 `grader_spec`、`resolve_image`、`rebench_calibration` 作无模型绑定校验并通过。
- 源码日志为 `runs/fix13-prep/source-preload.log`，源码校验摘要为 `runs/fix13-prep/source-validation.json`，首次本地镜像门控结果为 `runs/fix13-prep/intake-1.log`。

## 检查、耗时与后续边界

选题边界的三项本地测试通过，包括排除仓库、稳定顺序/仓库唯一，以及首五位置的四组两次名额结构。没有运行全仓库测试。

落地时修正过 Windows 换行造成的 exposure 文件哈希不一致，内容与选题顺序不变，原 CRLF 字节留存于 `runs/fix13-prep/exposure-original-crlf.json`，现文件与冻结计划记录的哈希一致。

### 实际耗时

从固定计划落地 13:51:51 至接入完成 16:04:25，共 **132.57 分钟**。首个实际接入阶段从 14:04:31 开始，到完成约 119.91 分钟。窗口包含下载/导入、串行接入、一次排除和 P1 诊断审阅。不同工作有重叠，下面的本地阶段总和与网络导入时间分别列出。

| 题目或范围 | clean 镜像准备 | 隔离 | 校准 | 内存检查 | 工作区 |
|---|---|---|---|---|---|
| Pulser | 12.29 分 | 15.19 分 | 2.63 分 | 0.50 分 | 0.05 分 |
| packit | 2.15 分 | 13.85 分 | 5.59 分 | 1.72 分 | 0.31 分 |
| toqito | 11.90 分 | 14.35 分 | 1.80 分 | 0.23 分 | 0.05 分 |
| spdx | 0.70 分 | 3.91 分 | 1.48 分 | 0.27 分 | 0.26 分 |
| venvstacks，含旧故障与 P1 | 2.46 分 | 7.84 分 | 1.28 分 | 0.20 分 | 0.04 分 |
| 全六候选与修复合计 | **31.04 分** | **57.36 分** | **14.36 分** | **2.92 分** | **0.72 分** |

本地已记录阶段合计 **106.48 分钟**，包含原 line 题的已执行工作和 venvstacks 的失败收集。venvstacks 两次构建、两次隔离均计入，旧校准故障发生在测试收集前。P1 后正常三次空补丁/三次 gold 校准耗时 51.75 秒。逐任务与完整尝试汇总为 `runs/fix13-prep/summary.json`，原始时间为单调时钟记录。

网络专项发现直连块测速较快，但两层实际传输遇 TLS reset，采用经验证代理续传并复用所有已校验缓存。原五图的共享压缩 blob 去重后约 9.13 GB，Pulser 单图压缩量约 7.29 GB、最大层 6.24 GB。原五图于 14:50:38 全部验证就位。已测 Docker 导入耗时约为 packit 205 秒、toqito 499 秒、spdx 471 秒、line 763 秒、Pulser **2176 秒**。替补新增传输约 440 MB，下载及缓存校验 160 秒、导入 670 秒。导入和接入中的本地等待属于实际时间，下载块速度不能直接换算完整准备时间。日志位于 `.tmp/fix13-network/` 与本轮替补下载日志。

### 剩余 25 题的排期与容量边界

按本批“106.48 分钟本地工作 / 5 个最终位置”线性外推，剩余 25 题的单路本地准备约 **8.9 小时**。若需提前留排期，建议按 **8–16 小时**粗略安排，并随下一块的实际体量与排除情况更新。该范围是工程排期假设，当前五位置以 easy 为主，且受到冷导入、共享缓存和一次修复影响。其余镜像体量、替补次数、额外环境问题和可重叠程度仍未知。

Pulser clean 镜像 Docker `Size` 为 **19.28 GB**，其他通过题约 4.52–7.13 GB。镜像 Size 包含共享层，实际新增占用还需结合缓存与 Docker 内部空间测量。收尾宿主盘观测约为 C 盘 34.9 GB、D 盘 291.6 GB 可用。其余 25 题的容量尚未验证，后续应按块核对宿主盘、Docker 内盘、OCI 缓存与工作临时空间；保留证据并维持不自动 prune，不提前预载全部候选。

### 换行与哈希收尾

主会话已为 `fix13_exposure.json`、`fix13_tasks.json` 增加精确的 `text eol=lf` 属性。接入进程全部退出后，仅将 plan 从 CRLF 转为 LF，原 plan 与原 ingest 字节备份于 `runs/fix13-prep/line-endings-before-lf/`。一次 JSON 对照确认 plan 语义/题序完全一致，ingest 仅更新 `plan_sha256`，所有接入结果保留。

- 原 plan SHA-256 为 `8a36d2a92e35dcdc85d8ee184e4f25bf716e700ad9ca88266af8fd81ae13884b`。
- 当前 plan SHA-256 为 `2ec5fb1d8fadd65c1c22e18dcaf7ee121657d828d3669318cbdc7d3f47945efe`。
- 新旧 ingest 的哈希、完成状态与比较结果记录于 `runs/fix13-prep/line-ending-migration.json`。

本轮止于五位置无模型接入，没有执行其余 25 题接入或付费四组实验，也没有 commit、push 或手动清理镜像。

## 后续 25 个位置接入，2026-11-10 新授权

用户已批准沿固定新 30 题计划完成其余 25 个位置，完成后与旧 30 题构成固定 60 题库。上文首批报告保留为当时结果。本次以五位置为一批自动推进，累计记录写回同一 ingest 与 manifest，计划及暴露快照字节保持固定。

批次/恢复入口新增五项无网络、无 Docker 的针对性检查，均已通过，覆盖保留既有通过题及替补、相同 pending 候选的有界恢复、三个规则拒绝后停止、容量故障不作资格失败，以及 D/E 缓冲检查。已接受的位置直接复用，逐候选先保存身份，再执行接入；临时错误最多两次流程尝试，耗尽后保留 pending 并诊断。

下载使用原已验证的代理续传与共享缓存，最多两个下载 workers，镜像导入、构建、扫描及校准串行执行。每批记录 D/E 空间；新增镜像另存官方 manifest 体量与共享缓存候选，以新增下载量估计 D 盘需求，以压缩体量六倍加 2 GB 作 E 盘保守规划量。保留 D 40 GB、E 80 GB 缓冲，预测越界则停下回报。该体量倍数用于排期/容量止损，实际扩展与共享占用仍须依据后续观测更新。所有缓存、partial、镜像和证据保留，不自动 prune。

P1 继续仅适用于原指定 venvstacks 任务。新环境/权限或验收变更回到主审，普通脚本故障在现有授权内修复并保留记录。本阶段仍为无模型接入。

### 续跑中已定位的来源格式分类修正

位置 16 的原 a2a 题按隔离条款 2 拒绝后，固定队列选到 Spack。Spack 的基线归档含两条 docs SVG 符号链接，其目标路径在归档中缺失，现有 `a5_image_tree.validate_entries` 因此拒绝派生镜像。最初批次入口将所有异常保守保留 pending，连续两次后于 20:30 有界暂停。只读诊断确认了原源码格式限制，按既有条款 1“镜像派生失败”纠正分类，未删除链接、修改源码或扩大可接受的符号链接类型。

证据为该题的 `symlink-diagnostic.json`、`source-rejection.json`，纠正前 ingest 原字节保存在 `runs/fix13-prep/ingest-before-spack-rule1.json`，两次 pending 历史仍留在该候选记录。仅增加该已识别来源格式异常到条款 1 的映射，六项针对性检查通过。同一位置累计已有两个候选被拒绝，继续取固定队列第三候选，若再拒则停止该位置。

### 累计记录写入的恢复

位置 16 的第三候选 Keras 已完整通过。21:35 保存下一位置的检查点时，Windows 对原子替换 `fix13_ingest.json` 返回 WinError 5，两次即时写入都失败，进程退出。已提交文件仍完整包含 16 个通过位置，下一题尚未开始。原 JSON 与临时文件备份于 `runs/fix13-prep/ingest-write-error-20261110/`。

仅在本轮累计记录写入增加最多六次、累计等待约五秒的 PermissionError 重试，持续失败仍停止。此修复不改 ACL、文件权限或接入验收，八项针对性检查通过。从已提交的 16 位置检查点恢复，已通过的隔离和校准继续复用。

### 同一 commit 的来源包根名称适配

累计 24 题通过后，Strands SDK 的来源包根名称为 `harness-sdk-029c77acae6f559212906a1ffe09368279ad976f`，固定清单仍使用仓库别名 `sdk-python`。原始下载包的 PAX comment 与预定完整 commit 完全一致，旧检查因外层文件夹名不同而停止。保留原始下载、旧规范包、旧 state/来源回执及 ingest 原字节后，仅将压缩包唯一顶层前缀规范为固定别名。

适配要求原始 commit 证明和根名称的完整 SHA 后缀同时吻合，并逐成员对照相对路径、类型、权限、uid/gid、链接目标和文件内容哈希。对照完全一致后才替换规范包，随后重新绑定来源回执；此前尚无已完成的镜像、隔离或校准。源码内容、依赖、权限及源树验收规则未改。十项针对性检查通过，证据为该题的 `source-root-alias.json`、`before-root-alias/` 与 `runs/fix13-prep/ingest-before-strands-root-alias.json`。仅为这个已验证修复恢复其重试额度，旧失败历史保留。

- 2026-11-10 18:17，授权至位置 10，累计通过 10/30。D/E 可用 287.4/371.9 GB。状态 batch_done。

- 2026-11-10 20:08，授权至位置 15，累计通过 15/30。D/E 可用 284.0/352.0 GB。状态 batch_done。

- 2026-11-10 20:30，授权至位置 20，累计通过 15/30。D/E 可用 283.2/347.9 GB。状态 blocked。IntakeBlocked: spack__spack-51723: infrastructure failure repeated twice: ValueError: symlink does not name an internal regular file or directory

- 2026-11-10 22:35，授权至位置 20，累计通过 20/30。D/E 可用 277.9/316.4 GB。状态 batch_done。

- 2026-11-10 23:39，授权至位置 25，累计通过 24/30。D/E 可用 275.5/301.9 GB。状态 blocked。IntakeBlocked: strands-agents__sdk-python-1734: infrastructure failure repeated twice: ValueError: archive root/commit mismatch

- 2026-11-10 23:56，授权至位置 25，累计通过 25/30。D/E 可用 275.5/301.2 GB。状态 batch_done。

- 2026-11-11 02:25，授权至位置 30，累计通过 30/30。D/E 可用 268.3/260.6 GB。状态 batch_done。

## 新 30 题接入完成，2026-11-11

新 30 个位置于北京时间 02:25:51 全部通过，pending/unfilled 均为空，累计 manifest 为 30 题。实际处理 36 个不同候选，原规则拒绝 6 个，发生 6 次替补事件，涉及 5 个原位置。各层最终均为 15 题。每题完成隔离、三次空补丁/三次 gold、内存、256 MiB 工作区及运行入口绑定。模型调用为 0。

### 最终题目

| 位置 | 通过题目 | 空补丁 / gold F2P | P2P |
|---|---|---|---|

| 1 | `pasqal-io__Pulser-936` | 0/1 → 1/1 | 22/22 |

| 2 | `packit__packit-2684` | 0/3 → 3/3 | 51/51 |

| 3 | `vprusso__toqito-1298` | 0/1 → 1/1 | 26/26 |

| 4 | `spdx__tools-python-855` | 0/1 → 1/1 | 0/0 |

| 5 | `lmstudio-ai__venvstacks-197` | 0/5 → 5/5 | 48/48 |

| 6 | `mmornati__home-assistant-csnet-home-132` | 0/5 → 5/5 | 72/72 |

| 7 | `vyperlang__vyper-4801` | 0/1 → 1/1 | 1/1 |

| 8 | `google__secops-wrapper-119` | 0/1 → 1/1 | 9/9 |

| 9 | `marimo-team__marimo-8387_interface` | 0/16 → 16/16 | 0/0 |

| 10 | `tox-dev__tox-3846` | 0/7 → 7/7 | 110/110 |

| 11 | `canopen-python__canopen-613` | 0/2 → 2/2 | 6/6 |

| 12 | `avaiga__taipy-2797` | 0/1 → 1/1 | 84/84 |

| 13 | `koxudaxi__datamodel-code-generator-2993` | 0/2 → 2/2 | 498/498 |

| 14 | `graphistry__pygraphistry-1107_interface` | 0/7 → 7/7 | 0/0 |

| 15 | `pab1it0__prometheus-mcp-server-76` | 0/1 → 1/1 | 25/25 |

| 16 | `keras-team__keras-22642` | 0/1 → 1/1 | 16/16 |

| 17 | `boxed__mutmut-403` | 0/2 → 2/2 | 163/163 |

| 18 | `gidgethub__gidgethub-225` | 0/1 → 1/1 | 56/56 |

| 19 | `mpmath__mpmath-992` | 0/1 → 1/1 | 59/59 |

| 20 | `researchobject__ro-crate-py-248` | 0/1 → 1/1 | 37/37 |

| 21 | `libp2p__py-libp2p-750` | 0/2 → 2/2 | 3/3 |

| 22 | `facelessuser__pymdown-extensions-2807` | 0/1 → 1/1 | 715/715 |

| 23 | `pgmpy__pgmpy-3137` | 0/1 → 1/1 | 60/66 |

| 24 | `pytest-dev__pyfakefs-1286` | 0/28 → 28/28 | 0/0 |

| 25 | `strands-agents__sdk-python-1734` | 0/4 → 4/4 | 109/109 |

| 26 | `skrub-data__skrub-1899` | 0/6 → 6/6 | 126/126 |

| 27 | `python-scim__scim2-models-120` | 0/3 → 3/3 | 300/300 |

| 28 | `ome-zarr-models__ome-zarr-models-py-206` | 0/1 → 1/1 | 5/5 |

| 29 | `Cambridge-ICCS__TCTrack-118` | 0/2 → 2/2 | 89/89 |

| 30 | `holoviz__holoviews-6783` | 0/1 → 1/1 | 96/96 |


### 替补与处理边界

| 原位置或候选 | 原规则条款 | 固定队列后继 |
|---|---|---|

| `line__line-bot-sdk-python-981_interface` | 2 | `lmstudio-ai__venvstacks-197` |

| `deepset-ai__haystack-10189` | 2 | `mmornati__home-assistant-csnet-home-132` |

| `pymc-devs__pytensor-1668` | 8 | `canopen-python__canopen-613` |

| `a2aproject__a2a-python-682_interface` | 2 | `spack__spack-51723` |

| `spack__spack-51723` | 1 | `keras-team__keras-22642` |

| `sktime__sktime-8723` | 3,8 | `skrub-data__skrub-1899` |


条款 1 为镜像派生/来源格式失败，条款 2 为隔离未通过，条款 3 为 gold F2P 未三次全过，条款 8 为过半 P2P 需要排除。以上表示当前接入条件下的资格判定，不能据此直接声称真实答案泄露或标准补丁本身错误。每位置最多拒绝三个候选的上限未触发。

最终三十题均通过 1 GiB 内存一致性检查。表中 P2P 显示校准原集合的通过数；pgmpy 为 60/66，其中六项按既有沙箱排除规则移出实际运行集合，明细保留在 manifest/state，其余通过题无 P2P 排除。


### 实际时间、空间和交接

剩余 25 个位置从 11-10 16:56:35 到 11-11 02:25:51，共 **9.49 小时**，包含传输、导入、校准、暂停诊断及恢复。自首份固定计划落地至新 30 全部完成共 **12.57 小时**，中间含首批审阅和后续授权等待。完成时 D/E 可用约 **268.3/260.6 GB**，仍保留全部镜像、缓存、partial、原始下载与证据，没有自动 prune。


续跑中的一般工程处理包括来源格式异常按旧条款 1 纠正分类、Windows 原子替换短时拒绝的有界重试，以及同 commit 来源包根名称的纯容器前缀适配。P1 仍仅用于原 venvstacks；其他权限、环境和验收边界保持原设置。sktime 的两大层低速通过原止损和缓存续传恢复，未重下完整镜像。


接入写入进程已退出、prepare.lock 已释放。最终 plan/exposure/manifest/ingest 的实际哈希与接受任务清单在 `runs/fix13-prep/handoff.json`，一次文件一致性核对通过。已明确交接给既有 `four_arm_implementation`，由其在独立目录接续全量无模型 Docker 预检及已授权正式四组运行。此后本接入执行者不再写累计 manifest/ingest 或运行 Docker。详细汇总为 `runs/fix13-prep/all-30-summary.json`，首批报告和全部历史日志继续保留。

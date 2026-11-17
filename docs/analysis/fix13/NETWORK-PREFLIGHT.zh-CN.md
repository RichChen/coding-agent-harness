# FIX13 网络预检与镜像下载

2026-11-10，本地网络预检与首 5 镜像准备。时间均为本机 UTC+8。下载缓存位于 D 盘，Docker 数据位于 E 盘。本工作未改全局代理或 Docker 配置。

## 历史核验

- 已只读核验 Cursor 会话 `78860345-e424-4309-a6fd-7411437c36da` 的本地 transcript。该文件共 675 行，前段为其他工作，661、664、670 行包含本项目镜像下载讨论。670 行记录代理约 0.6 MB/s、CDN 直连约 10–16 MB/s，且下载工具未入库。未向该会话发送消息。
- transcript 位于 `C:/Users/Administrator/.cursor/projects/d-crq-src-coding-harness/agent-transcripts/78860345-e424-4309-a6fd-7411437c36da/78860345-e424-4309-a6fd-7411437c36da.jsonl`。项目独立记录见 `docs/roadmap/FIX-12-RESULTS.zh-CN.md` 第 223 行。
- 已恢复旧工具 `.tmp/direct_fetch.py` 和 `.tmp/pull_speed_probe.py`。旧日志 `.tmp/direct_fetch_pdfly.log` 记录 14.1 MB/s，随后 docker load 成功、RepoDigest 匹配官方。`.tmp/direct_fetch_repl.log` 还有 9.5–15.7 MB/s 的结果。这些历史速度仅描述当时的下载。

## 本轮有界测速

所有请求保留 TLS 证书及主机校验。Docker token、manifest、blob redirect 经本机 `127.0.0.1:7890` 访问官方服务，签名 URL 只在进程内存及 curl 标准输入传递。探针未保存 token 或签名 URL。

首题 Pulser 官方 manifest 为 `sha256:0fc31855670d2ae02feacf95ed0cc8120aaf4e7ceb24584d87fc3053c5524c5e`。manifest 内容 hash 已核对响应 digest。同一大层的顺序对照均限制 32 MiB、连接 10 秒、单路 30 秒。

| 对象与路径 | 下载量 | 总耗时 | 观测速度 | HTTP |
| --- | --- | --- | --- | --- |
| Docker 官方 CDN 直连 | 32 MiB | 7.508 秒 | 4.469 MB/s | 206 |
| 同一 CDN 经代理 | 32 MiB | 10.272 秒 | 3.267 MB/s | 206 |
| 首题 codeload 源码直连 | 完整 9,064,875 字节 | 1.522 秒 | 5.957 MB/s | 200 |
| 同一源码经代理 | 完整 9,064,875 字节 | 2.900 秒 | 3.125 MB/s | 200 |

测速原始摘要见 `.tmp/fix13-network/probe-results.json`，探针见同目录 `probe.py`。小流量顺序测试只支持本次直连优先，无法承诺整镜像完成时间或其他时段速度。

Pulser 的 `4a874aed58ec…` 层出现默认直连 TLS 连接 reset，连续三次 curl 返回 35。定点 16 MiB 对照中，TLS 1.2 直连 8.272 秒、代理 9.655 秒，均为 206。随后 TLS 1.2 直连完整恢复该 203,182,154 字节层，用时 19.0 秒并通过 sha256。未放松证书校验。正式 helper 在错误 28 或 35 后改用 TLS 1.2 重试。

随后大层在 5.138 GB 处因持续低速停止，续传增至 5.159 GB 后再次触发低速规则，保留 partial。该时段与 packit docker load 重叠，无法分离网络与磁盘竞争因素。最后一次有界对照读取同一大层 5.159 GB 偏移后的 16 MiB，TLS 1.2 直连立即 reset，代理 5.236 秒完成。此时 packit 官方导入已结束，另有 toqito 下载和 packit clean build。随即结束测速，选当前可用的代理续传，不再反复比较路径。Toqito 的另一 303 MB 层也在直连有界失败后改走代理，194.3 秒完成并通过 hash。短块速度未用作总时长承诺。

## 可复用入口

正式入口为 `scripts/rebench/fetch_image_verified.py`，依赖 Python 标准库、Windows curl 与可用的 Docker `desktop-linux` context。默认本机代理用于官方 metadata 和不超过 32 MiB 的 blob，大层按官方 redirect 直连。

```powershell
py scripts/rebench/fetch_image_verified.py `
  --cache D:/crq/src/coding-harness/.tmp/fix13-network/oci `
  --workers 2 `
  swerebench/sweb.eval.x86_64.pasqal-io_1776_pulser-936:latest
```

- 连接最长 10 秒，小 blob 单次最长 90 秒，大层单次最长 1800 秒，大层低于 1 MiB/s 持续 30 秒终止该次，最多 3 次尝试。partial 保留以续传，失败时停止本次镜像处理。
- 当前直连不可用时，可在有界验证后显式添加 `--proxy-blobs`，让大层也使用配置的本机代理。本轮 Pulser 后段和剩余三个镜像采用该开关，恢复日志为 `pulser-proxy-resume.log` 与 `remaining-three-proxy.log`。代理速度同样随负载与时段变化。
- 已完整校验的相同 digest blob 可在同卷缓存硬链接复用。完整 blob 和 partial 均保留。
- top manifest 与子 manifest 核对 sha256，每个 config/layer 核对 sha256，随后按 OCI layout 流式 docker load，检查退出码及 RepoDigest 与官方 top digest 一致。
- 最终 `docker image inspect` 设 30 秒超时。`docker load` 本轮由会话持续观察，无新增硬时限，最长实际导入为 2176 秒。网络超时规则不能作为整个导入操作的总时长保证。
- 本轮初始恢复副本为 `.tmp/fix13-network/direct_fetch_bounded.py`，仍依赖旧 `.tmp/pull_speed_probe.py`。正式入口已去除此临时依赖，并通过编译、CLI help、官方 manifest 请求和缓存 blob hash 分支验证，随后已用于实际代理续传与导入流程。
- 源码建议从 `https://codeload.github.com/<repo>/tar.gz/<base_commit>` 直连有界下载，最终继续沿用原 `download_archive` 的 PAX `comment=<base_commit>` 重打包。源码接入由准备入口负责。

## 首 5 镜像体量与执行记录

首 5 固定于 `scripts/rebench/out/fix13_tasks.json` 的 `first_five`，未因体量或网络结果改选。

| 镜像任务 | 压缩 GB | 官方 top digest |
| --- | --- | --- |
| Pulser-936 | 7.288 | `0fc31855670d2ae02feacf95ed0cc8120aaf4e7ceb24584d87fc3053c5524c5e` |
| packit-2684 | 1.358 | `8ffc1494367f852ef1529dcdd9ee06b3bc8b272a3d3afbbb70f00df8396272db` |
| toqito-1298 | 1.905 | `f3fd8b7b18b86df48d5bd70510bd44a49fe8551ed00523a51ab20fbaa674aa82` |
| tools-python-855 | 1.252 | `2048b13c2d5672f6947d1111fc5d6edf476168384f08e6004724f30350b5987c` |
| line-bot-sdk-python-981_interface | 1.320 | `11b622466b7f8734ef20f3e5d595b533a8eb11d01f033a7e929c4e1269b4b1d4` |

总 blob 压缩量 13.123 GB，去重后 9.128 GB，共享重复 3.995 GB，共 19 个唯一 blob。最大单层为 Pulser 的 6.238 GB。摘要见 `.tmp/fix13-network/manifests.json` 与 `shared-layer-summary.json`。

当前执行日志为 `.tmp/fix13-network/pulser.log`、`pulser-import.log`、`recover-layer.log`、`remaining-four.log`。临时副本的 image 总字节除以耗时指标包含缓存命中，不可视为网络吞吐量。正式入口已去掉该指标。

Docker 只读配置确认 `CustomWslDistroDir` 为 `E:/Docker/wsl/DockerDesktopWSL`，数据文件 `disk/docker_data.vhdx` 当时约 379.34 GB，E 盘空闲约 427.86 GB。D 盘初始空闲约 280.8 GiB。上述为下载开始前快照。

## 最终就绪结果

首 5 镜像全部完成逐 blob hash 核验、docker load 返回 0、RepoDigest 匹配上表官方 top digest。

| 固定位置与镜像 | helper 身份核验完成 | docker load 实际耗时 |
| --- | --- | --- |
| 1 Pulser | 14:50:34 | 2176 秒 |
| 2 packit | 14:03:01 | 205 秒 |
| 3 toqito | 14:17:30 | 499 秒 |
| 4 tools-python | 14:26:42 | 471 秒 |
| 5 line SDK | 14:40:59 | 763 秒 |

Pulser 14:47 已可只读 inspect 且 digest 正确，客户端仍等待既有 `docker load` 结束，14:50:33 正常返回 0。等待阶段没有重导入。14:50:38 单次批量只读复核 5 个镜像 RepoDigests，全与预先获取的官方 manifest 一致，证据为 `.tmp/fix13-network/final-image-identities.json`。所有原始缓存、partial 历史日志及镜像均保留。

正式入口的网络及导入主流程已经实跑，首个 packit 使用恢复副本，后续代理恢复及导入使用正式入口。收尾增加的小 blob 90 秒时限、HTTPS 协议约束、HTTP 错误拒收、限定 manifest 闭包打包与最终 inspect 超时通过编译检查，未为这些收尾修改再次下载或导入整镜像。

14:51 附近空闲容量为 C 盘 34.94 GB、D 盘 292.17 GB、E 盘 402.86 GB，均为十进制 GB。缓存留在 D 盘，Docker 数据路径保持 E 盘。

本轮结果支持恢复官方 CDN 分层下载、完整 hash 校验与本地 OCI 导入的流程。当前可用路线随时段变化，初测直连较快，后续遇到 TLS reset 或持续低速时，经有界验证后代理续传成功。导入本身的 205–2176 秒实测耗时也需计入接入计划，短块下载速度无法估算完整接入时间。

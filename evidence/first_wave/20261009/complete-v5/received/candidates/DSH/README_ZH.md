# RPNH DSH codec 候选离线认证包

2026-10-08 UTC。基底：Deng-0119/RPNH `8dd360e4848912a998dbd83220c3f0ce0a1caa86`。

## 交付结论

这是可复核、可移植的 codec / provider DTO / detached history 候选，不是 DSH 新版适配完成或支持启用。生产继续固定旧版 `ddefc45fbc7f8e46dd73185e68295696d1297887`（0.1.6-alpha.2）。新版 `5badb15009ae1756c3afe0ae0cef1faafc290ccc`（0.2.1-alpha.1）仅为已审源候选目标。

- 单消息布局与完整历史视图保留合法 reasoning、工具调用/结果、消息及调用 ID、原始参数、内容、错误语义；V3 plugin source 按官方规则映射。
- 完整 detached history 支持 V3→V3、V3→V4、V4→V4；V4→V3 拒绝。读视图不授予执行或恢复权限。
- 生产 open/stat/read 仍走旧 pin。新版官方 Session cold reopen、typecheck、原生 lifecycle 和执行接线均未完成。
- 本地显式 owner `--history` 导出契约未变。没有证明它是远程泄漏，不做通用脱敏或删除合法正式消息。
- Registry authority、票据身份、PN、nativeRegistry、launcher/server、prepare/verify、factory patch、bridge REVISION 均保持既有边界。

## 最终证据

- Node：55 case 通过；Python codec/parity：39 case 通过；launcher/source：9 case 通过、2 个安装/打包 case 未运行。
- 新独审复跑的是相同 case 集合，不重复累计。旧独审窗口为54 Node；最终第55例覆盖 header 字段校验。两份独立脚本为1,133与36次 assertion，不能当作case相加。
- 原 owner 回归34 case：12通过、22在AF_UNIX EPERM处被环境阻塞；后一次经审批启动后中断 exit130，不算通过。原日志全部保留，本轮没有重跑或提权。
- 本轮无安装、模型/真实provider、登录、Actions、push；没有运行 native 或 TypeScript typecheck。

## 文件导航

- `candidate.patch`：13路径最小产品改动；`changed-files.txt`、`file-manifest.json`：精确基底/候选hash；`patch-apply.json`：新临时副本应用与逐字节核验。
- `source/`：1,346个已核源的验证快照文件；不是完整checkout，也不带依赖环境或缓存。
- `baseline/`：补丁涉及的已有文件及受保护文件的精确旧副本。完整1,339文件旧核源清单留在 evidence，不表示本包包含完整baseline。
- `upstream/{pin,latest}/`：14份官方关键源码及两版官方LICENSE。原始Git blob清单与新license读回均在 evidence。
- `evidence/`：原验证窗口；`evidence/freeze/`：最终冻结、case ID、源前后hash及只读来源核验。
- `DESIGN_REVIEW_ZH.md`、`IMPLEMENTATION_STATUS.md`、`LOCAL_VALIDATION.md`：设计、有限实施范围与剩余本地门槛。
- 同级 `rpnh-dsh-codec-independent-review/`：原始审阅脚本/报告、最终独审报告与证据。

## 先验证，再在正式工作树应用

在本目录执行 `python scripts/verify_bundle.py`，校验本包两目录的SHA256清单。不会写文件或运行产品。

在已准备的正式RPNH工作树，先运行 `python scripts/verify_bundle.py --checkout /path/to/RPNH` 验每个受影响旧文件和受保护文件；新文件须尚不存在。再在该工作树执行 `git apply --check /path/to/candidate.patch`，审阅后按既有授权应用。若main已变化，不强覆盖，先复核差异。

只重跑纯测试：用既有准备好的Python解释器执行 `python scripts/run_pure_gates.py --output /path/outside/bundle/results`；PATH中须已有Node24、Python环境须已有pytest及项目依赖。脚本不安装或补齐环境。它不代表owner/socket或native通过。

## 剩余门槛

见 LOCAL_VALIDATION.md：原 owner/socket 回归、完整包构建/安装、旧 exact runtime 无退化、两版 exact TypeScript 类型检查、新版真实Session恢复/open/stat/read/flush一致性、零输入生命周期与fake-model执行分别验证。之后才可单独评审新版production接线与支持manifest；旧active checkpoint不得换revision继续跑。

## 许可证与来源

RPNH MIT见 source/LICENSE；原项目第三方声明见 source/THIRD_PARTY_NOTICES.md。DeepSeek官方MIT分别见 upstream/pin/LICENSE 与 upstream/latest/LICENSE，来源与Git blob已核对；两版全文相同。补丁中有限移植的source映射沿用该许可。本包不附第三方安装依赖或其构建产物。

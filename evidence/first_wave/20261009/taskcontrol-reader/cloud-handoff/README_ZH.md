# H2a TaskControl reader 收敛：本地补验包

状态：**READY_FOR_LOCAL_NATIVE_VALIDATION**。产品范围独立审阅 PASS；原生 AF_UNIX 门槛仍待本地闭合，未推送或合并。

五文件候选只将既有 TaskControl result/status 消费者收敛到 Registry 原生 reader，并以 registered size 限制底层物理读取。合法超过 4 MiB 的 body/descriptor 仍可读，没有任意固定上限。legacy JSON 形状、累计 counts、当前 generation、不得回退旧 terminal，以及输出完成后的同一 read cut 重查保持不变。

从 `LOCAL_VALIDATION_ZH.md` 开始。先核包、checkout 与补丁，再在既有 Linux/WSL2 环境补验；`verify_package.py` 只检查完整性并在临时目录应用补丁，不运行产品测试、不修改目标 checkout。

- 作者最终 focused 47、兼容 44、shared-reader 15；独审 11、large descriptor 1、shared-core 24。批次有重叠，不能相加作独立 testcase 总数。
- 原生 status 批次为 6 pass / 1 socket EPERM；resume 在 provider dispatch 前遇到同类 EPERM，恢复断言未到达。原日志与失败 JUnit 保留。
- H1 七条变更路径与 H2a 五条无交集。H1 基线 674252f 至 H2a 基线 ec9077 仅 evidence 变化。详见 `packaging/H1_COMPATIBILITY.json`；路径无交集不能代替组合源上的原生测试。
- 本包不包含 H1 未合入实现。可独立准备本地补验；若最终组合 H1+H2a，需核十二文件身份，并在同一组合源码上重跑相关 native gate。
- A1 host config/environment 的单独阻断不阻止本包交接，也不能通过放宽安全条件绕过。

补丁 SHA256：`62f914d41d953e9badd4eb06eec43273f9d52bd5bd9b948ad6e10137d3ed2b24`。
源码基线：`ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`。

`source/` 与 `baseline/` 只带五条改动涉及的最终/原始文件，不是完整仓库。`tested-source.sha256` 是作者当时 1,335 条 materialized source 的历史身份记录，不是解压后所有条目都应存在的清单。完整仓库来自本地已有 checkout。

分发仅对历史证据中的云工作区、pytest 临时根、云主机标识作一致替换；XML 中占位符已转义且全部重新解析。源码、补丁、testcase、失败类别与计数不变。`packaging/DISTRIBUTION_PROVENANCE.json` 记录原始到分发文件哈希与每处修改的字节/行区间；`SHA256SUMS` 校验当前包。不要将脱敏分发副本称为原始字节相同的日志。

`materialize.py`、`freeze.py`、`package.py` 是作者历史生成脚本，保留用于追溯，不是本地补验入口，不应在本包内重跑覆盖冻结证据。无需模型、外部 provider、Actions、业务 benchmark、安装或远端写入。

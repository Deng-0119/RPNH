# 实施与验证结果

## 结论

显式候选入口已实现，默认仍为 1.18.32。候选 1.18.35 未进行 native 认证。
本包只交付10文件最小 patch、经 blob 核验的局部源码基线/实现、纯 G1 证据及有限
G2/G3 本地 smoke 准备材料；没有升级生产 pin，也没有 DTO/codec/backend 迁移。

## 最终 G1

作者最终 `run_g1.py`：**274 passed**。

- 169 个候选/profile/projection case
- 41 个专用测试入口、binary 文件检查、失败证据和防 skip 全绿 case
- 64 个既有纯 frontend/CLI 回归 case

禁用第三方 pytest 自动加载，使用预置依赖；guard 拒绝真实 socket、Popen、thread.start、
fork、PTY。version probe 仅注入固定返回或 timeout；真正的 probe 校验函数不替换。
G1 不执行 Registry fixture preparation、不启动 HTTPServer/stock client、不调用模型。
日志：`evidence/g1-final.log`；JUnit 与节点清单：`evidence/g1-final/`。

独立审查结果单独见 `independent-review/REVIEW_ZH.md`。复跑与重叠用例不相加为更大的
“认证测试总数”。源码审计的上游 blob equality 也不计作 native 测试通过。

## 状态分列

| 项目 | 状态 | 能证明的范围 |
|---|---|---|
| G1 纯离线 | PASS，274 cases | 有限 profile、projection/schema、严格probe与入口 fail-closed |
| 官方 SDK 编译验证 | NOT_RUN | 包内只使用 source-extracted subset |
| G2 stock + ApplicationDouble | NOT_RUN | 脚本仅准备，不能推断真实TUI通过 |
| G3 stock + 原Application/合成Registry | NOT_RUN | 脚本仅准备，prepare/measurement尚未执行 |
| 原生 Windows / macOS | 未支持 | 平台门未放宽 |
| 安装、登录、真实provider/API、Actions、push | 未执行 | 不属于本轮授权范围 |

## 范围与证据边界

- 完整保留原 manifest 的 `upstream`、`$defs`、responses/events 与全部其它字段。
  只增加 `certification_candidates`，默认常量变为从 default profile 派生的只读别名。
- `/global/health` 的旧版本 const 明确保留；candidate 对原const的拒绝被测试证明。
  候选仅对版本 metadata 做显式绑定，再用同一结构校验；不是悄悄放宽默认schema。
- profile 精确比对 version/commit/certification_only；未知、混配、suffix、范围或路径均拒绝。
  两个 protocol 实例版本不串扰；生产 launcher 无 candidate 参数或环境开关。
- 原 `test_opencode_pty.py` 与其它既有 OpenCode 测试未改；非本次产品文件逐字保持基线。
- native 入口对显式请求缺参数/依赖/binary、不符版本、错误lane或未执行做失败处理。
  call、skip、teardown 失败都写终态失败证据，避免内存已失败但磁盘仍 PASS。
- G3 将 fake-port 的两次准备调用与 measured零新增分开；原 gateway tick单列，
  原owner lease排它/释放保留；PN走 `/rpnh-net` 原投影与过滤，不用 `/rpnh-tasks`。
- G2/G3 只准备有限 smoke；picker、forced reconnect、long pagination 等未覆盖。
  binary hash和version不证明官方发行来源，环境隔离不等于OS网络隔离。

## 基线/补丁

实时 main `8dd360e4848912a998dbd83220c3f0ce0a1caa86`，产品父提交
`d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。972份基线文件逐 Git blob 验证。
10文件变化见 `changed-files.txt`，可应用补丁见 `opencode-candidate.patch`。
补丁 SHA256：`ee33ed95126989319e981683a308e3182d9b5b4e4c0f0a900e5bb8f111716bba`。
净应用与交付字节一致性结果见独立审查和 `evidence/package-application.json`。

本次没有以忽略权限、提升权限或换路径的方式运行受限 native/socket/PTY 动作。

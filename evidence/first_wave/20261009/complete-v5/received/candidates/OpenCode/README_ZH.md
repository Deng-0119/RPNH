# OpenCode 1.18.35 显式候选认证入口

本包实现获审的有限候选入口。生产 `rpnh --frontend opencode` 仍固定 **1.18.32**；
**1.18.35** 仅供专用测试显式选择，尚未完成 native 认证。没有安装、登录、模型/API、
socket/PTY native 测量、GitHub Actions、push 或默认 pin 升级。

## 基线与最小变更

- 实时 main：`8dd360e4848912a998dbd83220c3f0ce0a1caa86`；产品父提交：
  `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。
- `baseline/` 的 972 份 runtime/test/script 及所需文档逐 Git blob 核对该 main。
  `source/` 是此作用域的实现快照，不是完整 release、wheel 构建包或已注册 Git checkout。
- 产品改动只有 `opencode_protocol.py`、`opencode_launcher.py` 和 compatibility manifest。
  DTO/schema、Registry、PetriNet、provider、TaskControl、产品 CLI 均保持原实现。
- manifest 是版本与 commit 的单一来源。frozen profile 同时进入严格 version probe 与
  protocol；生产入口显式使用 default，且探测在创建 owner 前完成。
- 候选官方 tag commit：`53d1eabb61e21162157817bf677da0a4ad3332e3`；默认基线：
  `545f51d26cc39a907d2867492d498d9607ea5fa4`。
- 上游完整 SDK source 39/39 blob 相同支持复用已核对 DTO；不是编译 SDK 或 native 成功证据。
  来源见 `design/SOURCE_MATRIX.md` 与 `evidence/upstream-source-audit.json`。

## 离线复验

使用已有 Python 3.11+ 环境和已安装的项目测试依赖。不安装依赖，不启动 native 客户端。

```bash
python verify_package.py
python run_g1.py --output /absolute/path/outside-package/g1-evidence
```

`run_g1.py` 列举精确纯测试节点，并以 fail-on-call guards 拒绝 socket、native process、
PTY、fork 和工作线程。不能用 `tests/test_opencode*.py` 通配命令替代它：旧测试集包含
实际 loopback、Registry fake-port 执行与 native PTY 测试，边界不同。

G1 覆盖 exact profile/commit、拒绝未知与混配 target、双实例隔离、严格假进程 probe、
生产默认与前置平台检查、共享 DTO subset、三种 model shape、variant、fail-closed route、
稳定 ID、SSE framing 和请求去重。实际数量与结果见 `RESULTS_ZH.md` 和 JUnit。

已知明确特例：原 `/global/health` schema 含 `const: 1.18.32`，完整 schema 未改。
候选测试先证明旧 const 拒绝 1.18.35，再校验完整 health 对象和绑定候选版本的结构。
其它 DTO/event 使用同一原 schema；不声称候选通过未变的旧 health const。

## Native 本地交接

G2/G3 脚本已准备，本包没有运行。只有另行授权真实客户端执行、已有可信 stock Linux
binary 和预置依赖后，才在 **main 的真实 Git checkout** 应用候选 patch 并运行：

```bash
git rev-parse HEAD
# 必须核对上面的 exact main 和本地修改；不自动叠加其它未合并候选包。
git apply --check /absolute/path/to/opencode-candidate.patch
git apply /absolute/path/to/opencode-candidate.patch
python -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 \
  --opencode-certify-binary=/absolute/path/to/stock/opencode \
  --opencode-certify-lane=contract
python -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 \
  --opencode-certify-binary=/absolute/path/to/stock/opencode \
  --opencode-certify-lane=registry-read
```

1.18.32 对照可对同一命令显式选择 `--opencode-certify-version=1.18.32` 与对应 binary。
不得伪造版本 stdout、包装旧 binary、monkeypatch 旧 pin、改 PATH 或自动安装。
未请求候选 lane 时不执行。显式请求缺参数、缺依赖、缺 binary、版本不符或 lane 未执行，
必须 fail/blocked，不能 skip 全绿。收集测试也不是 native PASS。

G2 是 stock + 原 HTTP/SSE/Protocol + ApplicationDouble 的有限 bootstrap/help/prompt/
fixture-committed answer smoke。该 fixture 的 committed 不等于 Registry terminal proof。
G3 是 stock + 原 Gateway/Application + 原 API 准备的两 committed-turn 合成 Registry；
prepare fake-port 调用和 measured zero-effects 分列，使用原 PN projection/filter，保留实际
gateway tick 和独占 owner lease。`/rpnh-tasks` 会 reconcile，不能充当严格只读测点。

这两个 smoke 不覆盖完整 picker/effort 矩阵、强制 SSE 重连、失败/abort 展示、双 session
并发隔离、长历史分页、active-worker recovery、真实模型或插件工具。不得把有限 PASS
称为完整 G2/G3 或“新版全部兼容”。仅测具体 Linux/WSL2 组合，不启用原生 Windows/macOS。
环境隔离不是 OS 网络沙箱；不擅自修改网络安全设置。

证据记录 actual binary hash/实际 probe、OS/arch/WSL/PTY、RPNH revision 与源文件 hash、
backend、实际场景、首个失败、计数及去敏有界输出。binary hash + version 不独自证明官方
发行物，官方 release asset 来源与 digest 必须另外核对。运行证据不回写 manifest，也不
提升生产默认。独立审查与完整文件 hash 见包内报告和 `FILE_MANIFEST.json`。

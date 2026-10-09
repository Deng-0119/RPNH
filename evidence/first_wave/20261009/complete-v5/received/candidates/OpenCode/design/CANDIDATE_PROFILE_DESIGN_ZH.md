# OpenCode 显式候选认证入口设计

状态：仅设计，等待审核。没有实施、安装、native 运行或默认 pin 改动。

## 最小实现目标

让真实 1.18.35 stock TUI 可以在独立认证测试里连接原 RPNH HTTP/SSE 边界，且 binary probe、protocol version、commit provenance 使用同一个明确 profile。生产 `rpnh --frontend opencode` 仍固定 1.18.32。

源码 delta 没有 codec 迁移要求。新增工作只属于认证基础设施，不建立第二种 provider/runtime 或“新版执行后端”。

## 建议的显式参数边界

在现有 `opencode_protocol.py` 增加一个小型 frozen `OpenCodeCompatibilityProfile` 值对象（version、commit、certification_only），以及只能从已登记精确版本解析的 profile factory。沿用该文件的现有 dataclass 依赖；无需新 plugin 系统。

建议接口：

- `get_opencode_profile(*, certification_version: str | None = None)`：省略即 default；只允许明确登记的候选；拒绝 arbitrary semver、latest、>=、未知 commit 和用户提供路径。
- `check_version(binary, env, cwd, *, profile=DEFAULT_PROFILE)`：严格核对该 profile 的 version，保留 timeout/nonzero/suffix 拒绝行为，不关闭探测。
- `OpenCodeProtocol(gateway, directory, *, profile=DEFAULT_PROFILE)`：保存 immutable profile；只让 `session_document.version` 与 `/health`、`/global/health` 读取它。共享原 DTO 投影、route allowlist、model selection、event 和 Registry authority。
- 生产 `run_opencode_frontend` 显式选择 DEFAULT_PROFILE，把同一个 profile 传给 probe 和 protocol。该函数、`rpnh_cli`、环境变量均不新增用户可选 candidate 开关。本轮不让候选进入真实生产启动路径。

允许 profile 参数不等于验证通过。协议输出中的 version 是本次所声明的 compatibility target；manifest 和测试报告另外保留 `certification_only`、native status，不把 health healthy=true 解释为已认证或整个 RPNH 健康。

## Manifest 单一来源

继续保留 `opencode_compatibility.v1.json` 的 `upstream` 为 1.18.32 和已有 source-extracted subset 标记；不要把它改成 upstream generator output。

在同文件增加 `certification_candidates`，候选记录最少包括：

- `package_version: 1.18.35`
- `commit: 53d1eabb61e21162157817bf677da0a4ad3332e3`
- `status: certification-only`
- `production_enabled: false`
- `contract_source_commit: 545f51d26cc39a907d2867492d498d9607ea5fa4`
- `shared_contract_evidence`：双方完整 SDK source blob equality 的 immutable 引用/摘要
- `native_g2: not-run`、`native_g3: not-run`；运行结果应输出到独立 evidence artifact，不让一次运行自动改 manifest

profile factory 从 manifest 的 default/candidates 解析并验证，避免 hardcode 两份 candidate 集合。`OPENCODE_VERSION` / `OPENCODE_COMMIT` 如需兼容现有 import，仅作为 DEFAULT_PROFILE 的只读别名，值保持旧 pin。

既有 manifest $defs、responses、events 复用一套。无需复制两份相同 schema，也不能把 metadata equality 当编译 SDK 或 native 成功。

## 专用 native 认证测试入口

在 `tests/test_opencode_pty.py` 的既有 PTY flow 上提取共享 fixture；新增测试专用显式选项，建议通过集中 pytest option 注册实现：

- `--opencode-certify-version=1.18.35`
- `--opencode-certify-binary=/absolute/path/to/opencode`
- `--opencode-certify-lane=contract` 或 `registry-read`

这些是拟新增参数，当前不能直接使用。没有显式参数时继续运行现有 pinned 测试；如果仅准备候选认证用例，则用 marker 明确 skip。显式请求候选但 binary 缺失或版本不符时必须 blocked/fail，不能静默 skip 后以退出码零当 PASS。

测试 fixture 选择有限 profile，然后把该同一个对象传给 check_version 与 OpenCodeProtocol。原 binary 不改、不注入 wrapper、不伪造 stdout；不 monkeypatch OPENCODE_VERSION、OPENCODE_COMMIT、check_version 或 protocol version。G2 固定 ApplicationDouble；G3 固定原 RegistryFrontendApplication + 已提交合成 Registry，不支持传入用户 Registry 路径。

`run_opencode_frontend` 不用于 G2，因为它会构造生产 Application。G2 重用现有 PTY test 已有的 explicit HTTPServer 启动方式。G3 的 gateway/application 建立在测试 fixture 中，用原实现运行，无另造 backend。

## 最小文件范围

| 文件 | 拟改动 |
|---|---|
| `cpn/frontend/opencode_protocol.py` | frozen profile、manifest resolver；实例绑定 profile；仅版本元数据参数化 |
| `cpn/frontend/opencode_launcher.py` | check_version 显式 profile；生产调用固定 DEFAULT_PROFILE |
| `cpn/frontend/opencode_compatibility.v1.json` | 单独 candidate metadata；default upstream 和 schema 不变 |
| `tests/test_opencode_frontend.py` | exact profile 负向用例、默认拒绝新版、两 profile 元数据与同一 contract |
| `tests/test_opencode_pty.py` | 既有 native flow 分出显式 G2/G3 lane；受限 binary 参数与证据 |
| `tests/conftest.py` 或一个受控 pytest option 模块 | 仅测试命令参数注册，不加产品 CLI 开关 |
| `tests/test_opencode_registry_integration.py` | 抽取已提交 fixture，分离准备和零模型测量阶段 |
| `docs/guides/opencode.md`、`opencode_ZH.md` | 同步说明默认旧 pin、候选未认证与认证命令作用域 |

`cpn/rpnh/frontend_application.py`、MainSession、Registry/PetriNet、provider catalog、TaskControl 都没有本次产品改动需求。若 gate 暴露确切缺陷，先给出最小失败证据再单独评估，不预造 codec 或 recovery 修改。

## 审核和停止条件

先审核这个认证基础设施设计，再实施并跑 G1。安装或执行官方 native binary 另须明确授权和连接到可用本地任务。G2/G3 各自通过并记录 OS/arch/binary hash 后，只能声称该精确组合及场景通过；默认 pin 升级另行评审。

需要原生 plugin、native tools、上游 model/backend、mini UI、原生 Windows/macOS、登录或真实 provider 调用时，立即报告范围扩大，不顺手实施。

# RPNH Codex effort codec：本地验证包

这是已完成、经独立离线审阅的 selection-scoped effort codec 窄修，交给本地补原生 gate。不是新版 Codex 全面适配包，也不含历史分页修复。

先读 `LOCAL_VALIDATION_ZH.md`，按 `LOCAL_COMMANDS.md` 做完整性、目标 old-blob 与安全前置检查，再决定能否进入 native 验证。原 `COMMANDS.md`、`IMPLEMENTATION_ZH.md` 是作者历史执行记录；其中提及的完整开发环境或 HISTORY_NEXT/upstream 全量快照不在此白名单包内，不能直接照搬环境路径。已执行与本地待执行项见 `STATUS_ZH.md`。

- 唯一生产文件：`cpn/frontend/codex_app_server.py`；总计 12 个目标文件。
- 补丁 SHA-256：`3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d`。
- 作者基线 `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`；打包时 main 为 H1 `715468dab0b1bea07d7e94a7aa0606eaf194365c`。
- H1 完整差异与本包 12 路径互不重叠，H1 目标 old blobs 逐个一致；H2a 本地包 5 路径也不重叠，包括双语文档。未重测 H1+H2a+codec 组合。
- `source/` 只有 12 个候选文件，`baseline/` 只有 5 个已修改文件的 old blobs，均不是可运行整仓。请在授权的 RPNH 独立 checkout 上校验并应用，不可整目录覆盖。
- 保留 43-pass/1-deselected 作者及独立复跑、4 个独立补充 probe、AF_UNIX EPERM 和初次 TS fixture 断言失败。`packaging/DISTRIBUTION_PROVENANCE.json` 记录原始到分发的哈希及脱敏变换。
- 不含数据库、真实凭据、运行目录、用户配置、原内部笔记、完整上游仓库、H1/H2a 补丁或历史分页包。

0.155.0 pin 不变。0.161.0 只有 schema/源码检查，实际客户端仍应被 gate 拒绝。原生 0.155.0 model picker、配置往返、空 Registry 冷重开与 AF_UNIX 仍待本地验证；缺 binary/安全入口就明确 NOTRUN。

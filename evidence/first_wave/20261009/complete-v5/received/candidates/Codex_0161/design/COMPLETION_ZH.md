# 前序设计补全与实施边界

2026-10-08 18:56 UTC：前序目录的 DESIGN_ZH.md 声称设计/消费者矩阵完成，但它引用的 COMPATIBILITY_MATRIX.json 实际缺失。本目录保留前序材料的原始字节，并在这里新增矩阵，不回写原目录或冻结产品包。

31 项 INPUT_LOCK 输入均通过 SHA256/字节数复核。原三个 provenance 表的 53 个官方文件均通过 Git blob 与 SHA256 检查；另发现 absolute_path.rs、app_server_version_notice.rs、history_cell_session.rs 没有 provenance，已重新从官方 rust-v0.161.0 精确 tag 读取并逐字节匹配，补录 RECOVERED_SOURCE_PROVENANCE.json。因此现在 56 个官方源文件全部有已验证来源。SOURCE_VERIFICATION.json 是此次实测记录。

COMPATIBILITY_MATRIX.json 是证据/事实/验证状态清单，不是重复的产品能力表。产品唯一能力表仍在既有 codex_compatibility.v1.json；候选能力不会由此矩阵驱动。

前序 DESIGN_ZH.md 第 8 节中的 42 个断言是前序纯合成 JSON schema 探针，不是本次产品实现测试，也不是 Rust/TUI 运行。本次新增结果另存 reports/，不累加成互斥的通过总数。

本实施采用经审阅的显式 compatibility_profile='candidate-0.161.0'：默认 pinned-0.155.0，普通 CLI 不增加自动选择；所有未知 selector/version 拒绝，用户客户端自报不能改变 profile。不下载、安装、登录、调用模型、启动 native/socket、修改身份权限、push 或触发 Actions。

准确路径语义补充：AbsolutePathBuf 在未设置线程本地 base guard 时拒绝相对路径；若调用者主动设置 guard，可相对该 base 解析。当前 RPNH 始终输出 resolve 后的绝对路径，因此不依赖这个可选上下文。不能把 schema 的任意 string 当成已验证路径，也不把源码规则写成“任何情况下相对路径都失败”。

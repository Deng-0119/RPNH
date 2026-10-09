# 本次打包核查范围

本次只做文档、静态脚本与归档核查；没有重跑产品测试/native/模型，没有安装、远端动作或源码修改。

- 12个候选ZIP严格采用V5记录的最终原字节；另保留V5原ZIP和逐字计划文件副本。唯一应用patch为index选中的根patch，不应用历史evidence patch。
- ARCHIVE_LOCK记录13个原ZIP的精确bytes/SHA-256以及全部11,649内层成员的size/SHA-256；验证包括CRC、重复/大小写冲突、路径穿越/绝对路径/反斜线、符号链接/特殊文件及加密成员。
- ORIGINAL_MANIFEST_CHECKS记录18组原始manifest的静态逐行检查，原manifest自身字节也被锁定。有限source清单与完整repo不是同一范围。
- 顶层verifier重算S1 979/core992/history994/lowering999的curated source aggregate和Codex0.161的1044-file锁；不是目标本地checkout的替代gate。
- 包名/成员名检查未发现venv、node_modules、运行DB、客户端binary等禁打包项；高置信凭据模式扫描结果见PACKAGING_PRIVACY_SCAN.json。该启发式检查不能保证所有可能秘密均不存在；本次不添加用户私有配置、运行库或原始业务数据。
- PACKAGING_TOOL_SELFTEST.json只证明包装工具的合成安全/控制流检查，没有产品测试含义。预检Git响应在自验中是mock，不冒充真实用户checkout通过。
- BUNDLE_MANIFEST逐文件覆盖交付目录内除自身的所有文件；最终ZIP可用tools/verify_bundle.py --bundle-zip复核全部外层成员。该manifest不是签名或独立信任根；接收时对照交付消息给出的外层ZIP SHA-256。
- 原ZIP、V5原文、所有旧失败和NOT_RUN记录保持未改；只有新顶层说明负责说明后继范围。最新未通过独审的public-material设计未打入。

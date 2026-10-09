# RPNH Codex 历史投影与本地 native gate 包

完成两个分页RPC、同cut双resume cursor、安全正文renderer和原owner会话每请求/发送前复核。
同时修复Codex threadId必须为UUID的真实consumer契约；其他前端与Registry身份不变。

先读 IMPLEMENTATION_ZH.md 和 HANDOFF.json，再按 native-gate/NATIVE_GATE_ZH.md做本地门槛。

- 主补丁是21文件增量，依赖已交付的native reader与effort codec两个冻结包。
- source/仅为这21个变更文件及许可证，用于审阅，不能当完整checkout覆盖或直接运行。
- 作者119+43个互不重复离线cases通过；独审128通过（有重叠，不相加）；d92两生产文件
  依赖overlay重复同119矩阵通过。原始基底与每个结果分别记录，未声称全HEAD/全仓通过。
- 产品/测试最终冻结后只改中文文档language metadata一行；原测试hash、新patch hash与
  独审doc-only补充逐项对应，不把原128说成在新全文hash执行。
- stock0.155实际TUI/AF_UNIX仍未跑；0.161仅研究，pin不变。门槛脚本不安装/登录，拒绝模型/
  child启动，真实TUI进程必须在另行授权的已有本地环境运行。
- initial writable-connection/DB-WAL失败及修复证据保留。查询现在mode=ro/query_only，
  不新建authority；SQLite协调sidefile与同OS owner竞态保证的边界已明确。

upstream/包含官方未修改JSON/TypeScript/Rust证据、逐blob哈希、Apache LICENSE/NOTICE。
源码未执行TS/Rust编译；离线schema成功不等于stock consumer认证。

本ZIP只用于本地交接，没有上传、安装、登录、模型调用、Actions或远程发布。
SHA256SUMS覆盖所有成员（校验清单自身除外）；verify_delivery.py可独立核验。

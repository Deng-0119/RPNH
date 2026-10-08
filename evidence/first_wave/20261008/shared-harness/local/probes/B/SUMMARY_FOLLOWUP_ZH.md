B 原生补验最终结果：PASS_FINITE（仅 B native 范围）。已有两项 main-session gates 和 text 独立夹具保持原证据、不重跑；父任务授权后的 structured trusted HOST 夹具首轮 j1 通过。合计 4 个不同 pytest case / 4 PASS / 0 FAIL，4 次 scripted logical calls，post-limit excess 0，真实 provider/model 0。

`RESULT.json`、`SUMMARY_ZH.md`、`structured_api_inspection.json` 及初版全部 24 个 artifacts 原字节保留。初版 PARTIAL/BLOCKED 描述的是 AgentTaskSpec 高层入口缺少输出 schema 参数，该限制仍成立；本 follow-up 通过已有高级 HOST 声明入口关闭 structured native 验收，不声称高层接口新增 structured 能力。

新增 `test_native_structured.py` 从既有 `build_agent_task_module` 的纯声明创建本地 ModuleDeclaration，在 adoption 前声明 result schema `application/local_b_structured/v1`，并通过真实 Registration / SchemaCatalog 注册 `type=object`（必需 message:string、values:integer[]、verified:boolean，禁止额外属性）。使用现有 optional HOST binding、预算、start_run、OwnerEventLoop/AF_UNIX、Orchestrator/Harness 和 ExecutionServices 的 optional AgentLoop，只提供 scripted input port，无 production factory、writer、preflight、provenance 或 materializer mock。

- structured 原始命令、UTC 起止时间、exit0、stdout/stderr/JUnit：`j1.command.json`、`j1.stdout`、`j1.stderr`、`j1.xml`；完整 runtime 与请求留在 `.h26/bn/j1/`。没有重试或失败迭代。
- exact refs、hashes、schema authority、operation result 和 terminal 详见 `j1/evidence.json`；原生 outcome=complete，业务 operation result=completed，declared main.result / internal port_2 / complete 对应无误。
- Registry product、workspace snapshot 和 workspace JSON 文档字节均为 108 bytes、SHA256 `9a787067d88985cc070c0a4fd87e207263988fb8dc7d799c99cd1f14ac6b7d94`；按真实持久化 object schema 校验成功，绝非 text 端口内的 JSON 字符串。
- 实际 static prompt/catalog 原字节与 refs 已保存；实际 persisted recipe SHA256 `ff1bfcf1873228a2d139850d641fd03d826344286c4f2cb6402dc4839de519f8`，实际 attempt envelope SHA256 `b8d6c1c4d9da165f545f2ee90860d66528afa193854b4cdc78107ee48fcb8fdf`。真实 materializer 重建与端口收到的 canonical envelope bytes、messages、工具参数全部相等；recipe 与 envelope 哈希不同符合不同文档契约。
- j1 逻辑账本 `[1,0]`，含义为 settled calls / post-limit excess。只读回看前后 head/epoch 为 `[[456, 2], [456, 2]]`，immutable prompt/catalog 原字节不变；本轮没有旧历史执行、resume、reproject 或写入。
- 声明与 schema 在 `j1/authored-module.json`、`j1/declared-output-schema.json`；精确实测源码清单在 `j1/tested-files.json`，新增脚本独立 diff 为 `local_validation_structured_additions.diff`，fixture SHA256 `7a5a48e45aa4f9d5d588c06c2ae89efcc8c2b55bf96fe247d72539f5d46438d7`。冻结 17 文件与两份 patch 哈希前后相同；产品/冻结源码未改，未 commit/push，自有 runtime socket 0。

本结果不改变父任务 A 的 read-host 环境阻塞、A managed 通过记录或 historical reproject NOT_RUN；不等同整体 A/B 全验收。公开材料只含合成 refs/hashes、声明/schema、静态 prompt/catalog 与测试证据，勿上传 `.h26/bn` 的完整 Registry、配置和请求原件。

两份独立夹具 diff 分别为 `local_validation_additions.diff` 与 `local_validation_structured_additions.diff`；合并审阅 diff 为 `local_validation_additions_all.diff`。按用例分列的 1+1+1+1 逻辑账本见 RESULT_FOLLOWUP.json 的 ledger_details。

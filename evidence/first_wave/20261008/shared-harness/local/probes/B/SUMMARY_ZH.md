B 原生验收结果：PARTIAL_BLOCKED。指定起点两项 2/2 PASS，独立 Unicode/多行 text 原生闭环 1/1 PASS。没有失败测试；非 text structured 原生项 BLOCKED / NOT_RUN，不能称 B 全通过。

实测 detached 基线 `c545621c30452650202ad0aca2bc023b6956929b` 加冻结 A/B 17 文件；前后哈希一致，冻结补丁未改。只新增本目录夹具、日志和证据，原生运行完整保存在 `.h26/bn/`。未提交、推送、执行父任务离线测试、真实 provider、Docker、Actions、benchmark、grader 或历史运行。

- 两项起点：`gate.command.json`、`gate.stdout`、`gate.stderr`、`gate.xml`；真实 OwnerEventLoop / AF_UNIX，无 socket/pipe 替换。
- 独立夹具：`test_native_text.py`（及独立 `local_validation_additions.diff`）；仅注入 scripted input port，其余 Registry、provenance、materializer、writer 全部真实。首次执行通过，完整命令、时间、exit、JUnit 在 `i1.*`；精确已加载源码及测试哈希在 `i1/tested-files.json`。
- `i1/evidence.json` 保存实际 static prompt/catalog exact refs 与 payload SHA、实际持久化 recipe exact ref/canonical SHA/source refs、attempt 请求字节 SHA、产品/工作区/operation result/terminal refs 与哈希。可分享静态内容见 `i1/static-prompt.json`、`i1/tool-catalog.json`、`i1/output-schema-authority.json`；完整请求、profiles、Registry DB 仅留本地 runtime。
- 按实际 materializer 重新物化，provider-visible messages 与工具参数逐项一致，canonical envelope bytes 完全相等。logical recipe 与 provider envelope 是不同文档，二者哈希不同属于预期。
- text 产品 schema 为 `application/rpnh_agent_text/v1`、JSON Schema `type=string`。Registry product 是 canonical JSON string（149 bytes），workspace 与 workspace snapshot 是直接 UTF-8（73 bytes），解码内容一致。记录并核对 declared `main.result`、实际内部 `port_2` 和 outcome `complete`；operation result 为 `completed`、native outcome 为 `complete`。
- text 用例逻辑账本 `[1,0]`；两项起点各 `[1,0]`。总 scripted logical calls 3、post-limit excess 0、真实 provider/model 0。tuple 第二项是超限数，不是 fake-call 数。
- fresh 运行只读回看前后 event head 452、writer epoch 2 均不变，prompt/catalog bytes 不变。未运行旧 history、resume/reopen，历史 resumed 验收 NOT_RUN。自有 runtime 残留 socket 0。

structured 阻塞：`AgentTaskSpec` / `AgentStage` / `AgentWorkflowPort` 没有 output schema 参数；`build_agent_task_module` 的 result 与 workflow lowering 的 output data ports 均硬编码 TEXT_SCHEMA。`structured_api_inspection.json` 有实测签名、源码位置及哈希。禁止通过把 JSON 填入 text 端口或替换 module/catalog factory 冒充非 text schema。此为指定入口能力限制，不宣称产品缺陷。若要继续，需父任务明确扩展至现有 ModuleDeclaration/start_run 原生声明入口；本子任务没有越界实施。

完整 per-case 状态、counts、refs、hashes、路径与范围见 `RESULT.json`。离线拒绝/preflight/exclusivity/outcomes 由父任务负责，本轮不重复。

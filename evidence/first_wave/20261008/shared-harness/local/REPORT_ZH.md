# 共享 harness A/B 本地补验

实现与独立代码/原生复核结论为 **ACCEPTED_FINITE**；本地整体结果为 **PARTIAL_ENV**，保留一项环境门槛。两份冻结补丁按 A→B 应用，17 文件保持包内最终字节：A14 包括共享核心 reader 与两个消费者；B3 的唯一生产变化是提示文本，writer/parser/schema/enum 未改。没有为测试修改生产实现或安全检查。

本地初始产品 HEAD 为 `80a17c3ce45ec3c7a1b39c170c36bbb922276de1`，工作树干净。读取 live origin/main 后，仅快进两份技术报告到精确基线 `c545621c30452650202ad0aca2bc023b6956929b`。在工作区独立 worktree 应用并验证 A/B，主仓库接入后的17文件与实测工作树逐字节一致。外层 ZIP SHA-256 为 `33e405d0d6315ac304ea453016ff6e5c855746d6767cd65e7a359c54d765140b`，CRC 与83项 CHECKSUMS 通过，13旧文件/4新增路径、before/after Git blobs 和现有 native-add-v2 lock/archive 均核对。完整来源见 package-verification、source-verification、integration-verification 与原包 file_manifest。

| 独立窗口 | 实际结果 |
| --- | --- |
| A 当前冻结17源上的 targeted 集合 | 原始 pytest：241 passed、1 failed、0 skipped；234.09秒。该失败分层归为 BLOCKED_ENV |
| B focused 集合 | 36 passed、0 failed/skipped；1.35秒 |
| 现有原生 managed-plugin 起点 | 1 passed；57.34秒，逻辑调用4 |
| 新增 A 双消费者/managed 导出 | 1 passed；52.18秒，逻辑调用4 |
| 两项现有原生 main-session 起点 | 2 passed；42.42秒，逻辑调用各1 |
| 新增 B text 实际 recipe/request/write 链 | 1 passed；18.66秒，逻辑调用1 |
| 新增 B 非 text schema 原生链 | 1 passed；17.46秒，逻辑调用1 |

A/B、云端作者/独立/本地、起点/补充窗口分别列出，不相加为新的全仓库通过数。本轮 native 六个不同测试均通过，六个独立运行的 scripted logical ledger 合计12、超限0；真实 provider/model 请求0。逻辑账本第二项是超限数，不是 fake 次数。环境为 Python3.13.12、pytest8.4.2、Linux/WSL2 6.6.87.2，实际 core 从精确工作树导入，全部依赖在 pyproject 允许范围。所有生成物和临时路径在工作区内。

唯一失败为 `tests/test_registry_read_session.py::test_public_config_opens_real_context_in_second_process`。未改的 read_host_config 在进入新 reader 前要求祖先目录属于 root 或当前UID；受管进程 euid=0，而 `/` 与 `/home` 映射为 UID65534，检查按原规则拒绝。普通 WSL deng123 环境的能力探针及定向pytest启动均在 `UtilBindVsockAnyPort:307: socket failed 1` 退出，pytest未开始。这是 WSL AF_VSOCK 启动阻断；当前真实 AF_UNIX 原生测试已通过，不能把两者混为一谈。完整失败/traceback、两次启动 stderr、命令、时间和退出码均保留；没有 chown、放宽权限、修改检查、替换 transport 或尝试其他绕过。该一项仍 BLOCKED_ENV，独立复核确认不是本补丁引入的产品缺陷。

A 的原生 local-only fixture 实际启动两个 pure plugin worker，PID12/13与fixture PID8不同，产生真实 started/returned receipts；没有注入 action rows。同一次 HA 导出包含2条 managed tool observations与当前 terminal communication，environment_host 与 HA 各自使用正确 core-bound cut，authority/evidence/result refs完全一致。读取前、HA后、environment后、最终 head893、epoch2、268对象及其字节摘要一致，读阶段没有新 owner/writer/dispatch。两条 managed return 的 provider delivery 仍为 unproven，保留 `provider_delivery_not_recorded` 与 null visible-result refs；不据返回记录推断模型消费。

B fresh text 保存真实 Registry static prompt、optional_agent_tool_catalog、logical_provider_request_recipe 的 exact refs、payload SHA/大小与 source refs，并从实际 recipe/prompt/catalog 用现有 materializer 还原 scripted port 的 canonical request bytes。messages/tool parameters 与 envelope字节一致；recipe和envelope是不同文档。Unicode/多行 text 的 Registry product 是 canonical JSON string（149字节），workspace/snapshot 为直接 UTF-8（73字节），解码内容一致。output/outcome/operation result/terminal 的精确绑定均核对。

高层 AgentTaskSpec/AgentStage/workflow ports 现有接口只声明 text；最初 B 子任务的 PARTIAL 与 API 检查保持历史原件。随后使用任务允许的既有 trusted HOST ModuleDeclaration/Registration/start_run 原生入口，显式声明 `application/local_b_structured/v1` object schema 后执行真实 OwnerEventLoop/ExecutionServices/Orchestrator，补齐真正非 text 验收。product/workspace/snapshot 为完全相同108字节 JSON 文档，schema authority、结果与终态 exact refs、prompt/catalog/实际recipe和请求重建核对均通过。没有修改高层生产接口或用 text 内的 JSON 冒充 structured schema。

Fresh prompt/catalog 回读保持不可变；历史 resume 未执行、未改写，单列 NOT_RUN。真实 OS 并发 writer pressure 未运行，head/epoch漂移是已有确定性边界测试。历史 reproject 在已声明近期 Office任务、local runtime、archive/runs范围找到56个run_status，但没有完整 completed native-live HA 前置集合，故 reproject 和实际历史 stale-driver 场景为 NOT_RUN；没有运行/resume/backend/grader补材料。现有单测中的坏 ref 拒绝与这些未运行的历史场景分开记录。原历史分数和实验身份未变。

独立 local-only fixture 源码、diff/hash 和执行命令保存在 probes/A、probes/B及 local_validation_additions.diff，不混入冻结 A/B。原包旧 HA-only108通过记录、新共享实现242及最终说明性delta、B旧反证/EPERM、B初版PARTIAL均分版保留。本轮未运行真实模型、Docker、Actions或业务评分，未改变安全配置，未生成返回ZIP。按用户长期授权提交推送已审阅修复与证据；验证快照中的 no_push=true 仅指该快照形成时尚未发布，最终交付另有 push receipt。

公开材料仅含任务相关源码、日志/JUnit、最小 refs/hashes 与合成静态prompt/catalog/schema；Registry/Bank数据库、provider profiles、完整请求/对话、环境缓存留本地。本地公开副本仅替换私有工作区前缀，原件/导出摘要与每项转换在发布MANIFEST；原包文本按原字节保留。RETURN_VALIDATION_SNAPSHOT.json 通过原 return_schema，明确 A=BLOCKED_ENV、B=PASS 和剩余门槛。

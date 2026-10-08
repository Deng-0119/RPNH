# ERP unknown 本地补验结果

本包要求的 A/B/C 补验均通过，可进入统一代码/证据审核；独立软件审阅 ACCEPTED_FINITE。没有发现需要改 core 或原五文件补丁的阻断问题。仅新增一个独立原生验收脚本。按用户既有的 GitHub 发布授权，审核后提交推送；不另造返回 ZIP。

| 层次 | 结果 | 实际覆盖 |
|---|---|---|
| A offline / mock | passed | 26 + 39 = 65个不同pytest node IDs，另4个subtests；原15个AF_UNIX EPERM阻断项全部逐项通过 |
| B installed native | passed | installed rpnh入口、TaskControl owner/worker/AF_UNIX；complete、公共stop、wall timeout三场景 |
| C installed native | passed | 六个真实worker/AF_UNIX/Registry场景，直接owner API驱动；无内存transport替身 |
| provider / model | not_run / out_of_scope | 本轮确证真实调用0；B仅fake provider |
| ERP / Docker / evaluation | not_run / out_of_scope | 合成backend不执行source、不访问真实Odoo、未启动Docker/world/grader |

A 的65项全部通过，没有skip/deselect。原云端50 passed +15 failed/EPERM仍为历史事实，不改写为65通过，也不与本地相加为130。旧实现2项预期红灯已在这些IDs中，不额外加2。完整逐项映射见 `offline-deduplicated-results.json`。

B 实测fake submissions为complete4、stop3、timeout3（共10），与各自Registry账本和本地fake transcript相符。三个场景各有一次合成backend effect。stop/timeout都在backend实际入场后取消，backend在bridge teardown前退出；owner quiescent、process_exit_confirmed均为true，forced_termination为false、shutdown_error为null。complete有实际terminal refs；stop/timeout没有业务terminal，不冒称业务完成。worker/bridge/backend不是三个独立业务效果。计数API原tuple是settled total / post-limit excess，第二项不是fake次数；fake来源由本地脚本选择及transcript独立确认。

C 的实测分层计数如下：

| 场景 | worker dispatch | bridge request | backend effect | 回执 |
|---|---:|---:|---:|---|
| explicit_unknown | 1 | 1 | 1 | started → outcome_unknown |
| backend_exception | 1 | 1 | 1 | started → outcome_unknown |
| lost_completed_reply | 1 | 1 | 1 | started → outcome_unknown |
| known_failed_nonzero | 2 | 2 | 2 | 两组started → returned；原exit7保留 |
| known_domain_infeasible | 2 | 2 | 2 | 两组started → returned；原status/输出保留 |
| known_completed | 2 | 2 | 2 | 两组started → returned |

三个unknown都经真实worker闭合错误协议handler_failed进入Registry，未出现returned。同ID同参数、变参、新ID写入在原service和重建service共18个探针中不再派发，回执和三层计数不变；bridge uncertain保持true。丢回复只在backend completed之后的真实server socket发送处注入。已知结果的缓存replay和changed-body conflict不执行，合法新ID可以执行，status/stdout/stderr/exit/execution/effect identity均保留。

9次真实worker派发有SO_PEERCRED、PID/PPID及spawn_main观测，18份实际receipt核对了started/terminal、execution/firing/producer及catalog关联。worker finally的SIGTERM与owner异常强杀分开记录；随后SIGKILL均为ProcessLookupError。父任务最终复查9个worker PID已不存在，B/C无遗留socket。C是同一owner/Registry重建service，不是OS owner崩溃恢复。

本次实际验证基线为 `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0` 加原五文件patch和独立C fixture的未提交工作树。远端 `ac19c575bd18e5e0fbf2bef017e8d74c1b6d6afd` 仅修改两份技术报告；验证时没有替换基线。安装为专用Python3.13非editable环境，rpnh-harness0.1.0rc2、rpnh-erp-bench0.1.0、Harbor0.24.0、pytest9.1.1。关键4模块在venv并与源码逐字节一致；plugin.py SHA为 `13d9c09b3eb9b30f326f4daf9d9745309844c55f10cc02f832e9c984d63479bf`。

原patch SHA `3a0e9da55cae901933eef84221d67b47719fa57e9be6858d885a9f7b8c13fbf0`，五文件payload前后均保持原字节。新fixture单独位于 `examples/erp_bench/scripts/unknown_native_acceptance.py`，独立patch另附。最终779文件清单覆盖所用core/ERP/测试/fixture及包输入范围，189个实际加载模块均匹配；该清单不是完整repository tree。HEAD tree、tracked git diff和两个untracked源码清单均单独记录，不能只凭commit忽略overlay。

公开材料是检查过的脱敏projection或日志副本，不冒称原始网络包。仅替换私有路径前缀，完整异常、断言、结果、相对路径和原/分发hash保留；原件在任务目录及私有B/C现场。包内原云端日志按已交付导出字节保留，原作者已有的脱敏/provenance说明不改写；未收到的云端未脱敏原件不冒称已在本机。raw Registry/profile/fake transcripts、venv/cache不上传。

明确保留边界：interrupted仍returned且bridge防写；发送前connect failure仍可能保守unknown。其他native变体bad_schema/wrong_identity/partial_send/interrupted/connect_failure未扩建补跑，已有offline覆盖不冒称其native通过。未测token/费用保持null，不估算为0。本次不改变ERP100/21或SCB13/13、25/25、40/47等历史业务成绩及其源码身份。

交付方式采用用户明确的长期GitHub授权，覆盖包内默认“不推送/返回ZIP”流程；结果ZIP未创建，故不存在结果ZIP CRC通过的声明。输入ZIP的CRC与28项内容清单已验证。完成后等待用户分配新任务，不自动扩展真实实验。

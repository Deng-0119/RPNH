# 共享 harness：A/B 本地补验与原始阻断

代码经独立复核 **ACCEPTED_FINITE**；本地整体 **PARTIAL_ENV**。A targeted 原始结果为 **241 passed / 1 failed / 0 skipped**，该一项归为环境阻断；B focused **36 passed**。新增/既有六个不同原生测试全部通过，分别保留 A managed-action/双消费者与 B main-session/text/非 text structured 的窗口，不累加成全仓库覆盖数。

执行身份为精确基线 `c545621c30452650202ad0aca2bc023b6956929b` 加冻结 A→B 两补丁，17 文件保持包内字节。A14 包括既有 Registry 中的共享 frozen-cut typed reader、strict/bounded reads 与两个消费者；B3 仅改变生产提示文本，writer/parser/schema/enum 保持不变。

- [完整本地报告](local/REPORT_ZH.md)、[按原 schema 填写的验证快照](local/RETURN_VALIDATION_SNAPSHOT.json)、[独立代码/原生复核](local/review/)、[测试窗口清单](local/native-inventory.json)。
- [A 原始失败与完整 traceback](local/logs/A-offline.stdout.log)、[环境阻断说明](local/environment-blocker.json)、[WSL 定向启动 stderr](local/logs/A-readhost-native-recheck.stderr.log)。
- [A 实际原生 managed-action/终态导出](local/probes/A/native-observations.json)、[两消费者只读指纹](local/probes/A/read-only-observations.json)、[B text 链](local/probes/B/i1/evidence.json)、[B 真正 structured 链](local/probes/B/j1/evidence.json)。
- [原云端交付与失败历史](cloud-handoff/)、[新增 local-only fixture diff](local/local_validation_additions.diff)、[逐文件原件/公开副本摘要及必要转换](MANIFEST.json)。

剩余的一项 `test_public_config_opens_real_context_in_second_process` 在未修改的配置所有权检查处拒绝当前命名空间的祖先 UID65534；普通 WSL 用户的定向复验又在 AF_VSOCK 启动阶段被拒绝，pytest未开始。代码、权限规则与 transport 均未放宽。它与已通过的真实 AF_UNIX 原生验证分开记录。

A 同次导出两个真实 pure plugin managed return 与当前终态；HA/environment 的精确身份一致，读取前后 head893、epoch2、268对象字节不变，读阶段无新 owner/writer。managed result 的 provider delivery 仍未证明，保留 `provider_delivery_not_recorded`。

B 两条 fresh 写入链具有真实 Registry prompt/catalog/recipe refs 与内容摘要，并经实际 materializer 核对 captured envelope。structured 使用现有 trusted HOST 声明入口，object schema 下 product/workspace/snapshot 同为108字节 JSON 文档；高层 AgentTaskSpec 仍为 text-only。原子内容/来源/outcome/port 约束由独立离线窗口验证。

六个原生运行的 scripted logical calls 为4/4/1/1/1/1，超限0，真实 provider/model调用0。历史 reproject/resume及OS并发writer压力未运行；没有 Docker、Actions、业务评分或历史分数改写。数据库、private profiles、完整请求/对话、环境缓存和ZIP留本地。验证快照在授权推送前形成，其中 no_push=true 是时间点事实；交付按用户长期 GitHub 授权执行。

## 公开证据的可解析副本与元数据勘误

原始证据、失败记录、验证快照与旧 MANIFEST 保持不变。全部 21 份 XML 已核对；仅 A-offline 的 9 处脱敏占位符需要 XML 转义，提供 [可解析派生副本与说明](derived-junit/README.md) 及 [独立 provenance](derived-junit/PROVENANCE.json)。仍为 242 tests / 241 passed / 1 failed / exit 1，本地整体仍为 PARTIAL_ENV。

[验证快照的独立元数据勘误](derived-metadata/VALIDATION_SNAPSHOT_ERRATA.json) 精确定位 9 处 A 整体命令退出码及 3 处原生证据命令来源；单项 case 状态与进程退出码分开解释，B 聚合项保留两次独立运行的 receipt。没有改写原快照、制造新运行或扩大通过范围。

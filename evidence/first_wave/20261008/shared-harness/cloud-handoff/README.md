# RPNH 共享 harness 本地补验包：Registry 原生 reader 版

日期：2026-10-08。交付候选修订：2。仓库：Deng-0119/RPNH。
实测源基线：`c545621c30452650202ad0aca2bc023b6956929b`。

本版仅提供两个当前补丁，按 A → B 应用：

- A：在既有 Registry run_authority 中提供 frozen-cut typed execution reader；复用原生 exact-view、descriptor 和 resource provenance 边界；HA exporter 与 environment_host 两个 consumer 共用该 reader。包括必要的 physical bounded read、strict descriptor 可选能力、测试和双语文档。
- B：共享 optional-agent write_file 提示与现有 text / JSON / exact source_resource_ref 契约对齐；B 本身不改 writer/runtime/parser/schema/enum。

先前的 HA-only A 已被替代，其 patch 不在本包，也不是应用选项。旧 A 108 passed 只在 history 下按历史结果保存，绝不作为新公共 reader 的验证结论。新 A 与 B 结果分别记录，不求和。

新 A 的实现候选获得作者和独立各 242 passed；最终 docstring/双语文档补充的 None-delivery 说明及其针对性验证另列，详见 validation_summary.md。B 作者与独立各 36 passed。native/local gates 仍需完成，不能把这些离线结果当作整体原生验收通过。

## 使用顺序

1. 阅读 validation_summary.md 和 REVISION_NOTICE.md。
2. 将 LOCAL_AGENT_TASK.md 交给本地 agent，在正确 checkout 中保护 dirty 状态、核源、应用并补验。
3. 按 return_schema.json 填写 return_template.json，返回最小日志/refs/hash 证据和独立 local-only fixture diff。
4. 留待人工一起审核；不 push、不合并，不开启真实模型或业务评测。

## 内容与完整性

- patches：仅两份当前 patch 原件。
- source：17 个最终 changed files 的白名单原件。
- baseline_source：其中 13 个旧文件的精确字节；4 个 added 路径旧值为 null。
- file_manifest.json：逐文件旧/新 SHA256、Git blob、大小和补丁身份。
- evidence：新 A、B 的已有日志，失败归因、独立验证摘要、来源及包装完整性核验。
- history/withdrawn_HA_only_A：旧候选证据，独立分版；不含旧 patch。
- CHECKSUMS.sha256：除自身外所有成员 SHA256；外层 ZIP hash 随交付说明提供。

当前 A SHA256：`61f2f7115306b8c39846d34e77918c31ae4ccf9f62c0a8835a661c7124400137`。
B SHA256：`af1e325ad37099ed1430d282337999c8d1080fa00fae31f8ff2d2c9f59a41a76`。
A 原字节紧接 B 原字节、不加分隔/换行的组合身份见 file_manifest.json；它不是第三份需要应用的补丁。

不含完整仓库、Registry/Bank 数据库、凭据、私有 profile、provider 原始对话或内部方案笔记。没有新增本地验收脚本；若本地需要新增 fixture，另列 source/diff/hash，不混入已冻结 A/B。

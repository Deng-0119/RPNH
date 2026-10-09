# MainThread fixed-cut 原生只读前置包

## 交付结论

完成窄的 native refs-only 接口：capture_read_cut、project_thread_at、page_turns_at、
page_items_at。只有本包七个文件进入 patch，未更改 Codex adapter、未合 codec、H1/H2、
Registry core/ObjectStore 或 typed-reader catalog。两版 Codex 的两个分页 RPC 仍未实现，
不宣称修复 stock client cold resume 或新版兼容。

patch：`native-main-thread-history.patch`。
准确 scope、Git blob、SHA-256 见 `file-manifest.json`；apply 见 `patch-apply.json`。

## 固定切面与安全范围

- CanonicalView 本身只有 ordinal，因此用既有 task_id、branch_id、boundary event_id 与
  exact thread_ref 组成 source-bound cut。每次重校源身份、ordinal、event、thread与lineage。
- thread/turn/link header、canonical membership 与 immutable bytes 均贯通同一 cut；
  不用 get_version 的 ambient positive memo冒充历史 view。当前/recover 原字典形状保持。
- 新 projection 只含 committed turn 的 exact refs、两个完整字段槽 user_input/answer与
  at-cut child-link facts；不返回任意 JSON、不读取 child body、profile 或 TaskControl。
- raw input 可含 native_plugins 配置，answer 可含 task.prompt。当前 catalog未给公开
  main-thread body reader；本包停在这个边界，不创造 grant、不绕权限、不开放内部字典。
- TaskControl实时 launch annotation不属于immutable snapshot；保留原display_history
  渲染路径，固定cut只单列immutable linkfacts，不假造当时TaskControl文本。
- 既有child path范围/symlink校验仍检查当前filesystem，但不打开childRegistry；安全边界
  被修改会让旧cut fail closed，不能以快照稳定为由绕过。
- 默认body读取界限是每个对象登记size，不引入4MiB cap；显式可选max_object_bytes只能
  拒绝整个请求，不能截断/遗漏或放大物理界限。>4MiB旧JSON兼容有回归。
- 每页会重建验证该cut全部lineage。100是返回entry上限，不是整体CPU/RSS或扫描上限；
  metadata仍用既有native query API。没有historyDB/cursorstore/持久化快照副本。

## 验证与基线

起始 main：ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4；隔离baseline中1015文件与该
Git tree blob逐一吻合，见baseline-provenance.json。结束时main为715468，新增一个
owner-entry commit，七个目标blobs均未改。最终源码在原依赖组合57 passed/9 deselected；
单独覆入715468已合runner/agent_tasks后，同一矩阵再次57 passed/9 deselected。
这不是114个unique cases，也不是所有MainSession/native执行路径通过。

覆盖稳定多页完整性、asc/desc与turn内顺序、追加不污染旧cut、crosscut/source/thread拒绝、
inclusive/exclusive、empty/partial/stopped/failed、provisional/future过滤、损坏拒绝、
原display私有字段过滤、大descriptor和登记size物理界、read-only无写/counter变化。
py_compile及四个文档页定向parser检查通过；完整site build未运行。

没有真实模型/provider、Actions、remote写入、登录、安装或stock Codex运行。读测试只在
fixture setup写Registry；被测read阶段禁止writer入口/SQL mutation并核持久化状态不变。

独立review结果由reviewer另附；后续adapter边界见ADAPTER_NEXT_ZH.md。

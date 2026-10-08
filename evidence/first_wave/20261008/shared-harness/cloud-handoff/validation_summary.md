# 验证摘要和开放的本地 gate

## A：现有 Registry 公共 frozen-cut typed reader

当前 A 共 14 个 changed files：共享 reader/descriptor/object store，HA exporter/recovery，environment_host，相关测试及双语文档。独立审查确认复用既有 current authority、exact-view、readable_descriptor 和原生 resource provenance；两 consumer 不再自行重复终态身份链匹配。

RunReadCut 绑定一个具体 core handle、kernel 对应关系、task/native run、canonical view、physical head 和 writer epoch。caller expected refs 是断言，不选择 authority；不能将旧 view 混入新 head，也不能跨 core/错误 kernel 复用 cut。当前 terminal 来自同一冻结 cut；历史 generation 可以存在但不能关闭 reopened active generation。

reader 返回 typed exact identities 与进程内 PreparedObject，不新建 observer grant、写权限或远程 locator。descriptor strict JSON 拒绝重复 key/non-finite，physical bounded reads 覆盖实际 backing-file 大小。普通 resource 保留既有 exact-version/length/provenance 契约，没有冒充 Registry 未存储的历史 content hash；返回日志中的 SHA256 只是实际读取 bytes 摘要。

HA 暂存 observations，并在暴露前复查同 cut；environment_host 在 renderer 后复查同 cut，保留 opt-in、strict JSON/大小限制。fresh owner 继续断言 startup net；resumed owner 无 publication 时，仅省略不存在的 caller net expectation，保留 run/task/evidence 和 Registry 闭包验证。其 None→not_terminal 早退只表示没有 delivery 候选，不是“Registry 当前必定非终态”。

### 实验阶段与来源

- 共享实现候选 SHA256：5aac652ea4c0c76e8641581b1a972a31c992c1b70dc32bc2aa9094e52883dfc1。
- 作者最终相关集合：242 passed，162.23s，无 skip。
- 独立复测：242 passed，161.01s，无 skip。
- 70-case core、43-case integration 是较早阶段，与 242 集合重叠，不相加。
- 当前冻结 A SHA256：61f2f7115306b8c39846d34e77918c31ae4ccf9f62c0a8835a661c7124400137。242-case 实测发生于最终说明性 docstring/双语文档 delta 之前；delta 无执行逻辑变更，针对性 None 回归和最终审查单列，不重标 242 的源版本。

原始失败保留：first-tests 38 passed/1 failed，原因是旧 adapter 测试期望 ValueError，而复用原生 descriptor 抛出 RegistryConflict；按原生边界修正测试。consumer-tests 143 passed/26 failed，其中 25 项因局部快照缺失同提交 examples/package_reuse fixture，另 1 项发现 resumed owner 没有 publication 的真实兼容 gap。取得同一基线的 lock/archive 并验证 Git blob、修正仅缺省 caller net expectation 后，43-case integration 及最终 242 通过。未将缺 fixture 的错误归类为 socket 阻断，也未隐藏真实兼容缺口。

这些测试使用真实临时 Registry/object store 和 owner 静态 settlement；禁止 executor/model/socket 等边界按用例检查。head/fence 漂移为确定性注入，不是 OS/跨进程竞态实测；只读 fingerprint 关注事件 head、writer epoch 和 immutable object bytes，不声称整个目录逐字节无任何系统元数据变化。原生 managed-plugin 导出、AF_UNIX/process、现存完整 live-run reproject 仍是独立本地项。

## B：write_file 提示对齐，原字节不变

B 3 个 changed files。唯一生产变化为新 optional-agent prompt 文本；B 不改 parser、schema、enum、writer 或授权。

- text schema 的 content 直接文本；旧 JSON-string 兼容仍在，JSON-looking 文本仍按 text。
- 其他 schema 的 content 是符合声明 schema 的单份 JSON 文档。
- content 与 exact visible authorized source_resource_ref 互斥；workspace 旧 bytes 不是隐含来源。
- outcome/port、immutable failed action、completion ordering 保留。

作者与独立各 36 passed，分别记账。生产 prompt 修改前的反证为 2 failed/29 passed；最初 fixture authoring 错误只作开发历史。pure materializer 使用真实编译声明但 fixture refs；writer boundary 有 publication/delivery seams，不能当成真实 Registry prompt/recipe 注册和 native provider-visible chain。

native direct-text 现有回归曾在 AF_UNIX socket 创建时 EPERM，1 failed 于 agent 执行前，分类 BLOCKED_ENV。不得写成 native pass。真实 text 和真正 structured-schema 的 write/result 链、canonical recipe/可见 request 一致性需要零真实 provider 的本地 scripted fixture。

## 组合与未验范围

17 个 changed files 路径不重叠。A→B 的 apply --check、应用后逐文件 SHA256 及原字节组合检查见机械核验；不将机械 apply 当额外 runtime 实测。

完整仓库套件、真实模型/provider、业务 benchmark/grader/历史分数、Actions 与 push 都未运行或改写。历史 live Registry 有完整材料时只读 reproject 到新目录；缺前置材料即 NOT_RUN，不运行/resume/model 造证据。过期 driver ref 的拒绝保留为期望失败闭合，不修旧数据后冒充同一测试通过。

本地补验与两个 consumer 的 native gate 未关闭前，本包不是整体原生验收通过证明。A/B、作者/独立/本地各阶段分别记录，不相加。

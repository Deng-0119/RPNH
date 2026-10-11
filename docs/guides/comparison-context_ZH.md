---
name: rpnh-comparison-context
description: "只读比较已授权的 exact PN 定义与检查点。"
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: comparison-context.md
  revision: "2026-10-11.1"
  status: implementation-candidate
  basis: "public exact read session; descriptive comparison only"
---

[English](comparison-context.md) | [中文](comparison-context_ZH.md)

# 独立 exact 网比较

独立 Viewer 读取 HOST 已明确选择的 Registry 只读 session，可比较已登记的 exact 网定义、
明确保存的 checkpoint，以及已保存的 author PN 投影，不依赖当前运行。它不编译、执行、
采用、合并、签发 grant 或修改 Registry。

使用已有的可信本地 read-HOST 配置启动：

```sh
rpnh net --read-host-config /absolute/path/read-host.json --view --no-open
```

这是同一操作系统用户、仅监听回环地址的 Viewer。配置选择已有 source binding 与 owner
签发的 observation context；HTTP 输入不接收路径、principal 声明、grant、回调或 compiler。
index、record、材料正文与 export 权限彼此独立，打开 Viewer 不会授予这些权限。
配置与签发细节见独立 Registry read-session 指南。原有 `rpnh net --run ...` 行为保持不变。

## 普通合并结果

新建 `PlainModuleMergeAuthor` 结果会在实际双父版本 M 提交前，持久化确定性的
`rpnh/plain_merge_public_pn/v1` 投影。Viewer 直接选择 M，显示它的现有 PN
节点、弧和配置引用。M 与父版本的比较采用 `full_pair`；合并对应关系和运行时
连续性仍为 `not_provided`。

合并专用 `rpnh/stored_plain_merge_public_pn/v1` 封装冻结有界的祖先清单证明，
绑定原始来源、精确材料、命令、显式冲突选择、HOST 声明和 schema 权威。
读取方仅从已授权固定切面的字节重建最近共同基点、规范化分析及解析后的 Module，
不编译、不调用 HOST、不查询当前事实，也不写 Registry。全部合并证明材料都
是必需的；拒绝访问或损坏不能降级为仅拓扑回退。本版保留会话现有的行数及材料
总字节限制；有效的大型合并可能超出所选观察者的视图预算。这些视图限制不会
拒绝权威合并。图作者、不透明 designer constraints 及不支持的作者历史仍在范围外。

原结果命令及其五份预备材料保持不变。发布流程在最终版本切面之前增加一个
不可变投影切面；中断后重放复用原精确身份与字节。没有已存投影的历史完成版 M
仍返回 `PROJECTION_UNAVAILABLE`，读取和重放均不补写。普通作者及 Assembly
投影继续沿用原有合同。

## 选择与导航

1. 左右各选择一个来源和明确的 exact 对象，不隐式选 latest，也不依赖当前看板的 checkpoint
2. 显式选择 definition、configuration、materials、runtime 四轴；未请求轴显示 `not_requested`
3. 读取完整 pair。左右画布独立缩放、移动与选节点，使用现有 NetRenderer 和 ELK
4. 选择声明的 module/member 范围，或缩到所选节点。层级只来自已验证声明和 occurrence path，
   不从名称或布局猜测；父级导航遵循声明。内部弧与边界连接分开，边界占位不成为新 PN owner
5. “完整双网”按返回的相同 SourceCuts 恢复两侧 exact 网。完整范围超限或不可用时清空旧 scoped
   pair，不把静默裁切后的数据标为完整

未提供声明层级时保持平面图并明确标注。SourceCut 句柄绑定 session，篡改 head、跨 session
复用或偷偷换新 cut 均拒绝。后续普通 append 不必使可重建的固定 cut 失效；binding、fence、
catalog 或 authority 变化则使观察失效。

## 三种模式

- `reliable_diff`：已验证对应闭合当前范围的内部节点与弧；单个字段仍可 unknown
- `partial_mapping`：只比较有证据的部分；其余对象独立保留，不捏造新增/删除
- `full_pair`：没有可靠对应时独立阅读双网；同名、同字节不建立 identity

retained author identity 与复制来源是不同声明。一个保留的来源可以对应多个不同副本，交换
两侧后仍明确复制方向。split/fusion 保留包含弧的整组关系，不展开为笛卡尔积，也不推断 runtime
identity。一般多对多仍 unsupported。手工视觉配对仅作用于当前显示，不提高可靠性，不写 Registry。

对于使用已登记 native-plugin v1 组件的直接复制或保留 operation，读取器也可
验证生成的 capability place 和 read arc。它向已授权的 parent projection 核对
完整的存储 lowering 配方，以及 exact 配置、operation、plugin/executor、tool 和
schema 登记信息。该证明使用 `rpnh/native_capability_derivation/v1`，仅表示作者
对应关系；不会串联 parent 历史、编译网或证明 token 连续性。配置变化、缺失
parent 材料权限、其他 lowerer 和共享 capability carrier 继续保持 unknown。

## 四个独立轴

- definition：通过已验证的主体对应，比较明确提供的公开节点/弧字段
- configuration：比较允许公开的 exact 声明/要求引用。ref 不同不证明实际 model、tool、workspace
  值不同；HOST registration requirements 与 package environment requirements 保持不同类型
- materials：使用 exact token resource refs 和独立授权的 headers。只有授权正文读取后才计算正文
  digest；初始缺正文权限时保持 unknown。弧的 Boolean `resource` 标记绝不当作材料引用
- runtime：当前仅覆盖所选 checkpoint 的 marking 与 exact token occurrence refs；firing、活动和
  completion evidence 明确 unknown。仅定义目标不借用当前 marking

明确提供的 null、未知字段与已证明缺失不同。known_same 只表示指定比较器下一个已公开字段
相同，不表示业务等价或可安全替换。counts 仅统计当前 exact scope 的 field rows；未提供数量
是 null，不是零。

## 发布与生命周期

服务器读取两侧及证据、验证整个 DTO，并在发布前终检所有涉及来源，不流式吐出半份 pair。
错误只含固定 no-store code，不含私有路径或异常细节。comparison-only provider 不提供旧 raw-net 路由。

selector、scope、axis 或 visual pair 变化时取消在途读取，清空双图、mapping、counts 和详情。
晚到响应不能回填新 generation。关闭、看板导航、Back/Forward、pagehide 与 bfcache 恢复都会
丢弃 pair，并保持用户原来的 live-refresh 偏好。

新只读路由是 `GET/HEAD /api/v2/comparison-selection` 与
`GET/HEAD /api/v2/comparison-context?request=<JSON>`，分别消费
`rpnh/comparison_request/v1`、返回 `rpnh/comparison_context/v1`，输入输出均为封闭 DTO。
旧 `/api/v2/comparison-view` 与 `rpnh/checkpoint_comparison/v1` 仍要求 same exact net，
保留原 current-capture 语义。

## 验证边界

确定性 Python 测试使用 canonical owner issuer、真实 stored author producer、真实 checkpoint
publication、公共 session、权限与 cuts。Node 测试覆盖封闭 DTO 与取消；controlled-DOM 用例运行
实际双 NetRenderer 生命周期与真实 ELK 几何。以上不等同于浏览器绘制、无障碍、移动端、OS
隔离或恶意 HOST 安全审计。若执行环境阻止 browser/loopback，应在受支持的本地环境完成剩余
检查，不绕过限制。

---
name: rpnh-comparison-context
description: "只读比较已授权的 exact PN 定义与检查点。"
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: comparison-context.md
  revision: "2026-10-11.3"
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

大请求使用只读 `POST /api/v2/comparison-context`，`Content-Type: application/json`，
正文为同一闭合 v1 请求 DTO。浏览器在编码后的 GET 路径达到 60000 字节时自动改用 POST，
避免 HTTP 请求行 65536 字节限制；较小请求继续使用 GET，原 HEAD 仍支持。
POST 必须有单个有效 `Content-Length`，不支持分块传输；server 在读取、分配正文之前按
HOST `max_request_bytes` 检查声明长度，超限返回 413。解析器也校验实际 UTF-8 正文字节，
包括空白；重复 JSON 键仍拒绝。Python 对应 API 为
`parse_body(raw: bytes, *, host_limits=None)`，本地请求 helper 为
`handle_request(provider, "POST", "/api/v2/comparison-context", body=raw)`。
该 POST 只执行原有只读比较；其他路由的方法与 owner 操作保持原状。
比较响应发送与预算校验完全相同的紧凑 UTF-8 JSON 字节，`Content-Length` 为该字节数；
HEAD 返回相同长度头而不发送正文。

## HOST 资源预算

`LIMITS` 是默认值，不是产品固定上限。可信 Python API 为
`ComparisonProvider(session, *, host_limits=None)` 和
`comparison_context(session, request, *, host_limits=None)`。映射可只覆盖部分字段；
未指定项保持以下默认值：

| 预算 | 默认值 |
|---|---:|
| `max_nodes_per_side` | 10000 |
| `max_edges_per_side` | 30000 |
| `max_mapping_relations` | 20000 |
| `max_field_rows`（四轴合计） | 200000 |
| `max_evidence_reads` | 20000 |
| `max_response_bytes` | 16000000 |
| `max_material_bytes`（单份可选正文） | 1048576 |
| `max_selection_cursors`（缓存 continuation 句柄） | 128 |
| `max_request_bytes`（UTF-8 JSON 正文及规范表示） | 1000000 |

覆盖值必须是正整数，不能是布尔值，范围为 JSON 安全整数 1 至 9007199254740991；
未知键会被拒绝。整数传输表示、引用格式、字符串形状及 occurrence-path 形状深度限制
仍是协议校验，不是资源配置。

请求和 selection DTO 的 `limits` 仍仅包含原来的六个字段。客户端可降低预算；超过 HOST
配置值时返回 `scope_limit`（HTTP 413）。底层 session 的 `ReadLimits` 仍有效：响应字节
预算与 session 响应预算取最小值；可选材料正文取 HOST 单份预算、session
`max_material_bytes` 和比较剩余字节预算的最小值；selection 游标取 HOST 容量和
session `max_cursors` 的最小值。其他 session 读取门仍生效。selection 路由公布有效的
六项预算，浏览器将其用于请求。HTTP 解析与两端响应校验均使用实际预算，不再附加按
默认规模固定的图/字段行数组上限。不静默裁切输出；过大的可选正文仍按原语义不披露。

例如，将以下策略放入独立可信本地 `comparison-policy.json`，不要放入 HTTP 请求或
闭合的 read-HOST DTO：

```json
{
  "max_nodes_per_side": 20000,
  "max_edges_per_side": 60000,
  "max_mapping_relations": 40000,
  "max_field_rows": 400000,
  "max_evidence_reads": 40000,
  "max_response_bytes": 32000000,
  "max_material_bytes": 8388608,
  "max_selection_cursors": 256,
  "max_request_bytes": 2000000
}
```

```python
import json
from pathlib import Path
from cpn.frontend.comparison_context import ComparisonProvider
from cpn.frontend.server import serve_projection
from cpn.rpnh.collaboration.read_host_config import open_read_host_session

policy = json.loads(Path("/absolute/path/comparison-policy.json").read_text())
with open_read_host_session("/absolute/path/read-host.json") as session:
    provider = ComparisonProvider(session, host_limits=policy)
    serve_projection(provider, host="127.0.0.1", port=8765, open_browser=False)
```

已有 `read-host.json` 的 `limits` 对象也必须允许相应 session 预算，例如
`{"max_response_bytes": 32000000, "max_material_bytes": 8388608, "max_cursors": 256}`。
现有加载器用它为 HOST binding 构造 typed `ReadLimits`，session request 省略
limits 并继承这些上限。直接通过 Python 建立 session 时，配置
`RegistryReadHostBinding(..., limits=ReadLimits(...))`；省略 request limits
即可继承，或用 `ReadLimitOverrides(...)` 进一步收紧指定字段。显式传入
`ReadLimits(...)` 会约束其全部字段，包括按默认值填入的字段。请求不能提高 HOST 上限。
普通 CLI 启动器保留 Comparison 默认值；需要覆盖时使用上述显式启动示例。

自定义 HTTP 集成应向 `parse_query(raw, *, host_limits=None)`、
`validate_request(value, *, host_limits=None)` 和
`validate_comparison_context(value, request, *, host_limits=None)` 传入同一可信策略。
现有 server 从 `ComparisonProvider.host_limits` 取得策略。这些是本地 HOST 选择，
没有新增请求字段、grant、模型设置或 Registry 写入。

## 验证边界

确定性 Python 测试使用 canonical owner issuer、真实 stored author producer、真实 checkpoint
publication、公共 session、权限与 cuts。Node 测试覆盖封闭 DTO 与取消；controlled-DOM 用例运行
实际双 NetRenderer 生命周期与真实 ELK 几何。以上不等同于浏览器绘制、无障碍、移动端、OS
隔离或恶意 HOST 安全审计。若执行环境阻止 browser/loopback，应在受支持的本地环境完成剩余
检查，不绕过限制。

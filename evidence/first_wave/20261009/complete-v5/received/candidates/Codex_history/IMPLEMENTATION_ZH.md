# Codex 固定 cut owner 历史：实现与交付结论

已完成最小本地增量，离线与独审通过，可进入单独 stock 0.155 本地原生 gate。
尚未安装/启动真实Codex、登录、调用模型/provider、运行Actions、push或改版本pin。

补丁：codex-owner-history-projection.patch
SHA256：df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b
范围：21文件，相对于“715468 main + native reader + effort codec”的组合base。
不是已合并主线，也不是独立从裸main可应用的补丁；前置包身份见HANDOFF.json。
两份冻结输入的全部7/12文件与patch均复核未变。

## 真实完成的行为

- 原有private local-owner会话准入复用；没有另造RegistryReadSession grant/catalog要求。
  initialize是协议门禁，不是身份认证。每请求读前与取得send锁后复核原lease/source，
  包括实际DB/object-store路径、inode、task/branch/thread、writer/read句柄、只读flags。
- MainThreadRegistry私有接缝将同cut committed membership和exact-body hydrate封在一起；
  MainSession原安全校验/renderer仅送原user text、reply、protocol_valid提示和cut上launch fact。
  private native_plugins/profile/task.prompt/stage或node instruction/graph/child正文不进wire。
- turns/list和items/list走native cut分页，支持defaults、uint32 limit clamp、双方向、
  turn filter、notLoaded/summary/full、next-exclusive/backwards-inclusive及稳定native ordinal ID。
  resume只捕一次cut，双初始cursor一致；空cut后追加不污染首次页。初始turn view明确unbound，
  continuation严格绑定view。state.turns不承担历史分页authority。
- 保留原resume live reconnect/tracking，纯history path不launch/persist/reconcile/read-child。
  整个组合resume不能因此被说成永远没有后台live变化。
- 仅Codex wire层把既有ses_稳定身份的同一128bits规范为UUID。官方Rust consumer真的会
  Uuid::parse_str，schema string不足以发现旧格式问题。generic frontend/Registry/root身份
  不改；旧sidecar显示缓存可重建，旧ses_请求/cursor拒绝，不当作授权别名。
- 0.155 pin不变；0.161新增object item anchor及可选字段只做source/schema对照。

## 独审发现并修复的实质问题

最初“只执行SELECT”的owner writable core路径确实打开了235个rw SQLite连接，一次组合
测试出现canonical DB字节变化与非空WAL清空，符合连接GC/关闭时checkpoint行为。
不能把它简单归为fixture噪声，也不能删物理不变断言。原始失败日志完整保留。

现history_registry复用同一物理source的既有_RegistryCore(create=False,read_only=True)，
实际构造不mkdir、不_initialize、不acquire_writer；capture/page/hydrate/source meta重核
都走mode=ro/query_only。没有第二DB/authority。测试禁止writer.connect，并核实际RO连接。
在fixture writer结束与readonly句柄就绪后建立物理窗口，DB/objects/非空WAL/profile/lease
字节、head/epoch均保持，-shm读标记明示排除。独审另保留活跃writer和非空WAL，确认append
可见且history不改authority。无immutable=1、产品GC/flush/checkpoint。

SQLite首次只读打开仍可能建立-shm/空-wal；不声称整个目录绝对零物理写。全局ObjectStore
dirfd/no-follow/race加固另案，本包不改它，也不承诺抵御同OS owner恶意ABA竞态。

另修了source binding漏核actual event_store.path和lease新增失败路径fd清理。
普通无filter页去掉一次不必要的整lineage预投影后，同小fixture测得178个只读连接、0rw，
作者一次0.195826秒，d92依赖overlay一次0.156370秒；独审约0.263秒。不是性能基准，
每页仍重复验证lineage，页大小不代表总CPU/RSS有界。

## 已执行验证，互不混称

1. 冻结715组合base：119 passed / 1 native AF_UNIX case deselected。
   final-tests.xml/log；含53新history、29既有Codex/codec、14native history、9session access、
   14frontend boundary。大于4MiB合法body不隐式截断；显式预算整请求失败。
2. 同冻结base额外MainSession/Registry回归：43 passed，和上119不重复。来自此前已选的
   31 MainSession + 12 MainThreadRegistry安全离线cases；其他9 native执行case未选。
3. 独立冻结审查：128 passed，source1041文件before/after相同；21额外对抗probe包括workflow
   私有字段、actual DB path、send-lock撤销、只读URI、活跃非空WAL。与作者回归有重叠，不能相加。
4. d92ff370两生产依赖文件task_control.py/run_authority.py，经其他worker逐Git blob核验后
   单独overlay：同119矩阵再次119 passed / 1 deselected。是依赖组合复测，不是完整d92 HEAD
   或全仓认证，也不是又增加119个unique cases。8dd360仅README勘误，不因它重复测试。
5. portable git apply --check/apply与最终21文件字节一致通过；独审整树apply replay也通过。
   py_compile通过，两个新双语reference页的官方docs parser定向检查通过；完整site build未跑。
6. native-gate/prepare_fixture.py纯Registry烟测2个committed+1pending，0model/child calls；
   run_native_gate.py仅静态编译，未启动。源码fixture是真Registry，child观察为fixture模拟，
   不能冒充真实worker/provider已执行。

初期失败与修复后的最终证据分目录保留。v4的manifest actual=set()来自测试加载后源码行号
改动导致inspect.getsource采样漂移，未当作通过；最终独审before/after hash保证冻结一致。

## 本地交接与停止条件

native-gate/NATIVE_GATE_ZH.md给出原生验证步骤：已有stock0.155、真实Unix socket/TUI、
60轮/120items冷恢复与翻页、再次打开稳定ID、pending reconnect、私有哨兵检查。
脚本拒绝执行RPC和TaskControl.start，记录无正文RPC/cut证据；无安装/登录/模型调用。
不放宽pin、不使用假binary/transport绕过。真实running-worker reconnect/terminal要另有
明确零模型fixture；本包只声称它的离线生命周期回归。环境缺条件则报告原始阻塞。

官方两版10份JSON schema、10份TS类型、0.155 Rust消费者/分页/UUID源码均逐blob核验，
保留upstream URL/SHA256和Apache-2.0 LICENSE/NOTICE。未编译TS/Rust；仅源码与JSON验证。


## 最终文档单行修正

产品/测试冻结后，docs parser发现新增中文reference的language应为zh-CN，已只修这一行。
原128独审对应旧patch c1d5665c4955b0c129cc7b0b89b456a55307597bd0febae0ea1f76290b089fcb；
最终df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b独立doc-only补查
确认其余1040文件完全相同、新patch apply整树一致、两页parser通过；没有重跑或虚称原
128在新全文hash执行。详见independent-review/DOC_ONLY_ADDENDUM_ZH.md。

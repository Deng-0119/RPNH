# 本地 stock Codex 0.155 原生验证交接

本包尚未执行 stock Codex/native AF_UNIX gate。云端只完成离线 Registry/JSON 与独审。
下面是本地后续验证步骤，不是已通过记录；不安装、不登录、不升级、不push、不运行Actions。
需要已有可运行的 codex-cli 0.155.0、项目现有 Python 环境（含既有 pytest/jsonschema/websockets）
以及允许 AF_UNIX/PTY 的本地环境。缺任意条件就停在明确环境阻塞，不换假binary或绕transport。

## 1. 先核依赖与代码，避免覆盖已有工作

在用户确认的 RPNH checkout/隔离 worktree 中检查 git status，保留用户改动。基础参考为
715468dab0b1bea07d7e94a7aa0606eaf194365c；新 d92ff3704b6002bf5ecbccb3e6a3d1489809a805
的两个生产依赖文件单独叠加验证过，8dd360e4848912a998dbd83220c3f0ce0a1caa86仅改证据README。
本包不是全量checkout，不能拿它覆盖任意目录。三个补丁依次检查/应用：

1. 已交付的 native-main-thread-history.patch，SHA256
   f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654
2. 已交付的 codex-effort-wire-codec.patch，SHA256
   3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d
3. 本包 codex-owner-history-projection.patch，SHA256
   df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b

每一步先 git apply --check 再 git apply，若已有相同改动则核文件hash，不能盲目重复应用。
第3包是相对于“main + 前两个overlay”的21文件增量，包含对前两个包文件的后续修改；
不能直接拿它应用到未铺依赖的main。file-manifest.json提供全部最终文件SHA256。
这里的source/仅打包21个变更文件用于审阅，不能独立作为完整Python产品运行。

当前支持pin仍0.155.0。不要安装0.161或为了测试改initialize/CLI版本检查。官方0.161
schema差异仅研究；JSON通过不等于Rust/TUI消费者通过。

## 2. 制作全新、零模型调用的fixture

脚本仅写新的目标目录，目录存在就拒绝。用现有环境Python替换 PYTHON，用实际路径替换
REPO、PACKAGE、OUT。它核21文件hash，导入项目现有pytest fixture helper，创建真正
MainThreadRegistry committed records，child receipt由离线fixture模拟；没有真实child执行。

PYTHON PACKAGE/native-gate/prepare_fixture.py --repo REPO --output OUT/cold --turns 60
PYTHON PACKAGE/native-gate/prepare_fixture.py --repo REPO --output OUT/pending --turns 6 --pending

60个committed turn是120个安全文本item，足以让stock的5-turn页和100-item页跨页。
fixture包含“PRIVATE_PLUGIN/PRIVATE_PROFILE/PRIVATE_PROMPT/PRIVATE_INSTRUCTION”合成哨兵；
它们必须不出现在UI正文。原用户text与reply应完整显示；第3条含原protocol_valid提示。
pending fixture在末尾加一条从未执行的pending-start main turn，不能被当作committed正文。
这些不是用户真实历史，勿用于生产会话，也不要把生成的SQLite/配置/objects加入git或回传包。

prepare_fixture.py已以2个committed turn完成纯Registry云端烟测；run_native_gate.py只做静态编译，未运行。

## 3. 用真实客户端验证 cold resume 与分页

在真实交互终端运行，不用CI假PTY替代：

PYTHON PACKAGE/native-gate/run_native_gate.py --repo REPO --fixture OUT/cold --codex EXISTING_CODEX_0155 --log OUT/cold-run-1.jsonl

run脚本使用产品本来的 run_codex_frontend、Unix socket和原始stock TUI。它仅额外做两件事：
记录无正文的RPC/cursor元数据；拒绝执行类RPC与所有TaskControl.start。模型/child不能启动。
不要输入新任务、不要尝试登录或切换profile。若安装版本不符或socket受限，保持原错误并停止。
脚本拒绝不在read allowlist中的RPC是测试护栏；记录对应动作，不把主动执行被拦当成历史缺陷。

手动核验并记录：

- TUI成功打开，不出现invalid thread id；thread.id为合法UUID，和既有ses_身份同一128bits。
- 确实发生 thread/resume -> thread/turns/list(desc,notLoaded,limit5) -> thread/items/list(desc)。
  日志必须有真实这两种请求；缺请求不能凭屏幕有缓存就称通过。
- resume双cursor的cut_ordinal/boundary_event一致。所有continuation沿同一cut；首锚inclusive，
  后续exclusive。nextCursor最终null，不反复出现同一个位置。
- 上滚加载较早历史，直到第001条；最终可见完整60轮/120items，无漏首条、重复末项、重排、
  重复terminal文本。若TUI启动时已读完，日志应已记录>=2个item页面，不需伪造滚动。
- PRIVATE_*哨兵、native_plugins、task prompt/instruction均不显示；普通中文/英文文本不吞掉。
- 正常退出，再以新日志 OUT/cold-run-2.jsonl 重新打开同一fixture，UUID与turn/item ID稳定，
  不加载伪造sidecar正文。旧ses_引用和旧ID cursor必须拒绝，不能作为授权别名。

日志包含公开IDs、页面counts、cut位置，不保存正文或完整cursor。必要截图应只截合成fixture。
真实可见内容核验不能仅由无正文日志代替。

## 4. 保留现有 active reconnect 生命周期

对OUT/pending以同样命令启动、正常退出、再启动（使用两个新log文件）：

- 既有pending-start turn仍是同一个native ordinal，TUI可见正确active状态；不把它显示为
  已completed的历史answer，也不补launch/reconcile，不因reconnect重复创建turn。
- 既有已committed 6轮仍按分页恢复，pending text不混入committed items。
- 日志无任何被成功执行的turn/start或child launch，Registry保持pending，零model调用。

此步骤是“无执行pending reconnect”原生门槛。真实正在运行foreground进程的重连、真实
worker完成后terminal通知，不因这个fixture自动得到认证；本包只对这些生命周期保留了
离线真实Registry+live-control-double回归，以及一次terminal通知/cold ID对齐回归。
若要补真实running-worker门槛，需另定一个明确的零模型worker fixture与授权，不能偷偷
打开provider或用模拟child观察冒充真实worker已执行。不要为拿到绿灯放松零model约束。

## 5. 回传证据与判定

保留：git HEAD/dirty摘要、三个patch与21文件hash、已有codex --version输出、Python版本、
两个fixture的gate.json、cold/pending四个JSONL、UI核验清单/必要截图、失败时原始错误。
不要回传完整生成Registry、私人profile、凭据或真实用户正文。

判定应分开：

- 离线产品/原生Registry：本包作者119+43不重复用例；独审128；d92依赖overlay同119矩阵。
- 真stock0.155 cold resume/paging：本地完成上述请求与UI证据后才能记通过。
- pending reconnect：单列结果，不等于真实running-worker认证。
- 0.161 native：未运行，pin不变。

SQLite readonly首次打开可能建立-shm/空-wal；authority DB/objects/非空WAL与head/epoch
观察要排除先前writer连接结束时的checkpoint。不要在history handler中gc/flush/checkpoint，
不要用immutable=1冻结掉真实append。具体边界与已修初始失败见IMPLEMENTATION_ZH.md和独审报告。

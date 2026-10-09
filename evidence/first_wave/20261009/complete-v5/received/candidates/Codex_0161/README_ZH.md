# Codex 0.161 最小协议候选

结果：最小 adapter 候选已实现，云端定向验证通过；0.161 原生客户端认证未做，默认精确 0.155 不变。没有安装、登录、native/socket执行、模型/API调用、Registry身份/权限变更、push、Actions或发布。

## 实质增量

- 既有兼容 manifest 内增加显式 `candidate-0.161.0`，普通 CLI 不自动选择；未知 selector/version 拒绝。resolver、launcher、initialize 与 thread.cliVersion 都绑定同一个不可变精确 profile。
- 0.161 items object anchor 严格要求非空有界 turnId/itemId，按同一次新 cut 中 committed exact turn 的现有 0/1 safe slot 定位，复用原 exclusive native anchor。后续仍是原 v1 string cursor；无新 Registry/store/reader。
- 两个safe slots意味着object排除起点后最多返回一个item、nextCursor恒null；非空页提供原reverse/inclusive string cursor，不为测试造第三slot。
- 新版history时间戳、resume字段使用真实null/[]，不捏造时间、collaboration或plugin事实。旧版wire保持。

## 精确基线与补丁

`BASE_SOURCE_LOCK.json` 锁1041文件的继承组合：715468d source subset + Reader f4726d7c + effort codec 3be0de65 + owner history最终df0c3090 + d92产品overlay（task_control.py b8658b6c…、run_authority.py 7475c2d5…）。8dd只作已读回的文档HEAD，不把subset结果称完整8dd认证。

`codex-0161-candidate.patch` 只含6个本次增量文件：3个产品、1个新增测试、2份双语文档。它不重复包含Reader/codec/history/H2改动。最终完整合成source有1044文件，`file-manifest.json`列逐文件hash；`baseline/`只放补丁实际改动的三个旧文件供净应用检查。

补丁SHA256：720bcf2bd95886a26425e00caec80eab0fbcf0eec0481202ca54ce88a5f8a3c8

source fingerprint：316a06aad41653e890d16a19519d07b09042c54c4961586b54cf2842b10d8658

消费端可运行 `python verify_package.py` 做字节与6文件净应用检查。作者用 `freeze_package.py` 冻结的完整净合成base已做git apply --check/apply并核1044文件一致；freeze脚本依赖作者workspace的冻结前置包，不是消费端安装入口。

## 已验证与未验证

`reports/VERIFICATION.json` 与JUnit逐项对应：

- 122项组合history/profile：69项新增candidate + 53项既有history边界，全通过
- 8项既有默认handshake、pin、manifest、argv与effort wire定向回归，全通过
- 16项native交接runner/logger/analyzer纯fake/合成日志测试，全通过
- 独立复核另有34项最终d92组合测试，覆盖有所重叠，不能相加成新的作者总数
- 首轮69项旧组合结果单独保留在 reports/pre-d92，之后已在最终组合复跑，不计入最终通过项

没有Rust编译、真实stock TUI、真实native WebSocket、真实running worker或provider证明。当前执行环境默认Python缺pytest，测试使用已有可用venv；没有为此安装依赖。

## 设计修复与交接

`design/`保留前序材料原文，并补齐此前实际缺失的20项 `COMPATIBILITY_MATRIX.json`。31项冻结输入复核一致；56份官方源文件均核字节/SHA256/Git blob，其中3份前序孤立文件已按官方精确tag重新补核。详见 `design/COMPLETION_ZH.md`，其中对AbsolutePathBuf的base-guard条件作精确补充。

`native-gate/NATIVE_GATE_ZH.md` 给出一轮准备、同canonical cold/pending roots、新旧binary顺序首次/重开，以及独立object RPC probe。所有脚本在本轮仅编译或纯fake测试，尚未native运行。缺binary只标对应lane BLOCKED，不下载/换版；任何unexpected RPC/error/child尝试使lane非零停止。实际limit、cut、cursor推进和完整ID覆盖均验，0.161初包不强迫100项。日志完整也只标需UI复核，不自动宣布认证完成。

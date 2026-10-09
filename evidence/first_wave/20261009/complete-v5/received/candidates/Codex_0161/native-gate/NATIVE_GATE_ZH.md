# 新旧 Codex 同 root 顺序原生 gate

状态：未运行 native。此包只做源码/JSON/Registry与纯 fake runner/log 测试；真实 0.155/0.161 TUI、Unix WebSocket probe、Rust 编译、模型均未运行。候选 profile 不表示原生认证完成。缺少独立授权的本地环境或指定 binary 时，各 lane 保持 BLOCKED/NOT_RUN；不安装、降级、替换 binary，不登录、不绕 transport/PTY 权限。

## 1. 先核组合，准备一次

实施源是 715468d source subset + Reader f4726d7c + effort codec 3be0de65 + owner history df0c3090 + d92 两份产品 overlay，再应用本候选补丁。8dd 是后续文档勘误，不是完整 HEAD 重认证。`file-manifest.json` 锁实际 source；`BASE_SOURCE_LOCK.json` 锁候选前的精确组合；patch 只含本次3个产品文件、1个测试文件、2份双语文档，不重复 Reader/codec/history/H2 patch。

在已明确授权的本地 task 中，用既有依赖环境准备一次：

    python native-gate/prepare_pair.py --repo /absolute/composed/repo --output /absolute/new/synthetic-pair

会建立同一路径下 cold（60 committed turns /120 safe items）与 pending（6 committed +1 unexecuted pending）。两版与重开复用这些 canonical roots，不复制/移动后再要求UUID相同。此 fixture 使用现有 test helper 产生合成 Registry evidence，不运行任何 worker/provider。只在合成 fixture 上运行，不指向用户真实会话。

## 2. 逐版运行，不并发占 owner

    python native-gate/run_native_pair.py --repo /absolute/composed/repo --fixtures /absolute/new/synthetic-pair --output /absolute/new/evidence --codex-0155 /existing/0155/codex --codex-0161 /existing/0161/codex

两个binary必须是显式给出的既有文件。每版分别核 exact `codex-cli 0.155.0` / `codex-cli 0.161.0`，记录realpath、SHA256、CLI版本、viewport；缺参数或文件/版本不符记该lane BLOCKED，另一版可继续。顺序是每版 cold首次、cold重开、pending首次、pending重开。每次TUI退出后才进行下一次，运行期间向最旧历史滚动，核001原文、最后一条与全部条目，记录UI截图/人工观察但不把正文写进JSONL。

runner只允许既有只读RPC，拒绝执行/设置/unknown RPC，并封禁 `TaskControl.start`。任一RPC error、写操作/child尝试使gate返回非零，即使TUI正常退出0也会停止对应lane。出现登录/权限/模型需求，立即退出当前TUI并保留原错误，不能通过安装、修改profile或换transport绕过。不能自动增加allowlist。

## 3. 正确验收动态分页

对每份JSONL运行：

    python native-gate/analyze_log.py /absolute/new/evidence/0.161.0-cold-first.jsonl

分析器检查：单次resume；初turn为desc/notLoaded/limit5；每次limit1..100且返回数量不超limit；request token按每个query链消费前次next；request/response同resume cut；next有推进且最终耗尽；item/turn关联、预期ID覆盖和跨item页无重复。turn metadata链可与items链交错，不强迫补页limit5；初item页可受viewport影响，不强迫100。

即使输出 `RPC_COVERAGE_COMPLETE_UI_REVIEW_REQUIRED`，仍须人工核原文显示、001可见、private sentinel未显示、重复/截断不存在、pending未执行且重开后保持active、两版同root ID相同。分析器永不直接输出stock认证通过。多次resume须拆分证据链；当前单链分析器会将混合日志记INCOMPLETE。

## 4. Object单独标识为RPC probe

stock已审cold/scroll链不发object。额外probe独立执行：

    python native-gate/run_object_rpc_probe.py --repo /absolute/composed/repo --fixture /absolute/new/synthetic-pair/cold --output /absolute/new/object-rpc.json

该脚本用真实Unix WebSocket，但不启动stockbinary；先candidate再pinned，核object exclusive、缺turnId拒绝、最多1个剩余safe item、string反向inclusive及同root跨profile重验。它不是stock请求证据。当前包仅编译此脚本，未运行，遇transport被拒就停止，不提权/切换假transport。

## 5. 日志与不可混淆事项

只记录版本、code hash、方法、limit/view、public IDs、cut event/ordinal、token hash和计数/error code；不记录正文、完整cursor、profile私有内容或凭据。object日志只记录形状布尔，不回显未验证itemId或任意额外字段。两个safe slots意味着object anchor后next恒null，不能伪造第三slot证明分页；已有thread-wide string分页另验。

SQLite首个RO连接可能创建空WAL/SHM协调文件，不能误报为authority写入。字节观察窗口须在RO建立后开始；authority DB、objects、非空WAL/head/epoch与owner关闭checkpoint分开记录。不得用immutable=1忽略append、在history handler中checkpoint/GC、或承诺抵抗同OS owner恶意ABA。

所有新脚本是独立候选交接，已交旧runner/文件锁保持不动。最终版本支持、发布/合并/全局安装均未由本候选授权。

# 独立审阅结论

**结论：PASS，限于 L2（真实 Registry/Harness/owner dispatch，显式测试替换 wake transport）。L3 原生 AF_UNIX 运行验收仍待本地补验。未发现阻止当前代码包交付的产品问题。**

审阅日期：2026-10-08。最终源集合 SHA-256：`a547de500345f51c102fb197dfabedafc2aa48c251dc19d9e10733cbe2553618`。已独立逐项校验 manifest 的 16 个文件长度、SHA-256 和集合哈希。基线 main 为 `00f2d29c7deffed44e2ec635a24f390c6e0d9ace`；本次 core 无变更。

## 已确认的实现事实

- 10 个真实 transition，18 个 place。执行由采用后的 typed PN、当前 Registry marking 和既有 Harness admission 决定；没有另一个 Python ready queue、流程游标或 checkpoint 数据库。
- 每个 operation 只调用其精确注册的一个原子工具。现有 gateway 校验工具 key、允许列表、identity/contracts；工具只收到该 occurrence 已交付的纯输入数据与规则，没有 owner、kernel、gateway 或 writer context。
- async binding 是本 example 对通用 HOST callable 返回对象的受信用法：owner 校验后构造一个 coroutine，由同一已准入 worker await。它不是 core 新增的通用异步工具 API，不提供 managed-tool invocation receipts 或独立异步恢复能力。
- read 步骤消费已经交付的 Registry 输入，明确产生 raw 和 validation-source 两个产品。它们没有新发起外部文件/网络读取；本例并行证据不代表外部 I/O 加速。
- 12 个中间/最终产品均有 Registry exact resource/version、实际 input claims、producer、output binding、schema authority 和 operation result。父引用来自实际交付输入，来源字段不能代替这些权威关联。
- 计算使用 Decimal；结果验证从独立原始整数资料使用整数乘除与 HALF_UP oracle 重算，不调用 compute 的金额 helper。标准结果为 2.000 kWh、1.70 CNY；两行各半分的结果为 0.02 CNY。
- 业务无效输入/候选金额在显式 rejection outcome 停止，publish 没有 admitted。双分支失败保留两份 rejection，返回 quiescent；没有伪装 failed terminal，也没有把 Harness 的 terminal-present 布尔当业务成功。

## 执行与证据分层

1. 作者完整 focused suite：22 个独立用例通过，0 failed / error / skipped，444.670 秒。后续只改测试断言失败时的 cleanup，相关 9 个用例重新通过，105.186 秒；9 项与 22 项重叠，不能相加成 31。
   - `evidence/final-author-junit.xml`
   - `evidence/final-cleanup-recheck-junit.xml`
   - 两次运行的 source manifest 和关系见 `provenance/tested-source-manifest.json`。
2. 独立最终 HOST 边界探针通过，见 `review/final-boundary-results.json`：
   - 伪造 parents 在 publication 前被拒绝。
   - 同 Registry 中未 claimed 的 sibling 输入读取触发 `UnauthorizedResourceDelivery`。
   - 重建 Harness 后未决 read 仍只调用 1 次，不自动重放；正常 sibling 收敛后返回 `reconciliation_required`，无 terminal，模型计数 `[0,0]`。
   - 此结果记录最终 host/tools/contracts/module 的精确哈希。
3. 最终并行证据已独立交叉核验，见 `review/final-parallel-evidence-audit.json`：两个 read body 在不同 worker 进入；ordinal 389 时两个 firing 同时 active。usage normalize 在 530 settled，542 时该资源已落 Registry，而 tariff read 到 556 才 settled，join 到 698 才 admitted。全程最大 active 为 2，最终 1.70、0 模型。
   - 此审计明确针对 22-case `final-author-tests` 的受控运行，不混指后面的 cleanup recheck。JSON 已附所审 observations/evidence 的相对路径和文件 SHA-256。
4. 独立探索性长探针的 14 个完成场景由原 Registry 只读重建，见 `review/independent-results.json`。包含标准答案与 12 产品 lineage、11 个输入/候选反例、半分舍入及一个修正前 parents 缺陷复现。它在 HOST 加 guard 前已 import；该缺陷由第 2 项最终源复验确认修复。最后自写 30 秒调试 barrier 因测试超时/cleanup 不健壮而取消（退出 130），明确记为 inconclusive，不能把整段探索脚本计作通过，也不据此认定产品失败。
5. 原生 transport：独立小探针及原生 CLI 都真实遭遇 `PermissionError: [Errno 1] Operation not permitted`。未暗中 fallback。pipe 测试只替换 wake FD/socket setup/close，继承原 owner dispatch、gateway、completion dispatch，未 mock admission、Start、Success、资源发布或 terminal。

## 本地必须补验的门槛

在允许 AF_UNIX/socketpair 的 Linux 或 WSL2 环境，使用最终源和全新输出路径运行：

```sh
python -m pytest -q examples/tool_pipeline/tests
python -m examples.tool_pipeline.run \
  --run-dir /tmp/rpnh-tool-pipeline-native-run \
  --output-dir /tmp/rpnh-tool-pipeline-native-export
```

不要传 `--tool-pipeline-transport=pipe`。要求默认原生 suite 的 22 项通过；CLI 返回 PASS/退出 0；保存报告、exact terminal、12 产品和零模型计数；确认默认 socket 生命周期以及受控并行/未决恢复用例真实通过。已有路径需改成新的唯一名字，不要求删用户数据。

## 未覆盖，不能扩张的结论

- 未实现任意 Python 自动转换、父工具内嵌套 typed execution-net bridge、managed plugin subprocess 验收或外部副作用工具。
- 只证明本例未决 operation 不自行重放；没有验证外部 write 的 outcome_unknown、一般跨 run/同 key 重放全矩阵、多个 active firing 的通用恢复、durable completion 丢失后的完整 owner 重建恢复。
- Registry API 约束下的资源版本不可变依赖受信存储。当前存储读取有 envelope/长度校验；截短反例不等于任意同长度篡改检测，不承诺对恶意文件系统的完整性防护。
- native AF_UNIX 阻断是仍待补验项。L2 通过不能替代 L3。未调用模型，未启动 Actions，未 push。

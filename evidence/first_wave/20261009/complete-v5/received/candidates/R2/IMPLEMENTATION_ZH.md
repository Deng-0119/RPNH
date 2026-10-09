# R2 有限单 Registry 运行验证

## 结论和边界

5个新增example-local文件，现有product文件零修改；原Registration/compiler/Module/
Registry/PN admission均直接复用。最终作者验收：R2 15/15 passed（11纯step/编译，
4原Orchestrator配显式test-only pipe），R1后继＋原compiler兼容150/150 passed。
native单项实际1failed：OwnerEventLoop创建AF_UNIX时EPERM；归类partial_env，未进入
HOST执行，不重试不绕过。不能声称完整R2 authoring、native R3或模型改善已验收。

R2补丁SHA-256：5054c53cc61bb03fdc58a1da6f7e56b41decc0ccb78124478effc38b2bf93e52。
未上传交付、push、创建PR、触发Actions、登录、安装、调用模型或外部服务。

## 精确源码身份

固定被测相关main为715468dab0b1bea07d7e94a7aa0606eaf194365c，基于已核ec9077e源
加H1原两cpn修改。998文件逐一重核remote Git blob；另原pipe fixture Git blob
910687770c31a4e8ae11009c6352206dd67de94a。这是focused materialization，非完整clone。
source-provenance.json、tested-source-inventory.json列出实际全部1010个非cache文件；
不会把未materialize的RRSI文件谎报为已逐字核验。补丁只增加examples/rsi_workflows五文件，
不修改冻结RRSI协议、方法、scorer或campaign。

依赖R1后继交付v2的六文件，见r1-successor-overlay.json。完整patch SHA-256：
67466bfc13a685dd79dc26d0c0c2f0a33b7016553ccf53728b57906cc2f10ac3；从旧R1修订的
delta SHA-256：40da4de0316e5fcb07276fcc159c7277403eef942df4769ad361e5f40804e72d。
已与R1最终manifest六文件逐字核对，旧cad3873f冻结包完全未改。

冻结后观察main前进d92ff370（H2a），不回写本测试基底。h2a-head-drift.json明确差异；
两个实际cpn文件仅另置head-d92ff370-overlay-check，用于小范围只读组合检查，非完整新HEAD
checkout/套件。最新本地集成仍按实际HEAD运行LOCAL_GATE_ZH.md。

该有限组合检查已完成：针对complete/failed/running三个原node ID仅补跑3pass到固定
basetemp，分别用715源与两个d92 cpn文件overlay只读回读相同Registry，结果和TaskControl
status完全相同；canonical DB/immutable objects/非空WAL、head和writer epoch不变。
这是3个重复case的定向补证，不加入作者165项，也不是d92完整HEADsuite。
见h2a-readback-comparison.json、h2a-targeted.log/xml。

必须区分SQLite bookkeeping：最初全目录字节不变探针在base和overlay均失败并保留日志；
定位为SQLite只读连接可以创建32KiB SHM及0-byte WAL。修订探针明确记录这些侧文件变化，
严格要求canonical DB、immutable objects及任何非空WAL保持不变，没有改产品、跳过WAL
或使用immutable=1。不能把mode=ro描述成整个目录物理零写。首次旧临时fixture已被pytest
清理的FileNotFound尝试也保留，未当作语义失败或执行通过。

## 唯一执行权威

纯D0直接调用既有start_run → RunOwner.admit/start/products/succeed。step helper是单个
命名firing的测试探针，不决定ready、重试或下一轮；不允许的firing由原admit返回None。
端到端直接用Orchestrator.run → Harness.exact_execute；同步Future只是纯HOST提交服务。
角色函数只读本次claimed inputs、返回schema-valid bytes；没有Registry句柄、网络、
文件I/O、model client、外部cursor/best状态、新budget或runner。

candidate→evaluation→selection与next→后轮state均用实际resource refs。原PN要求本轮
evaluation token；report存在、候选高分字符串或其他轮已注册evaluation都不能替代。
selector业务检查拒绝错candidate、父state、round。evaluation附exact request（包含
synthetic dataset）及scorer operation-binding refs；这仍是可信pure HOST条件，非针对
恶意HOST的一般证明。

示例score是整数绝对误差，独立于RRSI。domain UNKNOWN空分数只retain有效incumbent，
不晋升。missing、仅products未settled或exception没有正常selection/terminal。
正常stop与owner interruption不同；末轮select/retain仅表示有限过程结束。所有framework
outcomes由profile显式terminal_outcomes选择complete|failed，不在R2补config，failed
有回归证明不被重标成功。物理outcome_unknown持久记录/传输丢失未在本包验证。

cap=1与rounds独立，4轮12个operation正常完成而原actual_model_call_counts为(0,0)
（returned total、post-limit excess）。零model测试只证明此处不误把round/dispatch计账；
不证明registered-model费用/物理attempt上限或完整provider覆盖。

## 发现并纠正的R1缺陷

最初原R1 config={}，原module_terminal.py要求run_outcome，导致已settled PN仍running。
真实无socket负例已在step-first.xml通过。最初设想由HOST补config，复核发现R1公共契约
并未声明该义务，因此否决为最终方案。原作者制作独立后继修订，新增必填三项映射。
R2最终源码不再有bind_completion，直接消费修正后的compiler产物。

早期source与log在early-derived-fixture及step-first.log/xml保留：6pass/4fail。
4fail来自测试helper误把VerifiedResourceArtifact当有payload的carrier；已改原
object_store.read_registered exact读取。没有通过改core、放宽保护或隐藏旧失败取得通过。

## 复核证据

- runtime-final.log/xml和start/end.json：15passed，445.11秒；JUnit内保留实际谱系refs、
  terminal refs、transport及operation/model-call计数。其returned_model_calls数组顺序为
  原API的[returned_total,post_limit_excess]，不是两类physical调用。
- compiler-compat.log/xml：150passed，其中141纯compiler/schema、9真实Registry step；
  这是依赖回归，不把重复运行叠加为新增case。
- native-attempt.log/xml和start/end.json：1failed，AF_UNIX EPERM；其余native参数未运行。
- file-manifest.json：5文件SHA；patch-apply.json：独立fresh-materialization apply-check/
  apply及逐字一致。不是git commit或完整git working tree status。
- 独审重复11纯step＋4pipe另计，新增5个边界探针单独报告，不混入作者165项。

后续：在支持AF_UNIX的Linux/WSL2上跑默认四项native；registered-model/scripted port、
owner-stop/resume、物理未知结果、author/CAS、parent-child与跨child预算按原门槛单独验证。

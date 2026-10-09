# R2 本地零模型验收与证据回传任务书

## 目标与输入

在用户已有授权且支持AF_UNIX的正常Linux/WSL2环境验证R2五文件补丁。依赖已交付R1v2，
不能沿用旧R1的空terminal config，也不能在runtime补config绕过编译器。

- R2 patch SHA256：5054c53cc61bb03fdc58a1da6f7e56b41decc0ccb78124478effc38b2bf93e52。
- R1v2 full patch：67466bfc13a685dd79dc26d0c0c2f0a33b7016553ccf53728b57906cc2f10ac3。
- 若工作区已应用旧R1，后继delta为40da4de0316e5fcb07276fcc159c7277403eef942df4769ad361e5f40804e72d。
  full与delta是替代路径，不能同时apply。已经修订的工作区不重复apply。
- 作者主测试base715468d＋明确R1v2；后来d92ff370(H2a)仅做独立小范围reader组合检查。
  本地记录实际HEAD和全部existing diff，不继承作者的base标签。

## 安全与停止线

仅使用现有Python测试环境；不安装、不登录、不调用模型/API、不跑真实campaign、不触发
Actions、不自动push/merge。遇socket、权限、ownership、安全守卫失败，保留原始失败，
不要换权限、放宽guard、改socket实现或用pipe结果冒称native。保留用户已有改动。
如果路径重叠或R1/R2 hashes不符，先报告具体差异，不强行覆盖。

## 验收步骤

1. 记录UTC开始、repo/HEAD、git status --short、已有diff/untracked、Python可执行文件/
   版本、pytest/jsonschema及cpn/iteration_profile实际import路径。核对root AGENTS.md。
2. 确认R1v2与file-manifest五R2文件一致；仅在未应用时git apply --check后apply。
3. 运行原依赖与无socket集，输出原stdout/stderr和合法JUnit：

```bash
python -m pytest -q tests/test_iteration_profile.py tests/test_compiler_json_contract.py -o junit_family=xunit1 --junitxml="$OUT/r1-compiler.xml"
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k 'not original_orchestrator' -o junit_family=xunit1 --basetemp="$OUT/rsi-step-runs" --junitxml="$OUT/rsi-step.xml"
```

4. 用默认真实OwnerEventLoop跑完整四项；不得传--rsi-transport=pipe：

```bash
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k original_orchestrator -o junit_family=xunit1 --basetemp="$OUT/rsi-native-runs" --junitxml="$OUT/rsi-native.xml"
```

5. 如需重現作者D0 pipe结果，单列日志/XML及D0标签，不能覆盖native窗口。作者已有
   AF_UNIX EPERM日志应保留在历史证据中，新通过不倒写旧窗口。
6. 对固定basetemp生成的Registry进行只读reader/status复核，可使用本包h2a_readback.py
   （在actual repo中，PYTHONPATH指向该repo，传固定basetemp与输出JSON）。此脚本只读，
   不创建owner、writer或调用operation。先核其来源和import路径，不混到其他安装态。

## 必须回传的事实

- 原生transport确为OwnerEventLoop/AF_UNIX，四项各自verdict、process exit和UTC窗口；
  1/2/4轮分别3/6/12次operation；正常stop仅3次。不得把fake模型等同真实provider。
- 缺本轮evaluation、已products未settled均不启用selector；wrong candidate/父state/round
  不产生选择；修改/删除report不改变PN准入。返回确切失败/未执行项，不仅总数。
- candidate/evaluation/selection、跨轮state和terminal资源的精确refs；JUnit中的
  exact_lineage/runtime_evidence可作为轻量证据，不回传凭据或全部原始Registry DB。
- domain UNKNOWN正常retain有效incumbent；角色异常、未settled不终态。explicit failed
  回读仍failed；正常协议stop与owner interruption分清。原R1v2另有owner-stop回归。
- reader使用同一个cut，输出后assert_unchanged；检查读前后event head、checkpoint、
  writer epoch及canonical DB/immutable objects/非空WAL不变。SQLite只读连接可能创建
  SHM/空WAL，应单独记录，不能称目录物理零写；不得用immutable=1绕过WAL核验。
  若authority并发变化，应拒绝旧结果，不补写终态。
- 原actual_model_call_counts为(0,0)，cap1不解释成一个operation。无额外模型/网络调用。
- 最终源码SHA、补丁apply结果、实际HEAD与既有diff、环境import证据、独立审查报告。

## 不宣称通过的范围

本任务不覆盖真实provider、registered-model scripted port、训练、正式physical
outcome_unknown记录、owner-stop/resume的完整竞态、author/CAS、跨child预算或RRSI
campaign PN迁移。它只补本例真实AF_UNIX及相应只读结果闭包。若后续获授权提交推送，
另核远端exact commit和相关checks；本任务书本身不新增发布授权。

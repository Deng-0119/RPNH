# 本地移交 gate：只读原生历史前置包

## 输入与限制

目标仓库仅 Deng-0119/RPNH。包不含完整runtime或依赖；先在获准的独立worktree核
origin/main与实际HEAD。起始baseline ec9077，最新已核715468；本包三个既有目标文件
blobs在两者相同。新的main若改到目标或相关依赖，须重核，不强行覆盖。

七文件patch已独审；SHA256见file-manifest.json、SHA256SUMS。包内source/只含七个
候选文件，baseline/只含三个被修改文件的原文，供apply/review，不要当完整代码树运行。

本gate不运行stock Codex、不调用模型/provider、不安装软件、不登录、不启用Actions、
不写remote、不执行child工作流、不变更reader grant/catalog。不得用真实用户Registry做
fixture。依赖不齐应准确报告，不自行安装或改测验断言。原有9个native执行/socket测试
属于另一gate，本包不把它们算PASS。

## 核与应用

1. 解压后用SHA256SUMS逐项核包内原文。file-manifest.json列七个候选source的SHA256及
   三个既有目标的baseline Git blob。应用前按git hash-object核这三个基线，新增四文件
   应不存在；不覆盖当地未合改动。
2. 在获准隔离worktree执行git apply --check后才git apply本patch。再次逐文件SHA256核
   与manifest一致。不要把latest-main overlay、H1/H2/codec或旧evidence当本包patch。
3. 使用该环境已有Python及pytest/jsonschema依赖，从任意目录执行：

```sh
"$PYTHON" "$PACKAGE/run_local_gate.py" \
  --repo "$WORKTREE" \
  --junitxml "$OUTPUT/history-native.xml"
```

runner先核全部七个已应用文件，再切工作目录；Python audit hook禁止socket connect/bind、
subprocess和shell启动。执行14新history + 12既有Registry + 6独立probes，预期32 passed。
跑完再核源码字节，JUnit必须可解析。OUTPUT需为已允许可写位置，勿混入用户Registry。

4. 保留实际HEAD、Python版本、命令、exit、32case XML、首错与源码hash。不得将包内云端
  结果冒充本地结果。可补py_compile三Python文件；完整docs build未在本包验证。
5. 本地结果若与预期不同，先诊断具体diff/环境/测试失败，停止依赖该结果的发布或adapter
  推进。不得为“通过”删掉身份、provisional、body界限、私有字段或read-only断言。

## 验收与后续

接受条件为七文件范围/hash准确、apply无冲突、32case全过、无外部执行、无真实provider、
固定cut无遗漏重复且source/thread隔离保持。测试fixture写入只在setup，read阶段不得写
event/counter/lease/profile。SQLite -shm读标记不是持久化Registry authority。

这只关闭native refs-only固定切面前置gate。后续公开renderer/分页RPC的准确API与权限
缺口见API_HANDOFF_ZH.md及ADAPTER_NEXT_ZH.md；不能因此声明Codex历史分页已可用。

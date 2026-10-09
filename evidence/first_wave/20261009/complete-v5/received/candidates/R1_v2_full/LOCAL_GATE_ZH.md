# v2 本地验证：先选择补丁，再做分层验收

先读 REPLACEMENT_NOTICE_ZH.md、根AGENTS，确认 Deng-0119/RPNH 工作区与HEAD，保留本地改动。当前验证基线715468d。若HEAD再前进，先核新增路径与原Module/Registry接口差异。

## 二选一

- 无旧R1：PATCH_FILE指向 inert-iteration-profile-v2-full.patch。
- 已有旧R1：确认六文件匹配file-manifest.json中的superseded_sha256，再将PATCH_FILE指向 r1-v1-to-v2-terminal.delta.patch。

禁止同时应用两个补丁，禁止对hash不符的文件强行覆盖。

```bash
git status --short
git rev-parse HEAD
git apply --check "$PATCH_FILE"
git apply "$PATCH_FILE"
python -m pytest -q tests/test_iteration_profile.py tests/test_compiler_json_contract.py --junitxml=/tmp/rpnh-r1-v2.xml
python -m py_compile cpn/rpnh/iteration_profile.py tests/test_iteration_profile.py
```

在已有受支持Python 3.11+测试环境执行；依赖缺失如实记录。本门槛不需要socket/provider/网络，不得放宽安全守卫。与旧R1的纯编译集不同，v2测试中9项显式建立临时真实SQLite Registry，使用原RunOwner步骤验证terminal发布；它们不调用业务HOST、不启模型或外部服务。

逐个核对六source文件最终SHA-256。确认三种映射的complete和failed都实际生成对应原生终态、缺mapping/未知值拒绝、原空config不能终态、未settle不终态、owner_stop不变complete。完整最终案例计数与分层见test-results.json；独审与重复窗口不可累加。

这不是Actions/push/模型/训练授权。R2端到端、原生transport、实际physical outcome_unknown及恢复、author CAS与parent-child仍各有自己的验证门槛。

封包时main已前进到d92ff37（H2a，改动原run_authority.py reader）。本包150项仍是715468d上实测；最新head不是同一测试身份。六个新增路径无冲突，但请在实际d92ff37或后继HEAD上执行本节gate并记录新身份，不借本包结果省略该步。

---
name: rpnh-result-erp-runtime-20261008
description: "ERP 合成原生 runtime 验证"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

[English](README.md) | 中文

# ERP 合成原生 runtime 验证

本历史验证使用合成 backend、真实 worker 与 AF_UNIX，没有真实 provider、Odoo world 或原始 ERP grader。[完整有限状态投影](results.json)标识基点 `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0` 上的五文件 ERP overlay 和独立 C fixture；发布于 `e92b05c9afe324ebb675f2d67b73c02efe7b9536` 不代表新的 benchmark 运行，core 不变。

| 范围 | 原本地结果 |
|---|---|
| 离线 | 65 个唯一用例通过；fail/skip 为 0；另有 4 个 subtest |
| 历史 AF_UNIX 阻断子集 | 15 项后来在本地通过，已含于 65，不累加 |
| B 安装态 complete | PASS，有 terminal，4 次脚本提交，进程 exit 0 |
| B 安装态 stop | PASS，静止无 terminal，3 次脚本提交，进程 exit 2 |
| B 安装态 timeout | PASS，静止无 terminal，3 次脚本提交，进程 exit 2 |
| C 明确 unknown / backend 异常 / completed 回复丢失 | 三场景 PASS；各一次派发、请求与合成 backend 影响 |
| C 已知非零失败 / 领域不可行 / 完成 | 三场景 PASS；各两次派发、请求与合成 backend 影响 |

C 合计 9 次 worker 派发、9 次 bridge 请求、9 次合成 backend 调用。三个 unknown 场景产生 `started -> outcome_unknown`；原 service 与重建 service 上 18 个同 ID/变参/新调用探针未增加回执或派发。已知结果支持缓存回读、拒绝 ID/内容冲突，并允许合法新 ID。Service 重建沿用同一 owner/Registry，不是 OS owner 崩溃恢复或 ERP 恰好一次事务。未运行 unknown 变体完整列于投影。

输入是合成生命周期/回执场景，不是 ERP 业务题；没有业务 scorer，按明确预期核对回执/准入状态。B 安装版本为 RPNH 0.1.0rc2、plugin 0.1.0、Harbor 0.24.0。C 原命令的参数化版本如下：PYTHON 是所选安装解释器，SOURCE 是历史 overlay，PATCH_PAYLOAD 为冻结五文件参考，OUTPUT 为新目录，TMP 是短的原生临时目录。

```sh
env -u PYTHONPATH TMPDIR="$TMP" "$PYTHON" -B   "$SOURCE/examples/erp_bench/scripts/unknown_native_acceptance.py"   --output "$OUTPUT" --patch-payload "$PATCH_PAYLOAD"
```

冻结外部参考须另行提供，身份列于 `results.json`，本页不重新分发。CLI 生命周期与离线准备见 [ERP 指南](../../../examples/erp_bench/README_ZH.md)。此命令不是本次新执行的检查。历史 AF_UNIX 权限失败保持原状态，后续本地通过不改写旧窗口；这些有限检查不认证 V6 组合产品。


[分发字节清单](MANIFEST.json)记录本摘要与投影的新字节身份，不能当作原始材料字节。

---
name: rpnh-github-oauth-account
description: 通过可选交互式HOST使用明确选择的已有GitHub OAuth账户。
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh
  counterpart: github-oauth-account.md
  revision: "2026-11-18.1"
  status: source-candidate-not-published
---

本次发行候选保留既有Linux/Viewer修复，核心候选为 `0.1.0rc5`，可选HOST为 `0.0.8`，实现revision仍为 `oauth-candidate-2`。从 `packaging/github-oauth-host` 可独立构建HOST。下方0.0.1为限定真实账户调用证据，0.0.2仅为离线/PTY证据，不代表0.0.8已完成新的真实账户测试；配套制品是否已提供请查对应GitHub Release。

[English](github-oauth-account.md) | [中文](github-oauth-account_ZH.md)

# 使用已有GitHub OAuth账户

此源码候选增加明确的`github_oauth/v1`槽，不要求把已有GitHub CLI OAuth登录改成PAT。可选交互式HOST不默认安装，不创建、刷新或改变登录。原Registry/PN继续管理账户绑定、调用准入、结算和回放。

## 安装匹配的两个包

从项目交接取得以下准确、已审阅材料，而不是搜索未核实的包索引同名包：

- `rpnh_harness-0.1.0rc5-py3-none-any.whl`
- `rpnh_gh_interactive_oauth_host-0.0.8-py3-none-any.whl`

HOST严格依赖核心`0.1.0rc5`；普通`dev2026111802`核心没有OAuth槽，不能混用。建议隔离安装，不替换日常环境。准备两个文件的绝对路径后：

```bash
python3 -m venv .venv-oauth
.venv-oauth/bin/python -m pip install "$CORE_WHEEL" "$HOST_WHEEL"
.venv-oauth/bin/python -m pip check
.venv-oauth/bin/python -I -c 'from importlib.metadata import version; print(version("rpnh-harness")); print(version("rpnh-gh-interactive-oauth-host"))'
```

`CORE_WHEEL`、`HOST_WHEEL`由操作者填写，不是已发布下载地址。安装依赖可能访问包索引，但这些命令不访问GitHub账户或调用模型。本页不代表公开发行或npm已经包含此功能。

## 选择已安装HOST

沿用原`AccountProviderSelection`及账户绑定配置。下方仅为provider选择对象，不是完整任务配置：

```json
{
  "schema_version": "rpnh/installed_account_provider/v1",
  "entry_point": "gh_interactive_oauth",
  "implementation": {
    "schema_version": "rpnh/plugin_implementation/v2",
    "implementation_id": "rpnh.local.gh_interactive_oauth",
    "revision": "oauth-candidate-2",
    "handler": "rpnh_gh_interactive_oauth_host.factory"
  }
}
```

原operation的account requirement、账户selection与binding request一致选择`github_oauth/v1`。principal/task/resource使用真正已注册的引用，binding revision沿原绑定，不复制测试中的合成标识。服务为`github`，账户是用户明确选择的GitHub login。`oauth-candidate-2`是HOST实现revision，不是账户binding revision。PAT仍使用`github_pat/v1`及自己的provider。

`RPNH_GH_EXECUTABLE`设置为已批准`gh`程序的绝对路径。HOST按`github.com`和明确用户名读取已有账户，不按全局active账户猜测，并剥离环境中的token覆盖。不要打印或复制凭据，不把凭据写入配置、prompt、Registry或报告。

## 用户会看到什么

需要具有控制终端`/dev/tty`的前台会话。当前调用取凭据前，HOST展示账户、工具、principal/task/call范围，并询问：

```text
Allow this call? [yes/no] (default: no):
```

输入`yes`同意，大小写和首尾空格不影响；`no`、回车、EOF及其它文字拒绝。不再需要输入`ALLOW`加调用编号。这只确认当前调用，不是长期授权。提示明确包括身份`GET /user`和所选只读MCP操作。由用户直接回答，不由agent代答，不接受模型可控制的stdin作为替代。多HOST进程需要各自终端；进程内锁不是跨进程终端协调器。

确认后，原凭据来源检查GitHub返回身份与所选login一致，再提供原有带调用范围的FD。`external_read`准入不扩大GitHub账户实际权限。当前可选HOST支持GitHub.com OAuth App凭据和声明的只读操作，不包括Enterprise、refresh流程、GitHub App凭据或通用写权限。

## 如何理解结果

- `account_provider_identity_mismatch`：核对安装HOST与所选实现revision。加载factory不等于访问账户。
- `account_use_denied`或缺少控制终端：不会自动降级到stdin或无人值守确认。
- `account_credential_unavailable`：不能从通用错误推断具体失败子步骤或HTTP次数。应核对所选账户/类型及脱敏诊断，不泄露凭据。
- 原managed回放读取已结算证据；不要为了取结果重新执行已完成的固定call launcher。

准确HOST0.0.1候选已有本地选定账户`get_me`真实成功、身份匹配、结算和持久回放记录。HOST0.0.2只改确认解析/交互，具有安装后离线回归及真实PTY离线证据，**没有新增真实账户验收**。历史真实记录中的身份/MCP HTTP总次数仍未知。不能据此宣称所有账户权限、工具、前端或公开发行完成。真正必要的新版真实集成验证应交由本地，以新调用及用户前台同意执行；不能仅因提示改变就重复已成功的外部调用。

[受管工具](../controlled-managed-tools.zh.md) · [安装](installation_ZH.md)

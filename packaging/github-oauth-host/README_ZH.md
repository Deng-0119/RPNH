# 可选 GitHub OAuth 账户 HOST

本包为 RPNH 原 Registry/PN 受管账户与文件描述符传输提供交互式账户来源，不随核心默认安装。

在此目录执行 `python -m build`。将核心 `rpnh-harness==0.1.0rc4` 与本包 `rpnh-gh-interactive-oauth-host==0.0.7` 的准确 wheel 安装到同一环境。实现 revision 仍为 `oauth-candidate-2`；0.0.7 调整产品配对，不更改确认逻辑。

使用说明见仓库 `docs/guides/github-oauth-account_ZH.md`。配置只含账户选择，不含凭据。原前台终端逐次确认后读取已选 github.com OAuth App 账户。支持声明的只读操作，不扩张 GitHub 实际权限。

历史 HOST0.0.1 有限定账户 get_me、结算及回放真实成功；0.0.2 有离线和无凭据 PTY 验证。这些记录不等于0.0.7新增真实账户验收。当前不声称已在包索引发布或所有 API 已认证。

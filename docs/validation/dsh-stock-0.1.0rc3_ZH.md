---
metadata:
  document-kind: validation-summary
  language: zh
  counterpart: dsh-stock-0.1.0rc3.md
  status: scoped-prerelease-validation
---

# Stock DSH v0.1.0rc3：限定验收摘要

日期：2026-10-30。本文件是脱敏摘要，不公开用户数据、私有运行日志，也不构成全产品认证。

## 精确配套与环境

- Python `0.1.0rc3`：`rpnh_harness-0.1.0rc3-py3-none-any.whl`。
- operator `0.1.0-rc.3`：`dsh-rpnh-operator-0.1.0-rc.3.tgz`。
- Linux、Python3.12.14、Node24.22.1；实际DSH app-boot/tools/native executor/
  system-prompt均 `0.2.2-alpha.2`，Cordis `4.0.6-alpha.1`、Schemastery `3.18.6-alpha.1`。
  Python依赖保持原约束，实际websockets15.0.1、jsonschema4.26.0、packaging26.3。

配套源码为本发行tag。已核cpn.__version__、Python发行元数据、operator元数据及client
版本标识一致；没有把旧候选验收改称本配套结果。

## 对最终字节实际完成的检查

1. **产物内容：**tarball恰9个产品文件逐字等于源码；wheel恰936唯一成员，其中928产品/
   资源文件映射源码，另8项元数据/许可。MIT、DSH上游及Viewer第三方许可保留。
   公开源码基线不包含私有开发树。
2. **真实非editable安装：**最终wheel安装到新隔离venv，pip check通过；源树外-I导入
   来自该site-packages，版本为0.1.0rc3。
3. **9项直接检查：**已安装导入、空root列表；额外字段、未知操作、空白task id、reopen
   缺reason、禁用控制时stop、root不存在等拒绝；updates-off状态。负例返回预期失败，
   不派发控制操作。
4. **另2项审计wrapper检查：**list和updates-off安装阻断网络connect/bind及产品子进程
   的audit hook，均未触发。该动态观察不能移用于未hook的直接-m进程。
5. **真实alpha.2 Loader配对：**安装tgz非源码软链接；官方boot/loadOverlayPatches
   加载实际bundle patch及native工具执行器，发现5个RPNH读工具、0个RPNH控制工具。
   一次真实native list派生准确已安装Python且带-I，返回空列表；额外解释器选择参数
   被拒绝且未新增spawn。上下文已dispose，新root仍空。未使用客户端UI或用户任务库。
6. **纯函数指导回归：**用构造offer核stock说明不含无关managed安装命令；不作为生产
   发行资格证明。

依赖取得使用官方registry，Node安装脚本禁用；这项有限检查未要求额外native addon构建。
最终新版本检查通过不抹除前期准备失败，也不表示全部历史尝试均成功。

## 未证明的范围

- profile管理CLI安装命令来自官方文档；最终配对直接运行真实Loader，而不是完整CLI安装流程。
- 不宣称最终配套的完整浏览器/UI、真实审批/控制/worker恢复、真实模型质量、时间/token
  节省或完整会话结果通过。
- 不具备managed冷安装/升级/回退资格，没有新supported自动更新声明；stock插件手动安装。
- 不是系统级网络审计。使用隔离空root、无模型配置、无用户服务；动态保证仅限上述具体观察。

[安装与操作边界](../../integrations/dsh/operator/README_ZH.md)说明匹配配套和手动步骤。

# R1 交付修订 v2：替换旧包，勿合入 v1

本包替换 `RPNH_R1_Inert_Iteration_Profile_20261008.zip`。旧包 SHA-256：

`aa210453d56af6ae3ed1658b48edb490db9ee61ce0c1566a0633670dbee912cc`

旧补丁 SHA-256：

`cad3873f4225c326c1dc4ebff2a382c8de9b9ffb0e8d372fbb296f07200f9a45`

旧冻结目录、源码、ZIP 和先前测试记录均保持原样；本修订不覆盖它们，也不改写旧测试事实。旧包曾通过纯 compiler/schema 检查，但输出的 terminal `config={}` 缺少既有 native `run_outcome`。实际 Registry 可以完成三个操作而保持 running，不能据旧纯测试结论宣称能发布 run terminal。因此旧包撤回合入建议，由此 v2 替换。

## 两条补丁路径只能选一条

- 没有应用旧 R1：使用 `inert-iteration-profile-v2-full.patch`，从主线增加全部六个修订文件。SHA-256：`67466bfc13a685dd79dc26d0c0c2f0a33b7016553ccf53728b57906cc2f10ac3`。
- 已应用旧 R1 且六文件逐字匹配旧 hash：只使用 `r1-v1-to-v2-terminal.delta.patch`。SHA-256：`40da4de0316e5fcb07276fcc159c7277403eef942df4769ad361e5f40804e72d`。

不要先后应用两个补丁。两条路径均已 fresh apply/check，并验证产生同样的六文件 final hash。发生重叠或旧文件不匹配时停止，不强行 apply、不覆盖用户改动。

## 接口修订义务

profile 新增必填 `terminal_outcomes`，三项 `stop`、`final_select`、`final_retain` 均必须显式填写 `complete` 或 `failed`。compiler 原样写入已有 `TerminalBinding.config.run_outcome`。旧 profile 缺少该字段会 fail closed；不再通过 example/HOST 修改 Module 补配置。

v2 指交付包修订号。旧 R1 尚未主线发布，本次保留候选 schema `rpnh/iteration_profile/v1` 和模板 `propose_evaluate_select/v1` 的名称，但明确是未发布接口的内容修订，**不宣称旧 profile wire 向后兼容**。已实例化/注册过的实验产物不应静默替换其 schema authority 或 runtime net。

预算修正保持不变：`native_model_call_budget` 保留原 registered returned-model-call 单位，与轮数独立；不重新引入 operation-attempt 计数器。模型绑定仍 inert；无 campaign、模型、训练、socket、登录、付费、Actions、安装或 push。

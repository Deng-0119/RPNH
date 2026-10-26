---
name: rpnh-project-entry
description: "通过原 Registry 和 owner HOST 使用受控本地项目入口。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: project-entry.md
  revision: "2026-10-25.1"
  status: maintained
  basis: "controlled local HOST; Registry authority; checkpoint continuation"
---

# 受控本地项目操作入口

[English](project-entry.md) · [完整项目示例](../../examples/project_collaboration/README_ZH.md)

本入口复用原 Registry、UserCollaborationHost、Branch、plain merge、owner replacement、独立读会话和官方 Viewer。没有新项目数据库、权限表或任务调度器。适合单个可信 OS 用户装配多个受控原主体；不是远端账号认证，也不隔离同 OS 用户。客户端不能在请求里声明 actor、principal、Core、session 或角色来取得调用身份。不同固定 socket 由 HOST 绑定不同的原 session；持有 OS 访问权限的人仍可访问所有这些端点，不把这些端点称为独立真人登录。

## 环境、源码与普通 WSL 入口

从权威产品 checkout 使用绑定该源码的任务内干净解释器。记录 `sys.executable`、实际 `cpn` 导入位置、精确测试修订和本地改动，正式运行前另外核对远端 `origin/main`。发行包 metadata 本身不证明源码最新。示例 0.1.1 兼容包使用 pandas 3.0.3/pyarrow 24.0.0、harness >=rc2；冻结上游 pin 和原包均保留。优先现有兼容依赖，不盲目降级。本指南不执行安装。

原 stock workspace 需要 Linux x86_64、`/bin/bash`、`/usr/bin/unshare` 及原 namespace/Landlock/seccomp 隔离接口。只读 platform/metadata 检查不证明内核能力或 provider 可用。原启动须加载既有获准 profile 并达到 READY，真实 runtime 仍须验证隔离。

工具 namespace 可能与普通 WSL 的 UID/ownership 映射不同。若原 owned-file 检查因系统路径映射而拒绝，通过官方 `wsl.exe` 以预期普通 WSL 用户进入原发行版；不改 owner、权限、UID 映射或原门。另一个环境的 AF_UNIX 拒绝须保留，不能当作 WSL 普遍限制。

既有获准凭据通过环境变量提供时，只把其**已有变量名**加入进程级 `WSLENV`。不把值放入命令行或证据，不把私有 profile 复制到包。以下 PowerShell 变量由操作者选择：发行版、普通用户、权威 checkout、任务解释器及已创建项目目录；`$CredentialNames` 只含原获准 profile 已使用、且当前进程已存在的变量名：

```powershell
$PreviousWSLENV = $env:WSLENV
try {
    $ForwardedNames = @($CredentialNames | ForEach-Object { "${_}/u" })
    $env:WSLENV = (@($PreviousWSLENV) + $ForwardedNames |
        Where-Object { $_ }) -join ':'
    wsl.exe --distribution $WslDistribution --user $WslUser `
        --cd $ProductCheckout --exec $TaskPython `
        -m cpn.rpnh.collaboration.project_local serve --root $ProjectRoot
} finally {
    $env:WSLENV = $PreviousWSLENV
}
```

这条 HOST 命令用于已初始化项目；初始化或完整 driver 也从同一普通 WSL 入口运行。沿用已有获准账号和凭据，不需新建凭据，不持久修改环境。

业务 PN 是 implementer/reviewer 两个 agent 的任务行为；领域处理代码由 agent 写。下级执行网在同一 Registry 内绑定精确业务 invocation/firing，处理 harness 文件/workspace 机制，其 marking/checkpoint 与业务网分离。AgentLoop 记录本身不构成执行网证据。

## 启动或接收已有项目

使用已准备的任务内解释器，从权威产品仓库根目录运行。下列 `PROJECT_ROOT` 为可写 workspace 内全新、较短目录，`PROJECT_PACKAGE` 和 `PROJECT_INPUTS` 为所选包及其输入 fixture；以下创建不调用模型：

```
python -m cpn.rpnh.collaboration.project_local init \
  --root "$PROJECT_ROOT" --package "$PROJECT_PACKAGE" --inputs "$PROJECT_INPUTS" \
  --contributors 2
```

也可对没有共享包的本地已知 Module 使用 --module module.json。--package 会调用原 plan_subnet_import/import_subnet，在新目标 Registry 创建本地身份和 copy-origin，再由新目标 owner 建 Branch/保护项目；不把源权限、session 或运行状态复制过来。--dependency 可重复提供锁定依赖包。--package 和 --module 互斥。

inputs.json 是 entry 名称到 {"schema_id": "...", "value": 任意该schema允许的JSON} 的映射。schema为stock TEXT时value是字符串。任务素材、工具实现、许可证、环境要求由完整项目包和对应使用说明提供；单独的 Module 不是完整可运行项目。

真实业务运行还需要既有、已经批准的本地执行 profile。在 init 时添加 `--execution-profile "$APPROVED_PROFILE"`，选择原获准本地 profile。HOST 会依原机制登记 exact model condition/可选执行绑定；init 不调用模型。此绝对路径仅在本地受控 selector 文件中保存，不进入 portable 包。没有 profile 的项目仍可进行上述协作，但 run 明确拒绝。

创建会将各原 owner 安全停止。开启可变 HOST：

```
python -m cpn.rpnh.collaboration.project_local serve --root "$PROJECT_ROOT"
```

serve 先检查固定配置与 socket 可用性，再调用原 resume_run，不直接开 _RegistryCore 来取得 writer。READY 输出每条固定路由的 socket。一个 HOST 占用该项目；已有 socket 拒绝覆盖。正常结束使用后文 stop-host。非正常关闭或未完成 execution 若不满足原恢复边界，明确阻断，不强开 writer。

## 发送命令

命令可以来自 stdin，或 --request request.json；结果均为 {ok,result} / {ok:false,error,message}。不要求操作者导入 Python/Core 对象。本文 ref 表示复制输出中的完整 JSON 引用，不能用名字或摘要代替。

```
printf '%s\n' '{"op":"status"}' | \
  python -m cpn.rpnh.collaboration.project_local call --socket "$PROJECT_ROOT/owner.sock"
```

每次写操作给稳定、不同的 command_id；重试原操作保留同一个 command_id 和全部参数。修改内容必须使用新 ID。内部按固定 session 与操作种类限定 ID。一次拒绝可能已留下 delivery、验证 run 或候选等原始事实；不删除它们或伪称跨多事务完全原子。Branch/采用仍由各自原事务和 receipt证明。

## 操作顺序与请求字段

1. status：无额外字段。owner 得到当前完整 Branch；各 endpoint 得到当前项目事实及自己的真实 actor/session。owner status 的 branch.branch_ref 与 branch.head_revision_ref 是后续预期。事实源于原 Registry，不是本地 JSON 配置中的项目状态。
2. read-plan（仅owner）：revision、include_runtime。沿选定原revision历史、原stored projection/proof及可选当前runtime依赖，返回record_types/material_refs/export_refs建议集合；不签发授权、不成为新的export plan。操作者审阅选择后将数组传给grant-read；真正原reader/export仍会重验。此方法不扫描并默认披露全部Registry资料。

   grant-read（仅 owner）：subject_session、command_id、expires_at、record_types、material_refs、export_refs、export_destinations。expires_at 为未来一小时内的带时区时间。没有默认整库材料选择；操作者必须显式列出允许读取的 typed record 类型、精确 source-qualified material refs，以及可选的 exact export refs/目的地。原 HOST 的 collaboration fact读取需要该 task完整协作事实scope，已明确包含；PN body与export只允许所选精确refs。想只进行邀请/审核可将后三种refs和record_types设为空数组。grant-read不因join、status、重新开Viewer或serve自动发生。
3. invite（owner）：subject_session、role（原支持的propose/read）、command_id。subject_session取init输出的routes；返回完整 invitation ref。
4. join（被邀请者固定socket）：invitation、command_id。先由owner明确授予协作fact读权限；join本身不增加body/export权限。
5. module：revision（完整source-qualified exact ref）。返回module及element_ids。owner可读本项目自身材料；贡献者必须已有该revision typed record与definition/element mapping精确body权限。init输出的initial_revision给出四个原材料ref，不能据此推定其它资料已授权。
6. history：revision。返回最多128个原精确parent链记录；超限拒绝而不截断宣称完整。贡献者同样必须持有当前原reader授权。
7. propose：base、branch、head、document、element_ids、reason、command_id，可选previous_proposal。document是完整编辑后的Module，element_ids是操作者明确提交的完整locator→稳定ID映射；编辑时保留module读取响应中的显式映射，新元素需要新的合法ID，不能凭同名推断rename/copy。base=head，branch必须属于当前项目且首次提交是当前精确head。原生人工验证run显式绑定固定个人origin，成功后才走原delivery/submit；不使用synthetic分支。
8. review：proposal、disposition（approve/request_changes/comment）、reason、command_id。原权限决定谁能批准；comment不是批准。
9. analyze（owner）：base、local、incoming、command_id。返回原analysis ref、differences和conflicts。客户端提交精确两个revision，不能伪造analysis。
10. merge（owner）：analysis、choices、command_id。choices每行含conflict_id、choice（base/local/incoming/delete）、reason、delete_element_ids；必须覆盖所有冲突，未删除时delete_element_ids=[]。返回原真正双parent修订，不推进Branch。
11. accept（owner）：revision、branch、head、proposals、reviews、command_id。proposals/reviews按原要求一一对应，双parent结果需要完整必需输入的精确批准。原commit再次检查资格、撤权、stale head、review与结果。返回Branch、decision与实际receipt。接纳不改变runtime。
12. runtime-status（owner）：无额外字段。返回原当前net/checkpoint、marking token、active firings与pending edits。不是任务成功证明。
13. adopt（owner）：receipt、branch、head、runtime、marking_mapping、retire_token_refs、owner_inputs、command_id。receipt来自accept；proposal/review集合由其原decision导出，不由请求者重报。runtime来自runtime-status；marking_mapping是exact token version ID→候选symbolic place。其它容器必须显式提供，即便为空。保留原whole_net_quiescent、budget、workspace、token安全门。仅有真实receipt才报告采用已完成；NEEDS_MARKING_DECISION/忙碌等返回原状态。
14. restore-content：restore_from、base、branch、head、reason、command_id。restore_from必须出现在当前head精确祖先链。原内容作为明确人工恢复输入，经真实验证operation→原proposal。旧element ID仍在当前版本且kind一致时可保留；其余元素取得新ID，不凭名称/locator复活旧身份。input原材料与proposal reason都保留exact旧ref。仍需review→accept→adopt。它不是Branch reset，不撤销外部业务效果或历史token，也不定义“任意版本插入”。
15. download：revision、destination。调用原plan_subnet_export/export_subnet，必须有所有闭包refs和目的地的原export grant；不会自授读权。响应包含原结果与archive_base64、dependency_archives_base64。客户端解码成文件，另一目录用init --package接收，源历史只是来源证据。选择精确原双parent M，不能用内容恢复后的单parent后继替代；receiver须从自己的下载包读取fixture/environment/license闭包，建立新ID和自身owner授权。
16. viewer（owner）：返回真实dashboard JSON。viewer-open（owner）：使用既有reader在回环地址开启原官方只读Viewer并返回URL，数据请求每次重检原owner权限。贡献面板仍受其独立reader的范围、过期/撤权/预算限制；缺资料会拒绝，不默默扩scope。当前未提供非owner的完整Viewer服务，非owner可以使用授权的事实/Module读取。
17. run（owner）：command_id。仅使用可信启动时指定的既有profile，通过原OwnerEventLoop/ExecutionServices/Orchestrator执行当前已采用网。返回REQUESTED与原execution_ref，仅表示进入当前HOST的原owner lane。通过runtime-status.host_run_request轮询RETURNED/FAILED；RETURNED.result包含stop_reason、真实terminal引用与实际模型计数；不将“存在terminal”替代项目原verifier。模型、预算、工具、运行环境仍应在调用前取得实际用户授权。实际计数须来自对应执行回执，不能由cloud静态检查推断。
18. revoke（owner）：subject_session、command_id，只变更贡献资格。撤销读取使用revoke-read（owner）：grant、command_id，直接调用原revoke_read_access。权限类型不能混称。
19. stop-host（owner）：command_id，调用各原owner的安全停止；未满足原停止条件会明确失败。它不抹除pending或运行历史。业务恢复须走下文原checkpoint reentry及精确launch重放边界，不支持任意终态回退。

## 协作、内容恢复与再用

按[完整示例](../../examples/project_collaboration/README_ZH.md)进行：完整seed导入 → 两受控主体人工定义经真实validator → 首次真实冲突 → A-resolved引用B精确proposal/revision及A的previous_proposal → 重新分析/全部显式choices → 真正双parent M → 受保护接纳。M须同时保留A的reviewer指令和B的连通容量约束。A-resolved的真实提议者仍是A；B提案、修订理由与M parents证明协调来源，不捏造字段作者。

每次接纳前后比对runtime。在首次业务firing之前分别采用M、base内容的新后继、M内容的新后继，每次都使用当时真实marking。内容恢复保留历史，不重置Branch、不复活已耗尽token、不撤销文件效果。下载精确原M，不用后继替代。独立receiver使用自身下载闭包、新身份和明确owner授权；两端随后由stock agents执行同一原任务。

## 原运行恢复与剩余边界

普通safe stop/resume保留原principal/session，须等待READY；reader过期或撤权后须重新显式grant，重开reader不会取得writer。原人工验证execution中断且没有成功stock TEXT时，不得用synthetic delivery或新身份修补。

业务执行失败时保留原请求、launch receipt、checkpoint、三Registry及原失败/计数。通过原native checkpoint reentry排空/对账原工作，持久化明确reopen authorization；它创建新的execution generation并保留原历史。这不同于重跑fresh目录driver或任意终态回退。

reentry后，本地HOST仅在原run authority为`running`且已有持久化`reopen_authorization_ref`时，允许**精确旧command/旧launch重放**。只有上述持久化native checkpoint reentry之后才复用原command ID和launch receipt；新execution generation不等于新run command。原`bind_execution`继续核验原关联及全部原门，不创建新的追认关联。尚有unsettled firing或缺reopen authorization时拒绝。原凭据失败即使记录为`NOT_SUBMITTED`、provider HTTP 0也须保留，后续结果使用自己的回执。

原protected pending correction/retraction仍未提供。runtime-status可查看pending；不清pending、不改owner/security flags强行推进。不支持任意终态reentry、活跃token热迁移或graph/open-region/selected-change/assembly的大范围交换，不复制活跃invocation/session/密钥。

创建或导入不等于依赖就绪、业务任务完成、独立真人认证或浏览器已验。

## 原 owner 调度与读取预算

serve的传输线程只parse固定route并调用原OwnerEventLoop.submit_host。HOST主线程使用同一个原owner loop处理命令和Orchestrator/Harness，执行中控制请求仍由原dispatch_ready进入，采用保留原quiescent门；没有第二个任务scheduler或跨线程Core写入。REQUESTED/RUNNING/RETURNED为当前进程观察缓存，不是持久事实/新调度账。同进程same-ID观察缓存不构成恢复authority；断连不撤销已接纳命令，重启必须依原native恢复边界和持久化reopen authorization，才能精确重放旧launch。任务运行时stop-host拒绝；人工验证器的独立source执行会短暂占住target dispatcher，未承诺无限制并发或实时可用性。未建立无限制socket并发保证。

init --read-closure-limit 16777216可为这一受控项目明确配置原ReadLimits的累计closure预算。默认仍4MiB；单body和response原上限不变。它不会扩大任何material_refs/typed/export授权。fixture去重闭包实际为4,553,881 bytes、70个exact body refs、4 revisions，最大单body1,941,458 bytes，material refs61，故需显式大于4MiB的总预算；16MiB是场景选择，不是本地机器硬要求。

## 已结算产物与正式验收

artifacts-plan（owner，无额外字段）只选择当前原settled checkpoint的workspace_revision_refs。返回task/net/checkpoint/firing绑定和files中的exact workspace/path/resource/sha256/byte_count/mode，以及material_refs建议。它不发grant，不读未结算私有工作目录，不证明verifier通过。操作者明确合并所需material_refs到owner的grant-read范围后，artifact-read（owner）传checkpoint、workspace、path，读取原scoped read_material并核对已结算archive SHA。返回content_base64及绑定。path只是原mapping的exact key，从不拼接HOST文件路径。

collector只对精确`RegistryReadSessionError`／`limit exceeded`响应，调用原owner取得**相同已选scope**的新reader，并对完全相同的checkpoint/workspace/path请求**仅重试一次**。receiver首次导出在第六个文件遇到retained-snapshot限制；原失败保留，有界重试后20文件全部导出。其它错误直接返回；不提高read caps、不绕过expiry/revocation，单body超限仍失败。

上限：128个workspace祖先、4MiB descriptor总量、16MiB选定archive总量和产物总量、2048文件、每文件4MiB。TAR头在原parser前检查大小、类型与sparse；超限明确阻断，不自动放宽。每次读取重新检查当前checkpoint与原owner/reader权限。

收集两端真实产物、实现、完整instruction审阅、attempt/失败和精确依赖。披露路径替换的原断言成绩与真正原布局test.sh/CTRF/reward分开报告，grader已可见。原terminal不是评分通过。

实际官方Viewer图须对应同次revision/net/checkpoint，展示agent简要视图、完整PN、贡献、内容恢复/采用、资源及所测版本实际提供的下级执行网。不得用AgentLoop日志、手画图或假refs代替执行网证据。

## 历史 Viewer 的读取范围

查看 Previous Net Segment 或其贡献，除所选 revision 的协作事实外，还需要该保留 runtime net 对应的精确 compiler-wire body。只授权当前网的 compiler body 时，旧 M/base 视图可能正确返回 HTTP 403。须通过原 owner grant 显式选择历史 body，不放宽读取门。

新流程中，driver 在每次原 adoption 成功、该网仍为当前网时，立即记录含 `include_runtime=true` 的惰性 `read-plan`；最终 runtime grant 将同项目已记录 scopes 与当前 plan 合并。记录 plan 本身不授予权限，后续明确 owner grant 才是授权依据。export 行为及 `include_runtime=false` 的 grant 不变。使用完整 driver 的操作者无需另造 Core 流程。

本次保留运行只通过普通只读检查，从原 adoption nets 找回两个精确旧 compiler-wire body，核验 schema/envelope 及与实际 native Module 相等后，显式加入 owner grant。历史 base 贡献请求由 403 转为 200。这是读取范围配置纠正，没有新增 API、Core writer、权限放宽或模型调用。source 历史导航／捕获现已接受，五个检查的历史／贡献 API 均为 HTTP 200。查看时从当前完成视图使用 Previous Net Segment 回到 base 内容恢复，再到原 M；检查实际采用引用与 M parents，最后返回当前完成视图。此浏览器结论不替代 caller 代码独立审阅。

## 2026-10-13 原生检查窗口

普通WSL中两个受控贡献者的原生人工输入验证、真实双parent M、业务前三次采用、精确M下载和新ID不重叠的独立receiver导入已通过。原checkpoint恢复保留同三Registry和原失败/计数历史。真实无模型回归一次通过（27.70s），此前两次fixture失败保留。精确重放仍使用原bind_execution门。

两端原生任务均已完成，独立完整 instruction 审阅**接受两端**。以下结果对应原 source/receiver 执行，不是替换 run command 的新运行。

| 已核验结果 | Source | Receiver |
| --- | --- | --- |
| 登记请求／模型响应 | 23／22 | 20／20 |
| 业务 firing | 3：原中断 firing 1 个、完成职责 2 个 | 完成职责 2 个 |
| 精确 scoped 产物导出 | 18 文件／47,815 字节 | 20 文件／52,089 字节 |
| 路径适配原断言 | 3/3 PASS | 3/3 PASS |
| 原 `test.sh` | NOT_RUN | NOT_RUN |
| 各自导出 workspace 内的 agent 实现 | `workspace/merger.py` | `solution/merger.py` |

共 **43 次登记请求，未超过授权上限 48**，响应共 42 次。source 保留原一次 `NOT_SUBMITTED` 凭据失败，该次 provider HTTP 请求为 0。上述是登记／响应计数，不推定尚未报告的 provider retry attempt 分母。恢复仅在持久化 checkpoint reentry 授权之后复用精确旧 command ID 和 launch receipt；变化的是 execution generation，不是 run command。轮询只观察原执行，原 HOST 持续运行。

两端原断言保持全部检查，仅披露四处 `/app` 输出路径字面量替换；grader 为 **VISIBLE**，不称盲评官方容器成绩。独立语义审阅核对三个原输入字节／摘要，确认两份实现均通用且没有硬编码答案。两端全部四行／20 单元格正确，恰有用户 101 的三项字段冲突（`name`、`email`、`created_date`）。两份 Parquet 字节一致，冲突 JSON 唯一区别是 source 多一个末尾换行。可选 status 的 `active`／`true` 归一化仍是已披露的指令歧义，不判缺陷。receiver 的 `check_out` 复算与原交付字节一致，不是修补原产物。

source 和 receiver 实现摘要不同。receiver 从**业务执行前**下载的精确原 M 独立导入后自行编写实现，包未复制 source 答案。两端实际 Registry 业务 PN 均为 **5 节点／6 弧、两个 agent 职责**。source 有三个 AgentLoop 实例（含原失败），receiver 有两个；loop 实例计数与下级执行网证据分开解释。

两端实际官方 Viewer 捕获窗口均完成，**报告缺项为 0**：source 捕获 101 张 PNG，receiver 捕获 28 张。两端简要视图均为两个 agent 节点／一条边，完整业务 PN 均为五节点／六弧且无裁切；sidebar 布局修正后真实 child 执行网图也完整适配。receiver execution API 返回 HTTP 200，关联真实文件物化／workspace-finalization 的 `map_ready`／sealed 证据。

source 通过原生 **Previous Net Segment** 连续两次导航，先查看已采用的 base 内容恢复，再查看原双 parent M 及其首次采用。M 两个 parents 和全部八个提供的历史引用均实际可见，返回原 completed 视图也已核验。在 owner grant 显式选择两个精确旧 compiler-wire body 后，检查的五个 source 历史／贡献 API 请求均返回 HTTP 200；原 Core／读取门不变。

本有界流程在 2026-10-13 原生检查窗口完成原生执行、原路径适配评分、完整 instruction 审阅和两端实际 Viewer 检查。首次浏览器尝试在 HTTP 前发生的 socket 路径过长失败保留。真实图及其精确观察引用见下方图像来源；模型登记总计仍为 43。

## 检查窗口与实际图

2026-10-13 原生检查窗口的实际官方 Viewer 图。[图像来源与逐图记录](../../examples/project_collaboration/figures/provenance.json)包含精确源码来源、PNG 摘要、revision/net/checkpoint 及观察位置。17 张 PNG 共 4,908,456 字节，原图未变换；同名 JSON 仅省去 localhost URL 和本地 run_dir，保留原 panel text、refs 与 position。source 捕获为 H3474/E8，receiver-04 捕获为 H2441/E4；之后 owner 读取授权行政续签推进 receiver 到 H2444，不能据此声称不同窗口 head 相等。历史 cut、checkpoint selector hint 和捕获 head 分别解释。

![source 完成后的 agent 简要视图：implementer → reviewer，两个 agent、一次交接。](../../examples/project_collaboration/figures/source-overview.png)

source 完成后的 agent 简要视图：implementer → reviewer，两个 agent、一次交接。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](../../examples/project_collaboration/figures/source-overview.json)。

![原 M 在历史 cut 574 的贡献观察，发生在业务执行前，不是 completed 状态。](../../examples/project_collaboration/figures/source-M-contributions.png)

原 M 在历史 cut 574 的贡献观察，发生在业务执行前，不是 completed 状态。 source-05 · cut 574 · selector 574 · [精确引用、面板文字与位置](../../examples/project_collaboration/figures/source-M-contributions.json)。

![历史 selected-observation JSON 中实际可见的首次 M 采用回执精确版本。](../../examples/project_collaboration/figures/source-M-adoption.png)

历史 selected-observation JSON 中实际可见的首次 M 采用回执精确版本。 source-05 · cut 574 · selector 574 · [精确引用、面板文字与位置](../../examples/project_collaboration/figures/source-M-adoption.json)。

![真实关联的 workspace-finalization 执行 PN，有独立 checkpoint/token 及 map_ready／sealed 证据。](../../examples/project_collaboration/figures/source-execution-finalization.png)

真实关联的 workspace-finalization 执行 PN，有独立 checkpoint/token 及 map_ready／sealed 证据。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](../../examples/project_collaboration/figures/source-execution-finalization.json)。

![独立 receiver 完整业务 PN：五节点／六弧，两个职责均完成。](../../examples/project_collaboration/figures/receiver-petri.png)

独立 receiver 完整业务 PN：五节点／六弧，两个职责均完成。 receiver-04 · cut 2441 · selector 2386 · [精确引用、面板文字与位置](../../examples/project_collaboration/figures/receiver-petri.json)。

[完整图组及运行结果](../../examples/project_collaboration/README_ZH.md)。

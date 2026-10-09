# RPNH V5 本地分阶段验证结果

当前任务中可运行的阶段验证已完成，整体为 **PARTIAL**。本次只发布分阶段结果、冻结候选补丁和经过内容复核的证据；产品代码未合并这些候选。完整 H7 原生链仍未实现，最终跨 lane 组合未运行。

## 实测结果

| 阶段 | 不同用例 / 执行次数 | 结论与范围 |
|---|---:|---|
| S1 | 离线100 /112；原生10；另对照1 | 离线全通过。最终原生9通过、g7失败；无替换对照通过。 |
| H7 core |139 /139 |现有 D0 首片通过；不代表完整 H7。|
| H7 history |298 /298 |247在完整工作树、51在原严格994文件快照；两种范围明确分开。|
| H7 lowering |774 /774 |原768（新声明28、编译器516、核心177、独立47）通过；另补齐原缺示例导致无法收集的精确六项。|
| R1 v2 |153 /153 |作者/编译器150、独立Registry3通过；独立compiler33因包缺helper未运行。|
| R2 |170 /170 |兼容150、step11、独立5、真实OwnerEventLoop/AF_UNIX4通过；RSI跨两阶段去重173、执行323。|
| Codex reader |32 /32 |原冻结读取用例通过。|
| Codex effort |48 /48 |原42、独立4、本地socket1、未改bridge1通过；不是stock认证。|
| Codex history |162 /162 |原119及原精确Registry/MainSession43通过。|
| Codex 0.161 |0 /0 |前置1041锁仅两份ARCHITECTURE文档不匹配，应用及后续门禁BLOCKED。|
| DSH |82 /82 |codec39、launcher9、owner34通过；另两项factory探针。Node24/pnpm及真正上游Session门未运行。|
| OpenCode |307 /307 |G1 274、独立33通过；重复窗口不增覆盖。G2/G3 stock门未运行。|

每个版本分别计数，不把各行相加为产品组合覆盖。完整原命令、UTC、cwd、环境、真实exit、JUnit用例ID、重叠与明确排除范围见 [结构化结果](result-manifest.json)。

## 必须保留的失败与阻断

S1 g7 在合法HOST成功和待处理动态替换交界失败：RunOwner.succeed先采用新网络，终态产物仍绑定旧网络的精确producing firing，随后Harness终态校验抛出 `ResourceIntegrityFault: terminal product differs from exact published producing firing`。同一冻结夹具不做替换可正常terminal，排除了这个例子的通用夹具/terminal不兼容；没有基线回归对照，不能归因于S1补丁本身。原生g7仍为FAIL，不能用事后只读分析或对照PASS替代。

[最终原生结果](stages/core/S1/native/result.json)、[原始g7失败JUnit](stages/core/S1/native/a004/g7.xml)、[直接provenance分析](stages/core/S1/native/g7-direct-analysis.json)、[唯一因果对照](stages/core/S1/native/g7-control-comparison.json) 保留exact refs和真实AF_UNIX/OwnerEventLoop条件。HOST Future在owner进程内受控完成，不宣称并行HOST线程或外部业务worker。

Codex 0.161锁的1039文件匹配，只有 `docs/ARCHITECTURE.md` 与 `docs/ARCHITECTURE_ZH.md` 因当前H2a reader说明不匹配。原1041/1044清单、补丁和旧文档未改，未强行应用。包静态verifier的首次失败另因临时目录向上发现工作区metadata Git，git apply跳过了六条路径；限定临时Git发现边界后，原verifier及所有原hash检查通过。这不解除checkout锁或stock门。见 [Codex复核](local/reviews/codex-recovery-audit.md)。

R1所选v2包的独立compiler测试导入缺失的 `test_inert_profile_independent`。保留实际collection错误，33项标NOT_RUN，没有从另一lane/旧包拼helper。DSH没有原Node24/pnpm环境；Codex没有可核实的现存0.155/0.161精确stock二进制（0.160元数据不替代），OpenCode没有可核实的精确stock Linux ELF。没有下载、安装、登录、默认版本升级或真实模型调用。

第一次磁盘中断留下的点号、NUL尾、空JUnit及缺exit记录均保持INCONCLUSIVE；重跑使用新名称，旧记录不覆盖。S1早期探针的输出端口/Authority比较错误及一次collection缩进错误另列为探针问题，最终a004仍保留真实g7失败。OpenCode首次补丁白名单解析错误已有严格unified格式校验纠正，旧诊断保留；其单独simulated-failure槽仍不宣称完成。早先A1权限阻断和业务benchmark分数未重写。

## 输入、身份与资源

输入 `RPNH_Complete_Local_Bundle_v5_Transfer_20261009.zip`：15350659 bytes，SHA256 `126b79ebde8093bf94536453130607ab4f680a951ca0bf58d9761d705eeda326`。外层ZIP、tar、48成员、13原ZIP与候选索引经原静态verifier核验；该PASS仅为包完整性。精确完整基线为 `1f191645c4d60c8b190d42e9fad99c85e8981c03`，产品基线d92ff37；先审查远端三项文档/旧证据更新再快进，没有reset。

十二候选均核对原patch身份；其中十一项通过old blobs、apply --check与postimages后隔离应用，Codex 0.161因前置锁不符未应用；core四个完整工作树的冻结979/992/994/999投影匹配，全量before/after清单仍另列。只读history的51项保留原994锁，不混称全部298在完整树运行。Codex effort独立x2只含effort；x3为reader+effort+history。所有适用完整源码与原清单检查通过，生成pyc缓存另列，不计为源码。

恢复期资源采样：D盘最低可用50.31 GiB、可用内存最低13.92 GiB、任务+运行目录峰值5.33 GiB。最多两路重测试、D盘25 GiB保留、内存4 GiB保留、任务8 GiB上限，全部采样在预算内。已结束且不含特殊文件的本任务runtime先压缩、逐文件/符号链接校验，再移除展开副本；完整原件只留本地归档，未碰历史任务、外部worktree或WSL虚拟磁盘。ext4释放不冒充等量返还D盘。

当前受管环境的euid/祖先UID以 [实际记录](local/LOCAL_ENVIRONMENT.json) 为准；不把它泛化为普通WSL UID1000。Python为已有3.13.12环境，pytest8.4.2/jsonschema4.26.0；Node现存22/20。所有新真实provider/model、收费调用、Actions、下载安装均0。

## 交付边界

本次GitHub新增内容仅为该证据目录。十二冻结根补丁作为候选证据，历史失败patch另标记，未合入产品。公开文本只作记录在案的私有路径前缀替换；原件/包含件SHA、大小、字节和行范围见publication-manifest。空文件、畸形XML/JSON和NUL原始记录保留，并有明确非替代的escaped视图。实际DB、私有profile、凭据、binary、venv/cache及压缩runtime不上传；无新返回ZIP。

H7 public inventory/native issuer/peer/receipt/reservation/wrappers/child completion为UNIMPLEMENTED，N01–N36是未来验证合同；不借此次D0通过写成已完成。最终组合需独立定义选入版本、新清单和门禁，本次NOT_RUN。

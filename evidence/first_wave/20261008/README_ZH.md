# 首波案例：原始失败与本地验收证据

本目录按用户要求保留失败的原始内容，供网页端结合环境、版本、配置、输入交付、模型／工具行为及验收语义分析。不要只从最终分数推断单一原因。

ERP 在源码 `6f8ee2e406f3c70edb73206f861e56a0202b9f15` 上的最终实测为：2000 smoke A04 原始100/100、9次真实调用；2299 showcase A01 原始21/100、13次真实调用，未通过业务验收。历史A03为原始0分、11次真实调用，属于较早的 `2ca5fbc` 源码条件。后续集成没有改写这些源码身份或分数。

`erp/MANIFEST.json` 登记595个保留文件，约13.1MB。其中474个是已有文件或注册payload的原字节，121个明确标为元数据序列化；已有公开投影也有独立类别，不能误当供应商原始网络包。内容包括8个环境窗口、4个失败验证窗口、启动失败、24份请求recipe、24份adapter返回和24份规范化响应、工具参数／输出／引用以及完整原始 reward、rules、spend、optimality、checks。

两份响应版本不等于两次调用。未保留的 vendor wire 和完整 Codex CLI event stream 明确为 NOT_FOUND。40个历史资源的 fresh-reader provenance 校验失败单列保留；读取精确对象字节不表示原 provenance 已通过，也不赋予执行权限。原件及 Registry head/epoch 保持不变。

SCB 新增适配器已完成59项合成测试。ERP 合并边界通过190项测试及35个另计 subtests；共享契约22项和4项声明测试亦有独立窗口。安装态 owner／插件／AF_UNIX 命令执行及固定上游 Docker Session 的 complete／stop 夹具各使用3／2次本地脚本响应，真实 provider调用为0。停止样本保留 outcome_unknown、owner退出2，无终态产品或快照；Docker stop的-1表示上游退出码不可用，容器移除和写入静止另有证据。

这些SCB夹具直接验证原生组件，尚未验证完整 `CheckpointPilot.run`／CLI 的真实 `code_search` 解题、原始evaluator或官方AgentRunner。镜像为最小Python夹具，不是SCB通用基础镜像。无timeout-case验收，不声明基准成绩或harness优势。实际执行身份是6f8加未提交案例文件及精确安装payload摘要，不能重标为新集成commit上的运行。

`scb-local/` 保留安装态初次失败和最终夹具证据，`reviews/` 保存有限独立复核。首次失败包括helper映射序列化、非canonical fake响应、gate与同步snapshot相互等待、错误结果端口；这些是基于实际诊断的分类，原始错误内容仍保留以供复查。

原始配置、凭据、Registry数据库及其WAL/SHM、ERP dump/tar、虚拟环境、缓存和ZIP不在本目录。历史WSL路径、源码路径和对象引用按诊断需要保留，它们不是访问授权或当前可运行地址。凭据出现时仅作必要字段替换并记录位置；本次ERP595文件未发生内容替换，已知密钥复查零残留。`ERP_PUBLICATION_REVIEW.json` 是新的发布检查，初始记录内的 pending 状态保持历史字节。

建议分析时同时引用具体条件、原始请求／响应与工具记录、环境／配置证据和原始规则行；把已验证事实、候选解释和缺失观测分开。验收器checks.log无traceback只涉及该日志，不等于所有模型脚本没有错误。原始分数和Actor自述不同，以原始grader为评分依据，工具／模型轨迹用于解释过程。

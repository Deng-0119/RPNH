# 被发现的问题与已完成修正

1. 前序文档引用缺失的 COMPATIBILITY_MATRIX.json。没有沿用“已完成”表述；补写20项证据矩阵，保留原文与补全说明。53份原provenance都对，但3份官方源无provenance；已用官方精确tag重新取证，56份最终全核。
2. 初版source只复制已交history包，未包含d92两文件。首轮作者69项与独审34项记录不能称d92验证。现独立合成base补确切d92字节，BASE_SOURCE_LOCK记录继承，作者122项及独审34项在最终组合重跑。旧轮保留在pre-d92，重复运行不累加。
3. 独审首轮34项中出现33过/1探针自身失败：在首个RO连接前拍fingerprint，合法空WAL创建被误判写入。探针改为先建立RO/epoch观察窗口；产品无需修改。前后日志/JUnit均在independent-review/pre-d92，最终组合仍只计34。
4. native logger旧复制逻辑把object按opaque decode，静态审查发现会拦合法输入。现按dict/string/null分类，只记录形状布尔与合法opaque的cut/token hash，不回显object字段或body。该修复经纯fake测试，无native运行。
5. runner遇拒绝RPC仍可能随TUI exit0继续lane。现unexpected RPC、任一RPC error、child尝试累计violation，最终返回非零并停对应lane；即使client_exit_code=0也不能记gate成功。包装结束恢复原方法。纯fake覆盖。
6. analyzer原版仅set全覆盖不能证明请求cursor链，独立合成错request cut/token重现仍被判完整。现按时间顺序分别校验turn/items前一next→下一request、同resume cut、不重复next、最终耗尽，跨desc item页重复亦拒绝；动态metadata/item limit合法链保留。独立5份日志全部符合预期，作者16项fake/log全过；同测试复跑不能计两次。

这些都是设计、组合、探针或交接脚本问题，不冒充已发生的stock客户端故障。0.161 stock、原生WebSocket、Rust以及provider运行均未执行，相关认证不能从此修正记录推出。

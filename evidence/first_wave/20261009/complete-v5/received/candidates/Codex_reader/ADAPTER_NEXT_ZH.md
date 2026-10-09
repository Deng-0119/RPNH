# 后续 adapter 小包的边界

本包可以稳定列出同一 native cut 的 committed turn/字段引用，不足以直接实现公开历史正文。

1. 先确定现有MainSession/public display权限边界如何在同一cut读取安全文本。不要把
   `_project_thread_at_view` 的任意 user_input/answer JSON直接序列化；配置、private task
   prompt、instructions、child answer/decision/receipt不因分页自动获准披露。
   不存在合适catalog/grant时停该branch明确报告，不能把native anchor当授权。
2. 使用existing renderer语义，仅公开原user text、reply、protocol_valid所需提示。
   at-cut child-link facts可供明确的immutable annotation设计；当前TaskControl fallback
   必须与snapshot分开标识，不能混入旧页或静默丢必要内容。
3. thread/resume首次共用一次cut，为turns/items各生成官方要求的initial-inclusive anchor。
   后续continuation-exclusive，不可两个cursor-null请求各捕新head。
4. transport cursor必须有界、严格解析并绑定既有会话source与授权；序列化版本、query、
   order、itemsView、turn filter都需明确。native anchor只是一种重校位置，不是bearer grant；
   不接收任意filesystem path、不另建cursorstore。native默认asc不等于Codex turns默认desc。
5. 确定live accepted/running通知与committed exactturn的ID映射。字段身份不能用会重排的
   UI index；防止一次terminal既通过notification又通过cold history重复显示。
6. 对两版官方schema和真实consumer确认notLoaded items=[]、summary/full语义、turn filter、
   limits、nextCursor、inclusive首次页。native字段refs不能单独声称full public items。
7. 用至少两个committed turn的冻结Registry做deterministic RPC矩阵，再做独立授权的
   stock 0.155 cold-resume运行。0.161另列认证，不因schema/refs-only测试自动换pin。
   所有历史读取须零provider/零launch/reconcile/恢复补偿，读权限每次重校。

当前原有Codex historyMode/分页契约问题仍是未完成事项；本前置包不替它改变声明或版本。

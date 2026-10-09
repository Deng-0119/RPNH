# 下一独立 Codex renderer／分页任务的 API 摘要

现有绑定：MainThreadRegistry(core, session_root=...)；core可为read_only=True。
不通过MainSession refresh/activation/reconcile来获取分页，不需要profile或TaskControl。

准确入口：

```python
cut = registry.capture_read_cut(max_object_bytes=None)
snapshot = registry.project_thread_at(cut, max_object_bytes=None)
turns = registry.page_turns_at(
    cut, limit=50, order="asc", anchor=None, max_object_bytes=None)
items = registry.page_items_at(
    cut, limit=50, order="asc", turn_filter=None, anchor=None,
    max_object_bytes=None)
```

类型位于cpn.rpnh.registry.main_thread_history：

- MainThreadReadCut(task_id, branch_id, view, boundary_event_id, thread_ref)
- MainThreadHistoryProjection(cut, state, next_turn_ordinal, active_turn_ref,
  latest_turn_ref, latest_committed_turn_ref, turns, child_links)
- MainThreadHistoryTurn(ordinal, turn_ref, items)
- MainThreadHistoryItem(turn_ref, turn_ordinal, item_index, field)
  field仅user_input/answer，index固定0/1，全部为refs-only；不含body。
- MainThreadHistoryChildLink(link_ref, origin_turn_ref, task_control_id,
  task_kind, state)，不含child path或body。
- MainThreadHistoryAnchor(cut, query, order, turn_filter, turn_ref,
  turn_ordinal, item_index, inclusive=False)
  query=turns/items；turns的item_index必须None；items必须0/1。
- MainThreadHistoryPage(cut, entries, next_anchor)

每页limit为1–100整数，order asc/desc。desc反转(turn ordinal,item position)整体顺序。
turn_filter只接受cut上exact committed VersionRef。初始inclusive=True可包含目标，
返回next_anchor都是exclusive。两个首次查询必须共用同一个cut；跨cut/query/order/filter
anchor拒绝；page size不参与identity，可以改变limit。无剩余项时next_anchor=None。

cut重开同逻辑Registry可重读，但不是授权或防伪token。每次RPC必须校验现有session/source
访问权；不得从cursor构造任意core/path。当前filesystem child path安全校验仍会触发，
symlink改变可让旧cut fail closed。不得以快照稳定为由去掉检查。

max_object_bytes=None表示无额外人造cap；物理body读取始终按登记size界限。显式预算
只能拒绝该请求，不能遗漏/截断条目。每页仍全lineage重建校验，不是总内存/CPU有界。

关键未完成边界：user_input/answer任意JSON含private configuration/task prompt。
未新增公开body reader、grant或TypedReaderCatalog条目。下一包必须把既有MainSession
安全renderer接到固定cut的获准内容读取边界；不能调用内部dict然后全部序列化。
必须保留user text、reply、protocol_valid提示与必要item；实时TaskControl annotation
另标，或仅用同cut已登记linkfacts明确投影，不能混用当前状态假作旧snapshot。

不含Codex DTO、transport cursor编码、itemsView或thread/turns/list、thread/items/list
RPC。native默认asc不代表Codex默认值。accepted/running通知ID到committed turn的映射、
首次inclusive cursor、重复terminal去重、授权复核与cold resume均由后续独立包验证。

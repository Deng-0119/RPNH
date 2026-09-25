/** Localized captions on existing cells; no execution or geometry changes. */
import { t } from './i18n.mjs';
import { nodeKey, edgeKey, arcLabel, wrapLabel } from './model.mjs';

export function refreshCanvasText(renderer, snapshot, layout) {
  const compact = snapshot.view_mode === 'overview';
  const flow = compact || snapshot.view_mode === 'flow';
  for (const node of snapshot.nodes) {
    const cell = renderer.graph.getCell(nodeKey(node.id)), text = node.display;
    if (!cell || !text) continue;
    cell.attr('tooltip/text', compact ? `${text.name}\n${text.description}` : `${text.name} — ${node.id}\n${text.description}`);
    if (compact) { cell.attr('status/text', ''); cell.attr('divider/display', 'none'); }
    cell.attr('root/aria-label', `${flow ? text.type : node.kind === 'place' ? t('库所') : t('转换')}: ${text.name} — ${node.id}`);
    if (flow || node.kind === 'transition') {
      const width = layout.nodes.get(node.id).width;
      cell.attr('eyebrow/text', flow ? text.type : t('转换'));
      // Human title and exact ID are separate. Technical IDs never get translated.
      cell.attr('title/text', wrapLabel(text.name, Math.floor((width - 32) / 8), 2));
      cell.attr('subtitle/text', compact ? wrapLabel(text.description, Math.floor((width-32)/7), 2) : wrapLabel(flow ? text.description : `${t('节点 ID')}: ${node.id}`, Math.floor((width - 32) / 6.5), 2));
    } else {
      const key = text.role === 'entry' ? '入口库所' : text.role === 'exit' ? '输出库所' : node.category === 'resource' ? '资源库所' : '库所';
      cell.attr('title/text', `${wrapLabel(text.name, 26, 1)}\n${wrapLabel(node.id, 26, 1)}`);
      cell.attr('status/text', t(key));
    }
  }
  for (const edge of snapshot.edges) {
    const cell = renderer.graph.getCell(edgeKey(edge.id));
    if (!cell) continue;
    let label = edge.agent_relation ? '' : edge.display_label ?? arcLabel(edge);
    if (compact && edge.handoff_token_count > 0) label = `${label || t('交接')} · ${t('{0} 个 token',edge.handoff_token_count)}`;
    const count = Number.isInteger(edge.active_token_count) ? t('{0} 个 token', edge.active_token_count) : t('token 未提供');
    cell.label(0, { attrs: { text: { text: !compact && edge.hidden_place ? `${label} · ${count}` : wrapLabel(label, compact ? 24 : 34, 1) } } });
    cell.attr('root/aria-label', edge.bundle ? t('包含 {0} 条原始弧，点击展开', edge.source_ids.length) : arcLabel(edge));
  }
}

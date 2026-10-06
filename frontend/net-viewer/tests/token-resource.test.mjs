import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeFrame } from '../../../cpn/frontend/static/dashboard-model.mjs';
import { renderInspector } from '../../../cpn/frontend/static/panels.mjs';
import { setLanguage } from '../../../cpn/frontend/static/i18n.mjs';
import { EN } from '../../../cpn/frontend/static/messages.mjs';

// Synthetic boundary cases through the real panel. This minimal DOM is not a
// browser, a Core run, or evidence of multiple retained token occurrences.
class Node {
    constructor(tag) { this.tagName = tag; this.children = []; this.text = ''; this.open = false; }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map(n => n.textContent).join(''); }
    set innerHTML(_) { throw new Error('HTML assignment is forbidden'); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.text = ''; this.children = children; }
}
const originalDocument = globalThis.document;
afterEach(() => { setLanguage('en', null); globalThis.document = originalDocument; });
const walk = node => [node, ...node.children.flatMap(walk)];
const refs = node => walk(node).filter(n => n.tagName === 'pre').map(n => JSON.parse(n.textContent));
const cards = node => node.children.filter(n => n.className === 'info-card');
const resource = (logical = 'a', version = 'b') => ({ resource_id: 'resource:' + logical.repeat(32), resource_version_id: 'resource_version:' + version.repeat(32) });
const token = (id, ref = resource()) => ({ token_ref: { entity_type: 'petri_token/v1', logical_id: 'token-' + id, version_id: 'token-version-' + id },
    place: 'p', kind: null, active_in_checkpoint: true, resource_ref: ref });
function frame(tokens, mode = 'live') {
    const source = { mode: 'registry_current', verified_head_ordinal: 10 };
    return normalizeFrame({ schema_version: 'rpnh/dashboard/v1', source, position: { mode, cursor: 10, latest_head: 10 },
        net: { schema_version: 'rpnh/net_view/v1', source: { ...source }, edges: [],
            nodes: [{ id: 'p', label: 'Same resource name', kind: 'place', category: 'place', tokens }] } });
}
function render(value, target = new Node('section')) {
    globalThis.document = { createElement: tag => new Node(tag) };
    renderInspector(target, value, { kind: 'node', id: 'p' }, 'executions');
    return target;
}
function freeze(value) {
    if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
    return value;
}

for (const language of ['en', 'zh-CN']) {
    for (const mode of ['live', 'history']) {
        test(`full token resource IDs render verbatim in ${language} ${mode}`, () => {
            setLanguage(language, null);
            const expected = resource(), value = freeze(frame([token('one', expected)], mode));
            const before = JSON.stringify(value), target = render(value);
            assert.deepEqual(refs(target), [expected]);
            assert.ok(target.textContent.includes(expected.resource_id));
            assert.ok(target.textContent.includes(expected.resource_version_id));
            assert.ok(target.textContent.includes(language === 'en' ? 'Token resource reference (full JSON)' : 'token 资源引用（完整 JSON）'));
            assert.equal(walk(target).find(n => n.tagName === 'details').open, true);
            assert.equal(JSON.stringify(value), before);
        });
    }
    test(`explicit null and legacy missing fields remain distinct in ${language}`, () => {
        setLanguage(language, null);
        const missing = token('missing'); delete missing.resource_ref;
        const value = freeze(frame([token('null', null), missing])), before = JSON.stringify(value);
        const target = render(value), boxes = cards(target);
        assert.equal(boxes.length, 2);
        assert.match(boxes[0].textContent, /resource_ref: null/);
        assert.ok(boxes[1].textContent.includes(language === 'en' ? 'does not provide' : '未提供'));
        assert.doesNotMatch(boxes[1].textContent, /resource_ref: null/);
        assert.deepEqual(refs(target), []);
        assert.equal(JSON.stringify(value), before);
    });
}

test('each token retains its own full IDs without merging versions, IDs, names or duplicate refs', () => {
    const expected = [resource('a', 'b'), resource('a', 'c'), resource('d', 'c'), resource('d', 'c')];
    const value = freeze(frame(expected.map((ref, i) => token(i, ref))));
    const target = render(value), boxes = cards(target);
    assert.equal(boxes.length, 4);
    assert.deepEqual(refs(target), expected);
    boxes.forEach((box, i) => {
        assert.ok(box.textContent.includes('token-version-' + i));
        assert.deepEqual(refs(box), [expected[i]]);
    });
});

test('resource references remain plain text without generated links, translation or HTML', () => {
    const expected = { resource_id: '未提供 <img src=x onerror=alert(1)>', resource_version_id: '<script>throw 1</script> {0}' };
    for (const language of ['en', 'zh-CN']) {
        setLanguage(language, null);
        const target = render(freeze(frame([token('untrusted', expected)])));
        assert.deepEqual(refs(target), [expected]);
        assert.equal(walk(target).filter(n => ['a', 'img', 'script'].includes(n.tagName)).length, 0);
    }
});

test('rendering never substitutes work resources, names, grants or delivery fields', () => {
    const missing = token('missing'); delete missing.resource_ref;
    const tokens = [token('exact'), token('null', null), missing].map(t => ({ ...t,
        work_resource_ref: resource('e', 'f'), name: 'not-a-reference', resource_body: 'private-body',
        grant: 'not-grant-evidence', delivery: 'not-delivery-evidence' }));
    const target = render(freeze(frame(tokens)));
    assert.deepEqual(refs(target), [resource()]);
    for (const hidden of ['resource:' + 'e'.repeat(32), 'private-body', 'not-a-reference', 'not-grant-evidence', 'not-delivery-evidence'])
        assert.ok(!target.textContent.includes(hidden));
});

test('legacy net-view responses still show token cards and mark absent resource fields', () => {
    const old = token('legacy'); delete old.resource_ref;
    const net = frame([old]).net, before = JSON.stringify(net);
    const target = render(freeze(normalizeFrame(net)));
    assert.equal(cards(target).length, 1);
    assert.match(target.textContent, /token-version-legacy/);
    assert.match(target.textContent, /does not provide/);
    assert.equal(JSON.stringify(net), before);
});

test('absent and empty token lists keep their existing distinct messages', () => {
    const absent = frame(undefined); delete absent.net.nodes[0].tokens;
    assert.match(render(freeze(absent)).textContent, /does not provide token references/);
    assert.match(render(freeze(frame([]))).textContent, /no tokens in the selected checkpoint/);
});

test('rerender clears an earlier exact reference when the selected frame is null, missing or empty', () => {
    const target = render(frame([token('one')]));
    assert.deepEqual(refs(target), [resource()]);
    render(frame([token('one', null)], 'history'), target);
    assert.deepEqual(refs(target), []); assert.match(target.textContent, /resource_ref: null/);
    const old = token('one'); delete old.resource_ref;
    render(frame([old]), target);
    assert.deepEqual(refs(target), []); assert.doesNotMatch(target.textContent, /resource_ref: null/);
    render(frame([]), target);
    assert.equal(cards(target).length, 0); assert.doesNotMatch(target.textContent, /token-version-one/);
});

test('token resource captions have aligned English and Chinese catalog entries', () => {
    for (const key of ['token 资源引用（完整 JSON）', '此响应未提供此 token 的资源引用字段。', '此 token 记录没有资源引用（resource_ref: null）。']) {
        assert.ok(Object.hasOwn(EN, key)); assert.ok(EN[key].trim());
        assert.doesNotMatch(EN[key], /[\u3400-\u9fff]/);
    }
});

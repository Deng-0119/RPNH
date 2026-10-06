/** Shared finite DTO checks; browser callers supply genuine DOM containers. */
export function exerciseConsumers(api, v1, v2, container = null) {
    const check = (ok, message) => { if (!ok) throw new Error(message); };
    const saved = JSON.stringify([v1, v2]);
    check(v1.schema_version === 'rpnh/workset_view/v1', 'actual v1');
    check(v2.schema_version === 'rpnh/workset_view/v2', 'actual v2');
    for (const view of [v1, v2]) {
        check(JSON.stringify(api.normalizeWorksets(view)) === JSON.stringify(view), 'exact DTO');
        check(view.current[0].state === 'completed', 'actual completed workset');
        if (container) {
            api.renderWorksets(container, view, 'en');
            check(container.querySelectorAll('section.info-card').length === view.current.length, 'native cards');
            check(container.querySelectorAll('ol > li').length === view.history.length, 'native history');
            check(container.textContent.includes(view.source_id), 'source rendered');
        }
    }
    const closure = v2.current[0].root_child_closure;
    check(closure && closure.verified_at_cut === v2.capture_cut, 'real closure cut');
    check(v2.current[0].required_child_seal_ref === null, 'legacy seal field untouched');
    check(v2.current[0].root_terminal_evidence_ref === null, 'not T01 run terminal');
    if (container) {
        check(container.textContent.includes(closure.seal_ref.ref.version_id), 'exact seal rendered');
        check(container.textContent.includes('execution-v1-normal-only'), 'profile rendered');
        check(container.textContent.includes(`Verified at cut: ${v2.capture_cut}`), 'cut rendered');
    }
    const cases = [
        ['root-binding', x => {x.current[0].root_child_closure.root_terminal_ref.ref.version_id = x.current[0].workset_ref.ref.version_id;}],
        ['seal-type', x => {x.current[0].root_child_closure.seal_ref.ref.entity_type = 'execution_children_sealed/v1';}],
        ['seal-source', x => {x.current[0].root_child_closure.seal_ref.source_id = v1.source_id;}],
        ['cut-binding', x => {x.current[0].root_child_closure.verified_at_cut--;}],
        ['missing-closure', x => {delete x.current[0].root_child_closure;}],
        ['v1-with-v2-root', x => {x.schema_version = 'rpnh/workset_view/v1'; delete x.current[0].root_child_closure;}],
    ];
    const rejected = [];
    for (const [name, mutate] of cases) {
        const value = structuredClone(v2); mutate(value);
        const domBefore = container?.innerHTML;
        let error;
        try { container ? api.renderWorksets(container, value, 'en') : api.normalizeWorksets(value); }
        catch (e) { error = e; }
        check(error?.message === 'Invalid or inconsistent Workset observation', `${name} refusal`);
        if (container) check(container.innerHTML === domBefore, `${name} DOM preserved`);
        rejected.push({name, message: error.message});
    }
    check(JSON.stringify([v1, v2]) === saved, 'input DTO unchanged');
    return {positive_views: 2, rejected, native_dom: Boolean(container), root: v2.current[0].root_terminal_ref,
        seal: closure.seal_ref, cut: v2.capture_cut};
}

"""Pure trusted-HOST grouping expansion; never authority or source discovery."""
from .registry_read_contracts import RegistryReadSessionError

MAX_HIERARCHY_DEPTH = 32
MAX_HIERARCHY_NODES = 1024
MAX_HIERARCHY_FANOUT = 1024


def _invalid():
    return RegistryReadSessionError('INVALID_HOST_CONFIGURATION')


def _names(value, *, nonempty=False):
    if (type(value) is not list or len(value) > MAX_HIERARCHY_FANOUT
            or nonempty and not value
            or any(type(name) is not str or not name or '\x00' in name for name in value)
            or len(set(value)) != len(value)):
        raise _invalid()
    return value


def expand_host_hierarchy(hierarchy, selections):
    """Stable sources-before-children DFS; exact configured leaves only.

    Every root-to-leaf path obeys the depth bound, even when a DAG node was
    reached earlier via a shorter path. Repeated cross-node leaves are emitted
    once; malformed/unreachable configuration is rejected rather than ignored.
    """
    if type(hierarchy) is not dict or set(hierarchy) != {'roots', 'nodes'}:
        raise _invalid()
    roots = _names(hierarchy['roots'], nonempty=True)
    nodes = hierarchy['nodes']
    if (type(nodes) is not dict or not 1 <= len(nodes) <= MAX_HIERARCHY_NODES
            or any(type(name) is not str or not name or '\x00' in name for name in nodes)):
        raise _invalid()
    by_source = {}
    for selection in selections:
        source = selection.source_ref.source_id
        if source in by_source:
            raise _invalid()
        by_source[source] = selection
    for node in nodes.values():
        if type(node) is not dict or set(node) != {'sources', 'children'}:
            raise _invalid()
        _names(node['sources'])
        _names(node['children'])
        if any(source not in by_source for source in node['sources']):
            raise _invalid()
        if any(child not in nodes for child in node['children']):
            raise _invalid()
    if any(root not in nodes for root in roots):
        raise _invalid()
    active, heights = set(), {}
    def height(name, depth):
        if depth > MAX_HIERARCHY_DEPTH or name in active:
            raise _invalid()
        if name in heights:
            if depth + heights[name] - 1 > MAX_HIERARCHY_DEPTH:
                raise _invalid()
            return heights[name]
        active.add(name)
        result = 1 + max((height(child, depth + 1) for child in nodes[name]['children']), default=0)
        active.remove(name)
        if depth + result - 1 > MAX_HIERARCHY_DEPTH:
            raise _invalid()
        heights[name] = result
        return result
    for root in roots:
        height(root, 1)
    if set(heights) != set(nodes):
        raise _invalid()
    visited, emitted, result = set(), set(), []
    def emit(name):
        if name in visited:
            return
        visited.add(name)
        for source in nodes[name]['sources']:
            if source not in emitted:
                emitted.add(source)
                result.append(by_source[source])
        for child in nodes[name]['children']:
            emit(child)
    for root in roots:
        emit(root)
    if emitted != set(by_source):
        raise _invalid()
    return tuple(result)

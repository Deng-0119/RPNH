"""Read-only command line for inspecting a generic RPNH run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class CLIUserError(ValueError):
    """One invalid command or unreadable run projection."""


def _json_text(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False,
        indent=2, sort_keys=True) + "\n"


def _add_net_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run", required=True, type=Path,
        help="inspect the registered Petri net in an existing generic run")
    parser.add_argument("--show-resources", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cpn", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    net = commands.add_parser("net", help="read-only Petri-net inspection")
    net_commands = net.add_subparsers(dest="net_command", required=True)

    show = net_commands.add_parser("show", help="print one run projection")
    _add_net_arguments(show)
    show.add_argument("--format", choices=("text", "json"), default="text")
    show.add_argument("--resources-only", action="store_true")
    show.add_argument("--node", metavar="ID")
    show.add_argument("--output", type=Path)
    show.set_defaults(handler=_cmd_net_show)

    view = net_commands.add_parser("view", help="serve the interactive run view")
    _add_net_arguments(view)
    view.add_argument("--no-open", action="store_true")
    view.add_argument("--host", default="127.0.0.1")
    view.add_argument("--port", type=int, default=0)
    view.set_defaults(handler=_cmd_net_view)
    return parser


def _load_net_projection(run_dir: Path) -> dict[str, Any]:
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.rpnh.inspection import project_registry_net
    try:
        return project_registry_net(
            run_dir.resolve(), catalog=agent_task_catalog())
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise CLIUserError(str(exc)) from exc


def _projection_summary(
        nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "node_count": len(nodes),
        "transition_count": sum(node["kind"] == "transition" for node in nodes),
        "place_count": sum(node["kind"] == "place" for node in nodes),
        "resource_place_count": sum(
            node["category"] == "resource" for node in nodes),
        "edge_count": len(edges),
        "resource_edge_count": sum(bool(edge.get("resource")) for edge in edges),
    }


def _filter_projection(
        projection: Mapping[str, Any], *, show_resources: bool,
        resources_only: bool, node_id: str | None,
) -> dict[str, Any]:
    full_nodes = {node["id"]: dict(node) for node in projection["nodes"]}
    full_edges = [dict(edge) for edge in projection["edges"]]
    if resources_only:
        edges = [edge for edge in full_edges if edge.get("resource")]
        selected = {
            endpoint for edge in edges
            for endpoint in (edge["source"], edge["target"])
        }
        selected.update(
            current_id for current_id, node in full_nodes.items()
            if node["category"] == "resource")
    elif show_resources:
        edges = full_edges
        selected = set(full_nodes)
    else:
        selected = {
            current_id for current_id, node in full_nodes.items()
            if not node["hidden_by_default"]
        }
        edges = [
            edge for edge in full_edges
            if not edge["hidden_by_default"]
            and edge["source"] in selected and edge["target"] in selected
        ]

    if node_id is not None:
        if node_id not in full_nodes:
            raise CLIUserError(f"unknown node ID: {node_id}")
        if node_id not in selected:
            raise CLIUserError(
                f"node is hidden by the selected resource view: {node_id}")
        edges = [
            edge for edge in edges
            if node_id in (edge["source"], edge["target"])
        ]
        selected = {node_id}
        selected.update(
            endpoint for edge in edges
            for endpoint in (edge["source"], edge["target"])
        )
    nodes = [full_nodes[key] for key in sorted(selected)]
    edges.sort(key=lambda edge: edge["id"])
    result = dict(projection)
    result["nodes"] = nodes
    result["edges"] = edges
    result["summary"] = _projection_summary(nodes, edges)
    return result


def _projection_text(projection: Mapping[str, Any]) -> str:
    lines = [
        f"schema_version: {projection['schema_version']}",
        "source: " + json.dumps(
            projection["source"], ensure_ascii=False, sort_keys=True),
        "summary: " + json.dumps(
            projection["summary"], ensure_ascii=False, sort_keys=True),
        "nodes:",
    ]
    for node in projection["nodes"]:
        detail = node.get("operation", node.get("token_kind", ""))
        capability = node.get("capability", {})
        if capability:
            detail += f" capability={capability['selector']} version={capability['plugin_version']}"
        runtime = node.get("runtime")
        if runtime is not None:
            detail += f" observed={runtime['status']} firings={runtime['firing_count']}"
        lines.append(
            f"  {node['id']} [{node['kind']}/{node['category']}] {detail}")
    lines.append("edges:")
    for edge in projection["edges"]:
        outcome = "" if edge["outcome"] is None else f" outcome={edge['outcome']}"
        lines.append(
            f"  {edge['id']}: {edge['source']} -> {edge['target']} "
            f"[{edge['kind']}/{edge['mode']}] weight={edge['weight']}{outcome}")
    return "\n".join(lines) + "\n"


def _cmd_net_show(args: argparse.Namespace) -> int:
    projection = _filter_projection(
        _load_net_projection(args.run),
        show_resources=args.show_resources,
        resources_only=args.resources_only,
        node_id=args.node,
    )
    rendered = (
        _json_text(projection) if args.format == "json"
        else _projection_text(projection)
    )
    if args.output is None:
        print(rendered, end="")
    else:
        try:
            args.output.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            raise CLIUserError(str(exc)) from exc
    return 0


def _cmd_net_view(args: argparse.Namespace) -> int:
    if isinstance(args.port, bool) or not 0 <= args.port <= 65535:
        raise CLIUserError("--port must be within 0..65535")
    from cpn.frontend.dashboard import RegistryDashboard
    from cpn.rpnh.agent_tasks import agent_task_catalog
    provider = RegistryDashboard(Path(args.run), catalog=agent_task_catalog())
    from cpn.frontend.server import serve_projection
    serve_projection(
        provider, host=args.host, port=args.port,
        open_browser=not args.no_open, show_resources=args.show_resources)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except CLIUserError as exc:
        parser.error(str(exc))
    raise AssertionError("argparse.error must terminate")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("CLIUserError", "_filter_projection", "build_parser", "main")

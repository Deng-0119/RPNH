"""Source-role projection into the actual final graph fragments, never a lowerer.

Internal source ports/arcs have field anchors, not invented Module elements.
Every primitive is matched to source semantics; an aggregate pointer cannot
satisfy coverage. Array pointers identify only this exact compiled resource.
"""
from __future__ import annotations

from ..agent_workflows import (
    AgentWorkflowGraph, TEXT_SCHEMA, _graph_io, _graph_activation_ports,
    _graph_output_route, _node_input_handle, _node_output_handle, _data_place,
    _control_place, _rework_permit_place, _rework_operation, _interrupt_handle,
)
from ..registry.schema_catalog import canonical_json, canonical_text
from ._assembly_lowering import prefix, _pointer as ptr, resolve_pointer
from .assembly_v2 import ORIGIN_CONTRACT
from .graph_authoring import ValidatedGraphRevision
from .graph_source import GRAPH_BUILDER, _derived_identity, graph_source_elements

GRAPH_ROLES = (
    "component", "activation", "input", "interrupt", "output", "edge_control",
    "edge_source_data", "edge_source_control", "edge_target_data", "edge_target_control",
    "edge_interrupt_data", "edge_interrupt_control", "permit", "permit_seed", "permit_consume",
    "ingress", "egress",
)
_PRIMITIVES = ("places", "transitions", "arcs", "ports", "operations", "internal_ports", "internal_bindings")
_UNSUPPORTED = ("lease_identities", "lease_pools", "variable_resource_arcs", "logical_slots", "reset_arcs")


def _equal(left, right, message):
    if canonical_json(left) != canonical_json(right):
        raise ValueError(message)


class _Inventory:
    def __init__(self, component, document):
        self.component, self.document = component, document
        self.fragment = document["fragments"][component]
        if any(self.fragment[field] for field in _UNSUPPORTED):
            raise ValueError("ordinary graph has unsupported fragment topology")
        self.coverage = {ptr("fragments", component, field, i): set()
            for field in _PRIMITIVES for i in range(len(self.fragment[field]))}
        self.index = {field: {item["name"]: i for i, item in enumerate(self.fragment[field])}
            for field in _PRIMITIVES if field != "arcs"}
        self.symbolic = document["symbolic"]
        self.final = {field: {item["name"]: i for i, item in enumerate(self.symbolic[field])}
            for field in ("places", "transitions", "operations")}
        self.arc_index = {self.arc_key(a): i for i, a in enumerate(self.fragment["arcs"])}
        self.final_arcs = {self.arc_key(a): i for i, a in enumerate(self.symbolic["arcs"])}
        if len(self.arc_index) != len(self.fragment["arcs"]):
            raise ValueError("ordinary graph local arcs must be unique")
        self.ports = {p["name"]: i for i, p in enumerate(document["ports"])}

    @staticmethod
    def arc_key(arc):
        return arc["place"], arc["transition"], arc["direction"], arc["outcome"]

    def q(self, name):
        return self.component + "." + name

    def p(self, name):
        return self.document["place_aliases"][self.q(name)]

    def add(self, row, role, field, index, targets):
        pointer = ptr("fragments", self.component, field, index)
        if pointer not in self.coverage or role not in GRAPH_ROLES:
            raise ValueError("origin role does not identify a graph primitive")
        for target in targets:
            resolve_pointer(self.document, target)
        row["compiled_roles"].append({"role": role, "fragment_pointer": pointer,
                                      "compiled_targets": sorted(set(targets))})
        self.coverage[pointer].add((row["source_element_id"], role))

    def place(self, row, role, name, *, control=False):
        i = self.index["places"][name]
        actual = self.fragment["places"][i]
        _equal(actual, {"name": name, "schema": TEXT_SCHEMA,
            "channel": "control" if control else "data", "capacity": None,
            "token_kind": "data", "schema_variants": [], "colours": [],
            "initial_tokens": [], "reusable": False}, "ordinary graph place role differs")
        final = self.final["places"][self.p(name)]
        _equal(self.symbolic["places"][final], {**actual, "name": self.p(name)}, "actual fusion place differs")
        self.add(row, role, "places", i, [ptr("place_aliases", self.q(name)), ptr("symbolic", "places", final)])

    def port(self, row, role, name, place, direction):
        public = name in self.index["ports"]
        field = "ports" if public else "internal_bindings"
        i = self.index[field][name]
        _equal(self.fragment[field][i], {"name": name, "place": place}, "source port carrier differs")
        q = self.q(name)
        if self.symbolic["port_places"][q] != self.p(place):
            raise ValueError("source port final fusion representative differs")
        targets = [ptr("ports", self.ports[q]), ptr("port_handles", q),
            ptr("symbolic", "port_places", q), ptr("symbolic", "places", self.final["places"][self.p(place)])]
        actual = self.document["ports"][self.ports[q]]
        if actual["direction"] != direction or actual["schema"] != TEXT_SCHEMA or actual["public"] != public:
            raise ValueError("source port actual compiled role differs")
        self.add(row, role, field, i, targets)
        if not public:
            i = self.index["internal_ports"][name]
            _equal(self.fragment["internal_ports"][i], {"name": name, "direction": direction,
                "schema": TEXT_SCHEMA, "channel": "data", "cardinality": 1,
                "cardinality_minimum": None, "cardinality_maximum": None}, "ordinary graph internal port differs")
            self.add(row, role, "internal_ports", i, targets)

    def operation(self, row, name):
        q = self.q(name)
        i = self.index["operations"][name]
        self.add(row, "activation", "operations", i,
            [ptr("operation_handles", q), ptr("symbolic", "operations", self.final["operations"][q])])
        i = self.index["transitions"][name]
        _equal(self.fragment["transitions"][i], {"name": name, "operation": name,
            "count_guards": [], "input_verdicts": []}, "ordinary activation transition differs")
        final = self.final["transitions"][q]
        _equal(self.symbolic["transitions"][final], {"name": q, "operation": q,
            "count_guards": [], "input_verdicts": []}, "qualified activation differs")
        self.add(row, "activation", "transitions", i, [ptr("symbolic", "transitions", final)])

    def arc(self, row, role, place, operation, direction, *, outcome=None, weight=1, emit="produced", forward=False):
        i = self.arc_index[place, operation, direction, outcome]
        a = self.fragment["arcs"][i]
        _equal(a, {"place": place, "transition": operation, "direction": direction,
            "weight": weight, "mode": "consume" if direction == "input" else "produce",
            "outcome": outcome, "emit": emit, "forward_source": place if forward else None,
            "colour_expression": None, "output_predicates": [], "lease_claims": [],
            "lease_claim_exclusions": [], "lease_claim_set": None, "effect_selector": None},
            "ordinary graph arc semantic role differs")
        expected = {**a, "place": self.p(place), "transition": self.q(operation),
                    "forward_source": self.p(place) if forward else None}
        final = self.final_arcs[self.arc_key(expected)]
        _equal(self.symbolic["arcs"][final], expected, "actual qualified/fused graph arc differs")
        self.add(row, role, "arcs", i, [ptr("symbolic", "arcs", final)])

    def finish(self, member_id):
        if any(not sources for sources in self.coverage.values()):
            raise ValueError("ordinary graph primitive has no specific source role")
        return {"member_id": member_id, "component": self.component, "elements": [
            {"fragment_pointer": pointer, "origins": [{"source_element_id": identity, "role": role}
                for identity, role in sorted(sources)]} for pointer, sources in sorted(self.coverage.items())]}


def graph_origins(plan, members, module, compiled, declaration_map):
    document, declaration = compiled.to_dict(), module.to_dict()
    _equal(document["source"], declaration, "origin map requires the exact compiled Module pair")
    source_rows, graph_members, coverage = [], [], []
    origins = {(r["member_id"], r["element_id"]): r for r in declaration_map["origins"]}
    for member_spec in plan["members"]:
        identity = member_spec["member_id"]
        member = members[identity]
        if not isinstance(member, ValidatedGraphRevision):
            continue
        source, recipe = member.source, member.recipe
        graph = AgentWorkflowGraph.from_mapping(source["graph"])
        producer, outgoing = _graph_io(graph)
        component = prefix(identity) + "_team"
        inventory = _Inventory(component, document)
        ci = next(i for i, c in enumerate(declaration["components"]) if c["name"] == component)
        cd = declaration["components"][ci]
        base = ("components", ci)
        cfg = (*base, "config")
        nodes = {n.node_id: n for n in graph.nodes}
        node_indices = {n["node_id"]: i for i,n in enumerate(cd["config"]["nodes"])}
        operation_indices = {o["name"]: i for i,o in enumerate(cd["operations"])}
        arc_indices = {a["arc_id"]: i for i,a in enumerate(cd["config"]["arcs"])}
        ingress = (graph.ingress.node_id, graph.ingress.port_id)
        ingress_place = f"ingress__{ingress[0]}__{ingress[1]}"
        variants = {}
        for node in graph.nodes:
            initial, feedback = _graph_activation_ports(graph, node, producer)
            variants[node.node_id] = [(node.node_id, initial)] + ([( _rework_operation(node.node_id), feedback)] if feedback else [])
        expected_sources = graph_source_elements(source)
        supplied = member.source_map["elements"]
        if ({r["locator"]: r["kind"] for r in supplied} != expected_sources
                or len(supplied) != len(expected_sources)
                or len({r["element_id"] for r in supplied}) != len(supplied)):
            raise ValueError("graph source origins require exact unique source coverage")
        rows = {}
        for src in member.source_map["elements"]:
            kind = src["kind"]
            roles = {"graph": ("module", "component"), "ingress": ("public_port", "entry"),
                "egress": ("public_port", "exit", "terminal"), "node": ("operation:initial", "operation:feedback")}.get(kind, ())
            mapped = [origins[identity, _derived_identity(src["element_id"], role)] for role in roles
                      if (identity, _derived_identity(src["element_id"], role)) in origins]
            row = {"member_id": identity, "revision_ref": member.revision.revision_ref.to_dict(),
                "source_element_id": src["element_id"], "source_kind": kind, "source_locator": src["locator"],
                "copied_from": src["copied_from"],
                "representation": "derived_declaration" if roles else "field_and_lowering_role",
                "declaration_locators": sorted({p for r in mapped for p in r["declaration_locators"]}),
                "declaration_field_targets": [], "compiled_roles": [],
                "dispositions": sorted({r["disposition"] for r in mapped})}
            rows[src["locator"]] = row
        root = rows["/"]
        root["declaration_field_targets"] += [ptr(*cfg)]
        constraint_index = next(i for i,r in enumerate(declaration["designer_constraints"]["assembly_member_constraints"]["members"]) if r["member_id"] == identity)
        root["declaration_field_targets"].append(ptr("designer_constraints","assembly_member_constraints","members",constraint_index,"constraints"))
        # The component aggregate is useful context, but never primitive coverage.
        root["compiled_roles"].append({"role": "component", "fragment_pointer": ptr("fragments",component),
            "compiled_targets": [ptr("source",*base)]})
        inventory.place(rows["/ingress"], "ingress", ingress_place)
        inventory.port(rows["/ingress"], "ingress", "request", ingress_place, "input")
        inventory.port(rows["/egress"], "egress", "result", _data_place(graph.egress.node_id,graph.egress.port_id), "output")
        for side in ("ingress","egress"):
            rows['/'+side]["declaration_field_targets"].append(ptr(*cfg,side))
        for node in graph.nodes:
            nr = rows[f"/nodes/{node.node_id}"]
            ni = node_indices[node.node_id]
            nr["declaration_field_targets"].append(ptr(*cfg,"nodes",ni))
            for operation, selected in variants[node.node_id]:
                inventory.operation(nr, operation)
                oi = operation_indices[operation]
                nr["declaration_field_targets"] += [ptr(*base,"operations",oi,field) for field in ("config","tools","budget_binding")]
                bucket = cd["operations"][oi]["budget_binding"]["bucket_id"]
                bi = next(i for i,b in enumerate(declaration["budget_buckets"]) if b["bucket_id"] == bucket)
                nr["declaration_field_targets"].append(ptr("budget_buckets",bi))
                for p in selected:
                    row = rows[f"/nodes/{node.node_id}/input_ports/{p.port_id}"]
                    edge = producer.get((node.node_id,p.port_id))
                    place = ingress_place if edge is None else _data_place(edge.source.node_id,edge.source.port_id)
                    handle = _node_input_handle(graph,node.node_id,p.port_id)
                    inventory.port(row,"input",handle,place,"input")
                    inventory.arc(row,"input",place,operation,"input")
                    interrupt = _interrupt_handle(node.node_id,p.port_id,operation)
                    inventory.port(row,"interrupt",interrupt,place,"output")
                    inventory.arc(row,"interrupt",place,operation,"output",outcome="interrupted",emit="forward",forward=True)
                    ii = cd["operations"][oi]["inputs"].index(handle)
                    row["declaration_field_targets"].append(ptr(*base,"operations",oi,"inputs",ii))
                    if ii == 0:
                        row["declaration_field_targets"].append(ptr(*base,"operations",oi,"request_port"))
                for p in node.output_ports:
                    row = rows[f"/nodes/{node.node_id}/output_ports/{p.port_id}"]
                    place, handle = _data_place(node.node_id,p.port_id), _node_output_handle(graph,node.node_id,p.port_id)
                    consumers = outgoing[node.node_id,p.port_id]
                    inventory.place(row,"output",place)
                    inventory.port(row,"output",handle,place,"output")
                    inventory.arc(row,"output",place,operation,"output",outcome=_graph_output_route(consumers),weight=max(1,len(consumers)))
                    row["declaration_field_targets"].append(ptr(*base,"operations",oi,"outputs",cd["operations"][oi]["outputs"].index(handle)))
                    for xi,x in enumerate(cd["operations"][oi]["outcomes"]):
                        for pi,product in enumerate(x["products"]):
                            if product["port"] == handle:
                                row["declaration_field_targets"].append(ptr(*base,"operations",oi,"outcomes",xi,"products",pi))
            for direction in ("input","output"):
                for pi,p in enumerate(cd["config"]["nodes"][ni][direction+"_ports"]):
                    rows[f"/nodes/{node.node_id}/{direction}_ports/{p['port_id']}"]["declaration_field_targets"].append(ptr(*cfg,"nodes",ni,direction+"_ports",pi))
        for edge in graph.arcs:
            row=rows['/arcs/'+edge.arc_id]
            row["declaration_field_targets"].append(ptr(*cfg,"arcs",arc_indices[edge.arc_id]))
            control=_control_place(edge.arc_id);data=_data_place(edge.source.node_id,edge.source.port_id)
            inventory.place(row,"edge_control",control,control=True)
            consumers=outgoing[edge.source.node_id,edge.source.port_id];route=_graph_output_route(consumers)
            for operation,_ in variants[edge.source.node_id]:
                inventory.arc(row,"edge_source_data",data,operation,"output",outcome=route,weight=max(1,len(consumers)))
                inventory.arc(row,"edge_source_control",control,operation,"output",outcome=route,emit="control_only")
            operation=edge.target.node_id if edge.kind=="dependency" else _rework_operation(edge.target.node_id)
            for place,role in ((data,"data"),(control,"control")):
                inventory.arc(row,"edge_target_"+role,place,operation,"input")
                inventory.arc(row,"edge_interrupt_"+role,place,operation,"output",outcome="interrupted",emit="forward",forward=True)
        if graph.max_rework_cycles:
            permit=_rework_permit_place();inventory.place(root,"permit",permit,control=True)
            for node in graph.nodes:
                if len(variants[node.node_id])==2:
                    inventory.arc(root,"permit_consume",permit,_rework_operation(node.node_id),"input")
            first=nodes[graph.ingress.node_id]
            for route in sorted({_graph_output_route(outgoing[first.node_id,p.port_id]) for p in first.output_ports}):
                inventory.arc(root,"permit_seed",permit,first.node_id,"output",outcome=route,weight=graph.max_rework_cycles,emit="control_only")
            bi=next(i for i,b in enumerate(declaration["budget_buckets"]) if b["bucket_id"]=="rpnh:workflow:rework")
            root["declaration_field_targets"].append(ptr("budget_buckets",bi))
        for row in rows.values():
            row["declaration_field_targets"] = sorted(set(row["declaration_field_targets"]))
            for pointer in row["declaration_field_targets"]: resolve_pointer(declaration,pointer)
            row["compiled_roles"] = [json_value for _,json_value in sorted({canonical_text(r): r for r in row["compiled_roles"]}.items())]
            if not row["compiled_roles"] or not row["declaration_field_targets"]:
                raise ValueError("graph source row lacks actual representation")
            source_rows.append(row)
        coverage.append(inventory.finish(identity))
        graph_members.append({"member_id": identity, "revision_ref": member.revision.revision_ref.to_dict(),
            "builder_contract": GRAPH_BUILDER, **{field: getattr(member.revision,field).to_dict() for field in
            ("graph_source_ref","graph_recipe_ref","graph_source_mapping_ref")}})
    return {"source_origin_contract": ORIGIN_CONTRACT, "graph_members": graph_members,
        "graph_source_origins": sorted(source_rows,key=lambda row:(row["member_id"],row["source_element_id"])),
        "graph_fragment_coverage": coverage}

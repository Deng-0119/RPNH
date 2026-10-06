"""Instance-path source roles projected against the actual root fragments."""
from ._graph_assembly_origins import (
    _Inventory, _equal, AgentWorkflowGraph, _graph_io, _graph_activation_ports,
    _graph_output_route, _node_input_handle, _node_output_handle, _data_place,
    _control_place, _rework_permit_place, _rework_operation, _interrupt_handle,
    ptr, resolve_pointer, canonical_text, GRAPH_BUILDER, _derived_identity, graph_source_elements,
)
from .assembly_v3 import ORIGIN_CONTRACT

def graph_origins(placements, module, compiled):
    document, declaration = compiled.to_dict(), module.to_dict()
    _equal(document["source"], declaration, "origin map requires the exact compiled Module pair")
    source_rows, graph_members, coverage = [], [], []
    for placement in placements:
        path, references, member = placement["member_path"], placement["revision_path"], placement["member"]
        identity = tuple(path)
        origins = {(identity, row["element_id"]): row for row in placement["origins"]}
        source, recipe = member.source, member.recipe
        graph = AgentWorkflowGraph.from_mapping(source["graph"])
        producer, outgoing = _graph_io(graph)
        component = placement["component"]
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
            row = {"member_path": path, "revision_path": references, "revision_ref": member.revision.revision_ref.to_dict(),
                "source_element_id": src["element_id"], "source_kind": kind, "source_locator": src["locator"],
                "copied_from": src["copied_from"],
                "representation": "derived_declaration" if roles else "field_and_lowering_role",
                "declaration_locators": sorted({p for r in mapped for p in r["declaration_locators"]}),
                "declaration_field_targets": [], "compiled_roles": [],
                "dispositions": sorted({d for r in mapped for d in r["dispositions"]})}
            rows[src["locator"]] = row
        root = rows["/"]
        root["declaration_field_targets"] += [ptr(*cfg)]
        root["declaration_field_targets"].append(placement["constraints_pointer"])
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
        coverage.append({"member_path": path, "revision_path": references,
            **{k: v for k, v in inventory.finish(identity).items() if k != "member_id"}})
        graph_members.append({"member_path": path, "revision_path": references, "revision_ref": member.revision.revision_ref.to_dict(),
            "builder_contract": GRAPH_BUILDER, **{field: getattr(member.revision,field).to_dict() for field in
            ("graph_source_ref","graph_recipe_ref","graph_source_mapping_ref")}})
    return {"source_origin_contract": ORIGIN_CONTRACT, "graph_members": graph_members,
        "graph_source_origins": sorted(source_rows,key=lambda row:(row["member_path"],row["source_element_id"])),
        "graph_fragment_coverage": coverage}

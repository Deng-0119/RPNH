"""V7 uses the reviewed pure direct composition and complete origin scanner."""
from ._assembly_v5_lowering import compose_plan_v5 as compose_plan_v7
from ._assembly_v5_lowering import lowering_map_v5


def lowering_map_v7(plan, members, module, compiled, assembly_identity):
    from .assembly_v7 import LOWERING_V7_SCHEMA, ORIGIN_CONTRACT
    mapping, ids = lowering_map_v5(plan, members, module, compiled, assembly_identity)
    mapping.update(schema_version=LOWERING_V7_SCHEMA, origin_contract=ORIGIN_CONTRACT)
    return mapping, ids

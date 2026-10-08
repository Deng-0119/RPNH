"""Read-only persisted Registry measurements in a separate process."""
import json
from pathlib import Path
import sys

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from examples.tool_pipeline.host import registration

catalog = SchemaCatalog()
registration().bind_schema_catalog(catalog)
core = _RegistryCore(Path(sys.argv[1]), create=False, read_only=True, catalog=catalog)
events = core.event_store.list_events()
print(json.dumps({
    'max_ordinal': core.event_store.max_ordinal(),
    'event_count': len(events),
    'dispatch_reservations': sum(e.event_type == 'operation_dispatch_reserved/v1' for e in events),
    'execution_starts': sum(e.event_type == 'operation_execution_started/v1' for e in events),
    'actual_model_call_counts': list(core.event_store.actual_model_call_counts()),
}, indent=2))

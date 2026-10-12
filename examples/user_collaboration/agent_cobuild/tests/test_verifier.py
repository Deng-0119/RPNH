from copy import deepcopy
import pytest
from examples.user_collaboration.agent_cobuild.verify import verify_result

INPUT = {"records": [{"id": "one", "category": "work", "minutes": 12},
                     {"id": "two", "category": "work", "minutes": 0}]}
REPORT = {"schema": "agent_cobuild_report/v1", "lineage_retained": True,
    "source_reconciliation_required": True,
    "totals": {"record_count": 2, "total_minutes": 12,
        "by_category": [{"category": "work", "record_count": 2, "total_minutes": 12}]},
    "source_reconciliation": [{"source_id": "one", "source_index": 0, "category": "work", "minutes": 12},
                              {"source_id": "two", "source_index": 1, "category": "work", "minutes": 0}],
    "count_checks": {"input_record_count": 2, "normalized_record_count": 2,
        "reconciled_source_count": 2, "reported_record_count": 2, "counts_match": True},
    "checks": {"input_count_matches": True, "record_count_matches": True,
        "category_totals_match": True, "total_minutes_match": True,
        "unique_source_ids": True, "source_indices_complete": True}}


def test_independent_result_check():
    assert verify_result(INPUT, REPORT)["status"] == "PASS"


@pytest.mark.parametrize("path,value", [
    (("source_reconciliation", 0, "source_index"), False),
    (("source_reconciliation", 1, "minutes"), False),
    (("source_reconciliation", 0, "minutes"), 12.0),
    (("count_checks", "input_record_count"), 2.0),
    (("count_checks", "counts_match"), 1),
    (("checks", "source_indices_complete"), 1),
    (("source_reconciliation", 1, "source_id"), "one"),
    (("totals", "total_minutes"), 13),
])
def test_rejects_changed_values_and_json_types(path, value):
    report = deepcopy(REPORT)
    slot = report
    for key in path[:-1]:
        slot = slot[key]
    slot[path[-1]] = value
    with pytest.raises(AssertionError):
        verify_result(INPUT, report)

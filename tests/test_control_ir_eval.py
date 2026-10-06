"""Finite expression and exact observation tests; no implicit source readers."""
import pytest

from cpn.rpnh.control_eval import evaluate_expression
from cpn.rpnh.control_types import ControlIRError, data, type_spec, typed_value, same_type


INT = {"kind": "Int"}
BOOL = {"kind": "Bool"}
RATIONAL = {"kind": "Rational"}
TEXT = {"kind": "Text"}


def lit(value, spec=INT):
    return {"op": "literal", "type": spec, "value": value}


def op(name, *args):
    return {"op": name, "args": list(args)}


def read(name, *path):
    return {"op": "read", "name": name, "path": list(path)}


def binding(value, spec=INT, *, version=1, stream=None, source="source-a"):
    origin = ({"kind": "head", "source": source, "stream": stream, "identity": "state", "version": version}
              if stream else {"kind": "immutable", "ref": {"schema": "application/author_input/v1",
                            "source": source, "identity": "input", "version": version}})
    return {"type": spec, "value": value, "origin": origin}


@pytest.mark.parametrize("expression,expected,spec", [
    (op("floor", lit("-3/2", RATIONAL)), -2, INT),
    (op("floor_div", lit(-3), lit(2)), -2, INT),
    (op("div", lit(3), lit(2)), "3/2", RATIONAL),
    (op("rational", lit("0.1", TEXT)), "1/10", RATIONAL),
    (op("count", lit([], {"kind": "Set", "item": INT})), 0, INT),
    (op("absence", lit([], {"kind": "Set", "item": INT})), True, BOOL),
    (op("member", lit(1), lit([2, 1], {"kind": "Set", "item": INT})), True, BOOL),
    (op("min", lit(["1/2", "1/3"], {"kind": "Sequence", "item": RATIONAL})), "1/3", RATIONAL),
    (op("max", lit(["1/2", "1/3"], {"kind": "Sequence", "item": RATIONAL})), "1/2", RATIONAL),
    (op("mul", lit(3, {"kind": "Int", "unit": {"bytes": 1}}), lit("1/2", RATIONAL)),
     "3/2", {"kind": "Rational", "unit": {"bytes": 1}}),
])
def test_finite_arithmetic_and_collection_semantics(expression, expected, spec):
    result = evaluate_expression(expression, {})
    assert result.error is None and result.value == expected and result.type == spec


@pytest.mark.parametrize("expression,code", [
    (op("div", lit(1), lit(0)), "division_by_zero"),
    (op("floor_div", lit(1), lit(0)), "division_by_zero"),
    (op("min", lit([], {"kind": "Sequence", "item": INT})), "empty_collection"),
    (op("max", lit([], {"kind": "Set", "item": INT})), "empty_collection"),
    (op("rational", lit("NaN", TEXT)), "invalid_rational"),
])
def test_evaluation_errors_are_not_false_or_unknown(expression, code):
    result = evaluate_expression(expression, {})
    assert result.value is None and result.error.code == code
    assert result.error.path.startswith("$.expression")


@pytest.mark.parametrize("bad,spec", [(True, INT), (1, BOOL), (1.0, INT), (False, RATIONAL),
                                      ({"tag": "unknown"}, {"kind": "Maybe", "item": INT})])
def test_bool_number_and_unknown_input_are_exact(bad, spec):
    with pytest.raises(ControlIRError):
        evaluate_expression(lit(bad, spec), {})


@pytest.mark.parametrize("name,first", [("and", False), ("or", True)])
def test_short_circuit_skips_missing_fields_and_read_heads(name, first):
    environment = {"skipped": binding({}, {"kind": "Record", "fields": {"flag": BOOL}}, stream="unread")}
    result = evaluate_expression(op(name, lit(first, BOOL), read("skipped", "flag")), environment)
    assert result.error is None and result.value is first and result.read_set.heads == ()
    executed = evaluate_expression(op(name, lit(not first, BOOL), read("skipped", "flag")), environment)
    assert executed.error.code == "missing_field" and len(executed.read_set.heads) == 1
    assert executed.error.binding_ref["stream"] == "unread"


def test_unknown_branches_before_known_value_or_pressure_reads():
    maybe = {"kind": "Maybe", "item": INT}
    expr = {"op": "match_known", "value": read("capacity"), "name": "c",
            "known": op("ge", read("usage"), {"op": "local", "name": "c"}),
            "unknown": lit(False, BOOL)}
    environment = {"capacity": binding({"tag": "unknown", "reason": "unmeasured"}, maybe, stream="capacity"),
                   "usage": binding(True, INT, stream="usage")}
    result = evaluate_expression(expr, environment)
    assert result.value is False and result.error is None
    assert [h["stream"] for h in result.read_set.heads] == ["capacity"]
    environment["capacity"]["value"] = {"tag": "known", "value": 10}
    result = evaluate_expression(expr, environment)
    assert result.error.code == "type_mismatch"
    assert len(result.read_set.heads) == 2


def test_read_set_compares_each_source_qualified_head_and_preserves_refs():
    environment = {"a": binding(1, stream="queue"), "b": binding(2, stream="children"),
                   "c": binding(3, source="source-b", stream="queue"), "immutable": binding(4)}
    result = evaluate_expression(op("add", op("add", read("a"), read("b")),
                                      op("add", read("c"), read("immutable"))), environment)
    assert result.value == 10 and len(result.read_set.heads) == 3 and len(result.read_set.immutable_refs) == 1
    current = [dict(head) for head in result.read_set.heads]
    assert result.read_set.stale_heads(current) == ()
    current[1]["version"] += 1
    assert result.read_set.stale_heads(current) == (result.read_set.heads[1],)
    assert len(result.read_set.stale_heads(current[:1])) == 2


def test_inconsistent_same_stream_snapshot_fails_instead_of_overwriting_head():
    result = evaluate_expression(op("add", read("a"), read("b")),
                                 {"a": binding(1, stream="queue"), "b": binding(2, version=2, stream="queue")})
    assert result.error.code == "inconsistent_snapshot"
    assert result.read_set.heads[0]["version"] == 1


def test_sort_is_stable_by_explicit_keys_and_rejects_nonunique_final_key():
    record = {"kind": "Record", "fields": {"score": RATIONAL, "id": TEXT}}
    expr = {"op": "sort", "value": read("items"), "keys": [["score"]], "tie_break": ["id"]}
    environment = {"items": binding([{"score": "1/2", "id": "b"}, {"score": "0.5", "id": "a"}],
                                     {"kind": "Sequence", "item": record})}
    result = evaluate_expression(expr, environment)
    assert [row["id"] for row in result.value] == ["a", "b"]
    environment["items"]["value"][0]["id"] = "a"
    assert evaluate_expression(expr, environment).error.code == "nonunique_sort_tie_break"


def test_exact_refs_carry_schema_source_identity_and_version():
    spec = {"kind": "ExactRef", "schema": "application/test/v1"}
    ref = {"schema": "application/test/v1", "source": "source-a", "identity": "same-id", "version": 1}
    assert typed_value(ref, spec) == ref
    with pytest.raises(ControlIRError, match="invalid_ref_version"):
        typed_value(dict(ref, version=True), spec)
    with pytest.raises(ControlIRError, match="ref_schema_mismatch"):
        typed_value(dict(ref, schema="application/test/v2"), spec)


@pytest.mark.parametrize("expression,code", [
    ({"op": "eval", "args": []}, "unknown_operator"),
    ({"op": "clock", "args": []}, "unknown_operator"),
    (op("add", lit(1), lit(1, {"kind": "Int", "unit": {"bytes": 1}})), "unit_mismatch"),
    (op("and", lit(1), lit(True, BOOL)), "bool_required"),
    (op("member", lit(True, BOOL), lit([1], {"kind": "Sequence", "item": INT})), "membership_type_mismatch"),
    (lit(["1/2", "0.5"], {"kind": "Set", "item": RATIONAL}), "duplicate_set_member"),
])
def test_unknown_and_ill_typed_constructs_rejected_before_evaluation(expression, code):
    with pytest.raises(ControlIRError) as caught:
        evaluate_expression(expression, {})
    assert caught.value.code == code


def test_data_rejects_python_objects_subclasses_and_cycles_without_callbacks():
    class Evil(dict):
        def items(self):
            raise AssertionError("callback must not execute")
    with pytest.raises(ControlIRError, match="non_json_data"):
        data(Evil())
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ControlIRError, match="cyclic_data"):
        data(cycle)
    with pytest.raises(ControlIRError, match="invalid_unit"):
        type_spec({"kind": "Int", "unit": {"tokens": True}})


@pytest.mark.parametrize("container", ["Sequence", "Set", "Maybe", "Record"])
def test_dimensionless_type_equivalence_is_recursive(container):
    wrap = lambda item: {"kind": container, "fields": {"value": item}} if container == "Record" else {"kind": container, "item": item}
    assert same_type(wrap(INT), wrap({"kind": "Int", "unit": {}}))
    assert not same_type(wrap(INT), wrap({"kind": "Int", "unit": {"bytes": 1}}))


@pytest.mark.parametrize("capacity,ratio,retained,usage,expected", [
    (100, "0.8", 10, 80, False), (100, "0.8", 10, 81, True),
    (10, "0.8", 9, 1, True), (0, "0.8", 10, 0, False),
])
def test_spec_pressure_formula_is_expressible_without_hidden_services(capacity, ratio, retained, usage, expected):
    seq = lambda *items: {"op": "sequence", "item_type": INT, "items": list(items)}
    threshold = op("min", seq(op("add", op("floor", op("mul", read("ratio"), read("c"))), lit(1)),
                              op("max", seq(lit(1), op("sub", read("c"), read("retained"))))))
    result = evaluate_expression(op("ge", read("usage"), threshold), {
        "c": binding(capacity), "ratio": binding(ratio, RATIONAL),
        "retained": binding(retained), "usage": binding(usage)})
    assert result.error is None and result.value is expected


@pytest.mark.parametrize("consumer,maximum,ordinal,expected", [
    ("normal", 1, 0, False), ("leaf", 1, 0, False),
    ("compaction", 2, 0, True), ("compaction", 2, 1, False),
])
def test_neutral_retry_formula_keeps_existing_1_1_2_policy(consumer, maximum, ordinal, expected):
    expr = op("and", op("member", read("failure"), read("retryable")),
                      op("lt", op("add", read("ordinal"), lit(1)), read("maximum")))
    result = evaluate_expression(expr, {"failure": binding("no_input", TEXT),
        "retryable": binding(["no_input", "protocol_rejected", "provider_retryable"], {"kind": "Set", "item": TEXT}),
        "ordinal": binding(ordinal), "maximum": binding(maximum)})
    assert result.error is None and result.value is expected


def test_record_constructor_preserves_the_same_field_name_type_boundary():
    result = evaluate_expression({"op": "record", "fields": {"value": lit(1)}}, {})
    assert result.type == {"kind": "Record", "fields": {"value": INT}} and result.value == {"value": 1}
    with pytest.raises(ControlIRError, match="expected_nonempty_text"):
        evaluate_expression({"op": "record", "fields": {"": lit(1)}}, {})

"""A deliberately small external package using only the public plugin SDK."""
from cpn.plugins import PluginDefinition, PluginOperation, PluginResource


DEMO_RESULT_LIMIT_BYTES = 1024
DEMO_INTEGER_BOUND = 1_000_000_000


def add(context, arguments):
    context.check_cancelled()
    return {"value": arguments["left"] + arguments["right"]}


def instruction(context, arguments):
    asset = context.resources["instruction"]
    return {"instruction": asset.text(), "source_resource_id": asset.resource_id,
            "source_resource_version_id": asset.resource_version_id}


def summarize(context, arguments):
    context.check_cancelled()
    values = arguments["values"]
    total = sum(values)
    mean = total / len(values)
    if mean.is_integer():
        mean = int(mean)
    return {
        "count": len(values),
        "total": total,
        "mean": mean,
        "minimum": min(values),
        "maximum": max(values),
    }


def plugin():
    # Declaration only: no network, child process, implicit dependency installs,
    # private Registry calls, or provider access at import/registration time.
    return PluginDefinition(
        name="demo", version="0.3.0",
        operations=(
            PluginOperation("add", "Add two numbers and return a JSON value.",
                {"type": "object", "additionalProperties": False,
                 "properties": {
                     "left": {
                         "type": "integer",
                         "minimum": -DEMO_INTEGER_BOUND,
                         "maximum": DEMO_INTEGER_BOUND,
                     },
                     "right": {
                         "type": "integer",
                         "minimum": -DEMO_INTEGER_BOUND,
                         "maximum": DEMO_INTEGER_BOUND,
                     },
                 },
                 "required": ["left", "right"]},
                {"type": "object", "additionalProperties": False,
                 "properties": {"value": {"type": "integer"}}, "required": ["value"]},
                add, max_result_bytes=DEMO_RESULT_LIMIT_BYTES),
            PluginOperation("instruction", "Load the exact bundled instruction resource.",
                {"type": "object", "additionalProperties": False},
                {"type": "object", "additionalProperties": False,
                 "properties": {"instruction": {"type": "string"},
                    "source_resource_id": {"type": "string"},
                    "source_resource_version_id": {"type": "string"}},
                 "required": ["instruction", "source_resource_id", "source_resource_version_id"]},
                instruction, resources=("instruction",),
                max_result_bytes=DEMO_RESULT_LIMIT_BYTES),
            PluginOperation(
                "summarize",
                "Summarize one nonempty list of bounded integer values.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "values": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 1000,
                            "items": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 1000000,
                            },
                        },
                    },
                    "required": ["values"],
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "count": {"type": "integer", "minimum": 1},
                        "total": {"type": "integer", "minimum": 0},
                        "mean": {"type": "number", "minimum": 0},
                        "minimum": {"type": "integer", "minimum": 0},
                        "maximum": {"type": "integer", "minimum": 0},
                    },
                    "required": [
                        "count", "total", "mean", "minimum", "maximum",
                    ],
                },
                summarize,
                max_result_bytes=DEMO_RESULT_LIMIT_BYTES,
            ),
        ),
        resources=(PluginResource("instruction", b"Use the supplied exact inputs. Report the computed result."),),
    )

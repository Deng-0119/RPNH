"""Opt-in real upstream checks. No LLM calls or original benchmark rollout."""
import os
from pathlib import Path
import pytest

@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("RPNH_AB_UPSTREAM"), reason="real upstream not installed/configured")
def test_real_six_domain_manifest_and_native_tools():
    from rpnh_ab.upstream import Upstream
    from rpnh_ab.experiment import plan_for
    upstream = Upstream(Path(os.environ["RPNH_AB_UPSTREAM"]))
    cases = upstream.cases("public")
    plan = plan_for(cases, "public")
    assert len(plan["tasks"]) == 600
    for case in cases:
        state = upstream.start(case["row"])
        assert len(state["tool_defs"]) == 3
        assert isinstance(state["world"].meta.allowed_services, list)

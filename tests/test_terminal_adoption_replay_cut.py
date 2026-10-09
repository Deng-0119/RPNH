"""Actual original-Registry appends/fence changes during terminal replay."""
import pytest
from test_terminal_owner_adoption import adopted_fixture, terminal_count, registry_evidence
from cpn.rpnh.registry import module_terminal
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.errors import TerminalReadStale


@pytest.mark.parametrize("race", ["writer", "append"])
def test_crossnet_replay_rechecks_cut_after_evidence_read(tmp_path, monkeypatch, race):
    owner, _, _, _ = adopted_fixture(tmp_path)
    evidence = owner.terminal()
    assert evidence is not None
    read_object = module_terminal._object
    injected = []
    def read(core, kernel, ref, object_type):
        result = read_object(core, kernel, ref, object_type)
        if object_type == "run_terminal_evidence/v1" and not injected:
            injected.append(True)
            if race == "writer":
                _RegistryCore(core.run_dir, create=False)
            else:
                owner.control.publish("result", "race:terminal-replay", None, {"status": "OBSERVED"})
        return result
    monkeypatch.setattr(module_terminal, "_object", read)
    with pytest.raises(TerminalReadStale) as caught:
        owner.terminal()
    assert caught.value.reason == ("STALE_WRITER" if race == "writer" else "STALE_CUT")
    assert injected == [True]
    assert terminal_count(owner) == 1

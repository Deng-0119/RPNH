import pytest
import opencode_candidate_support

@pytest.fixture(autouse=True)
def mock_preflight(monkeypatch):
    def prepare(self):
        self.evidence['status'] = 'running'
    monkeypatch.setattr(opencode_candidate_support.CandidateRun, 'prepare', prepare)

@pytest.mark.opencode_candidate('contract')
def test_explicitly_simulated_call_failure(opencode_candidate):
    raise AssertionError('Synthetic test failure; no native execution')

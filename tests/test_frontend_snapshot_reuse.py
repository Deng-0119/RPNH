"""Owner-local observation reuse; never a cross-request authority cache."""
import copy
import threading
import pytest
from cpn.rpnh.frontend_application import FrontendGateway, FrontendError

class App:
    def __init__(self):
        self.value = [{"state": "running"}]
        self.reads = 0
        self.failure = None
        self.host_exited = threading.Event()
        self.observed_exit = threading.Event()
        self.closed = False
    def snapshot(self):
        self.reads += 1
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.value)
    def tick(self):
        if self.host_exited.is_set():
            self.value = [{"state": "stopped"}]
            self.observed_exit.set()
    def configuration(self):
        return {}
    def command(self):
        self.value = [{"state": "changed"}]
        return "ok"
    def close(self):
        self.closed = True


def test_each_read_fresh_once_and_notification_tracks_returned_observation():
    app = App()
    notifications = []
    gateway = FrontendGateway(lambda: app, on_change=lambda: notifications.append(1), tick_interval=60)
    try:
        assert app.reads == 1
        for _ in range(4):
            assert gateway.call("snapshot") == [{"state": "running"}]
        assert app.reads == 5
        assert len(notifications) == 1
        app.value = [{"state": "external change"}]
        returned = gateway.call("snapshot")
        assert returned == app.value
        assert app.reads == 6 and len(notifications) == 2
        returned[0]["state"] = "client mutation"
        assert gateway.call("snapshot") == app.value
        assert len(notifications) == 2
        assert gateway.call("command") == "ok"
        assert app.reads == 8 and len(notifications) == 3
        gateway.call("configuration")
        assert app.reads == 9
    finally:
        gateway.close()
    assert app.closed


def test_host_transition_observed_on_periodic_tick_without_new_request():
    app = App()
    changed = threading.Event()
    def notify():
        if app.observed_exit.is_set():
            changed.set()
    gateway = FrontendGateway(lambda: app, on_change=notify, tick_interval=.01)
    try:
        app.host_exited.set()
        assert changed.wait(2)
        assert gateway.call("snapshot") == [{"state": "stopped"}]
    finally:
        gateway.close()


def test_snapshot_error_not_replaced_with_previous_observation():
    app = App()
    gateway = FrontendGateway(lambda: app, tick_interval=60)
    try:
        app.failure = FrontendError("denied", "denied", 403)
        with pytest.raises(FrontendError) as exc:
            gateway.call("snapshot")
        assert exc.value.code == "denied" and app.reads == 2
        app.failure = None
        assert gateway.call("snapshot") == app.value
    finally:
        gateway.close()

@pytest.mark.parametrize("kind", ["canonical", "callback"])
def test_observation_failures_still_fail_call(kind):
    app = App()
    def notify():
        if kind == "callback" and app.reads > 1:
            raise RuntimeError("private")
    gateway = FrontendGateway(lambda: app, on_change=notify, tick_interval=60)
    try:
        app.value = [{"state": float("nan") if kind == "canonical" else "changed"}]
        with pytest.raises(FrontendError) as exc:
            gateway.call("snapshot")
        assert exc.value.code == "application_error"
    finally:
        gateway.close()


def test_continuous_snapshot_polling_does_not_starve_host_tick():
    import time
    app = App()
    gateway = FrontendGateway(lambda: app, tick_interval=.01)
    try:
        app.host_exited.set()
        deadline = time.monotonic() + 2
        observations = []
        while time.monotonic() < deadline:
            value = gateway.call("snapshot")
            observations.append(value)
            if value == [{"state": "stopped"}]:
                break
        assert app.observed_exit.is_set()
        assert observations[-1] == [{"state": "stopped"}]
    finally:
        gateway.close()

@pytest.mark.parametrize("args,kwargs", [((True,), {}), ((), {"filtered": True})])
def test_parameterized_snapshot_retains_full_fresh_notification(args, kwargs):
    class FilteredApp(App):
        def snapshot(self, filtered=False):
            value = super().snapshot()
            return [] if filtered else value
    app = FilteredApp()
    notifications = []
    gateway = FrontendGateway(lambda: app, on_change=lambda: notifications.append(1), tick_interval=60)
    try:
        assert gateway.call("snapshot", *args, **kwargs) == []
        assert app.reads == 3  # startup, filtered result, fresh full notification
        assert len(notifications) == 1
    finally:
        gateway.close()

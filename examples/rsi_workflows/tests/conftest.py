import pytest

from cpn.rpnh.control_server import OwnerEventLoop
from examples.tool_pipeline.tests.pipe_transport import PipeOwnerEventLoop


def pytest_addoption(parser):
    parser.addoption("--rsi-transport", choices=("native", "pipe"), default="native",
        help="explicit test-only pipe wake transport; never native AF_UNIX acceptance")


@pytest.fixture(scope="session")
def rsi_loop_factory(pytestconfig):
    return (OwnerEventLoop if pytestconfig.getoption("--rsi-transport") == "native"
            else PipeOwnerEventLoop)

import pytest
from cpn.rpnh.control_server import OwnerEventLoop
from examples.tool_pipeline.tests.pipe_transport import PipeOwnerEventLoop


def pytest_addoption(parser):
    parser.addoption("--tool-pipeline-transport", choices=("native", "pipe"), default="native",
        help="pipe explicitly substitutes only test wake transport; it is not AF_UNIX acceptance")


@pytest.fixture(scope="session")
def loop_factory(pytestconfig):
    return (OwnerEventLoop if pytestconfig.getoption("--tool-pipeline-transport") == "native"
            else PipeOwnerEventLoop)

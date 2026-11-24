"""One built-in web harness implementation, parameterized by its page adapter."""

from ..claude.control import Catalog, Model
from ..harness import Launch
from .process import WebProcess
from .providers import Provider


class WebHarness:
    src = "web"
    tool_prefix = ""
    bin = ""  # built into aegis; no provider CLI to install or version-probe
    steers_mid_turn = False
    steers_notices = False
    mcp_transports = frozenset()
    contains = frozenset({"harness_tools"})

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.name, self.label = provider.name, provider.label

    def process(self, launch: Launch) -> WebProcess:
        return WebProcess(launch, self.provider)

    async def probe(self, spec, stderr_path) -> Catalog:
        return Catalog(
            (),
            (
                Model(
                    "web",
                    "web",
                    self.label,
                    "Model and modes are selected in Chrome",
                    (),
                ),
            ),
        )

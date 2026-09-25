"""Prepare the narrow factory seam in the pinned DSH checkout."""

from pathlib import Path
import sys


target = Path(sys.argv[1]) / "packages/core/agent-loop/src/index.ts"
source = target.read_text(encoding="utf-8")

replacements = (
    (
        "import { ReactLoopAgent }",
        "import type { Scope } from '@deepseek-ai/dsh-scope'\n"
        "import { ReactLoopAgent }",
    ),
    (
        "interface PreparedAgent {\n  agent: ReactLoopAgent",
        "export interface AgentMachine extends Agent { readonly scope: Scope }\n\n"
        "interface PreparedAgent {\n  agent: AgentMachine",
    ),
    ("let machine: ReactLoopAgent | undefined", "let machine: AgentMachine | undefined"),
    (
        "const loopCtx = this.runtime.ctx",
        "const loopCtx = this.runtime.ctx\n"
        "    const createMachine = this.createMachine.bind(this)",
    ),
    (
        "machine = new ReactLoopAgent(loopCtx, id, options, session)",
        "machine = createMachine(loopCtx, id, options, session)",
    ),
    (
        "  constructor(ctx: Context, config: Config) {",
        "  /** Replace the driver while retaining factory ownership/setup/publication. */\n"
        "  protected createMachine(ctx: Context, id: SessionId, options: AgentOptions, "
        "session: Session): AgentMachine {\n"
        "    return new ReactLoopAgent(ctx, id, options, session)\n"
        "  }\n\n"
        "  constructor(ctx: Context, config: Config) {",
    ),
)

markers = tuple(updated for _original, updated in replacements)
present = tuple(marker in source for marker in markers)
if all(present):
    print("Pinned DSH factory seam is already prepared")
    raise SystemExit(0)
if any(present):
    raise SystemExit("DSH factory seam is only partially prepared")

for original, _updated in replacements:
    if source.count(original) != 1:
        raise SystemExit(
            "DSH factory source differs from the pinned semantic seam: "
            f"expected one occurrence of {original!r}"
        )

for original, updated in replacements:
    source = source.replace(original, updated, 1)

target.write_text(source, encoding="utf-8")

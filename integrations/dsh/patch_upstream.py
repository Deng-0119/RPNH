"""Prepare the narrow factory seam in the pinned DSH checkout."""

from __future__ import annotations

from pathlib import Path
import sys


FACTORY_PATH = "packages/core/agent-loop/src/index.ts"

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

def prepared_source(source: str) -> tuple[str, bool]:
    """Return the exact prepared source and whether a change is required."""
    markers = tuple(updated for _original, updated in replacements)
    present = tuple(marker in source for marker in markers)
    if all(present):
        return source, False
    if any(present):
        raise ValueError("DSH factory seam is only partially prepared")
    for original, _updated in replacements:
        if source.count(original) != 1:
            raise ValueError(
                "DSH factory source differs from the pinned semantic seam: "
                f"expected one occurrence of {original!r}"
            )
    for original, updated in replacements:
        source = source.replace(original, updated, 1)
    return source, True


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        raise SystemExit("pass the pinned DSH checkout")
    target = Path(arguments[0]) / FACTORY_PATH
    try:
        source, changed = prepared_source(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not changed:
        print("Pinned DSH factory seam is already prepared")
        return 0
    target.write_text(source, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

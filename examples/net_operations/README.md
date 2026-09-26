# Native net-operation example

From the repository root, run:

```bash
python -m examples.net_operations.compose_serial
```

The command prints a complete two-stage `ModuleDeclaration`. It performs no
Registry write and no model call. The symbolic components are prefixed
`prepare_` and `finish_`, the connecting edge is explicit, and both operations
share the same predeclared budget bucket. Supply matching trusted component,
executor, terminal and schema registrations before compiling or running it.

[中文说明](README_ZH.md)

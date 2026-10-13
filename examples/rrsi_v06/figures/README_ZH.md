# 在官方 Viewer 查看 RRSI 声明

[English](README.md) | 中文 · [案例与原始图片](../README_ZH.md)

## 打开一个 child 声明

在仓库根目录，使用已有且已安装 RPNH 的 Python 环境。查看这些随仓库提供的 JSON 不需要安装独立 RRSI example 包。

```bash
python examples/view_declaration.py rrsi-policy --no-open
python examples/view_declaration.py rrsi-analyst --no-open
python examples/view_declaration.py rrsi-proposer --no-open
python examples/view_declaration.py rrsi-critic --no-open
python examples/view_declaration.py rrsi-digester --no-open
```

选择其中一个命令，打开打印的 `http://127.0.0.1:端口/`，保持终端运行，结束后按 Ctrl+C。`--no-open` 只禁止自动打开浏览器；Viewer 仍需允许绑定 loopback HTTP socket。它不是无 socket 模式，也不是 `file://` HTML Viewer。

可切换 **PetriNet**、**Detailed flow** 或 **Agent overview**，使用缩放、平移与 **Fit to view**，再点击节点和弧检查细节。

静态帧：[Policy](policy/declaration-frame.json)、[Analyst](analyst/declaration-frame.json)、[Proposer](proposer/declaration-frame.json)、[Critic](critic/declaration-frame.json)、[Digester](digester/declaration-frame.json)。[Policy 全景说明](policy/README_ZH.md)。

## 如何理解视图

每份帧对应一个真实、独立 child Module 的 `initial_configured` 声明。四角色的 Petri 图均有七个节点、七条弧。Detailed flow 仅折叠 `role.response`；初始化与 action 循环都输入 `role.state`，所以它保留。Agent overview 显示声明的 `role.model` agent 及结构性自环。自环有经过 response、act、state 的路径见证，不代表已观察执行顺序或循环成功。

`role.init` 与 `role.act` 保持 deterministic executor transport。model 身份来源为 `declared_llm_executor`；`semantic_group`、`agent_ref`、`executable_binding_ref` 保持 null。帧不含 runtime、token marking、firing 或 terminal evidence，历史不支持；未知观察数量保持未知。这些独立声明不会合成 campaign 总 PN，也不会充作下级执行 PN。

查看实际运行时，使用其精确 Registry 目录执行 `rpnh net --run "$RUN_DIR" --view --no-open`，并检查该次运行的证据。

## 无 socket 重建与检查

在源码根目录，已有 RPNH Python 依赖可用时执行：

```bash
python -m examples.rrsi_v06.declarations --check
python -m unittest discover -s tests -p 'test_rrsi_declaration_export.py' -v
node tests/rrsi_declaration_read_model.mjs
```

Python 测试使用标准库 runner，不依赖 pytest；JavaScript 检查需要 Node.js。这些命令不调用 provider、模型、Registry 或 Viewer server。重新生成四帧及 provenance 时，运行 `python -m examples.rrsi_v06.declarations`，省略 `--check`。

[生成器](../declarations.py)复用真实角色声明、注册、编译器及共享身份导出。[当前帧来源记录](role-declaration-provenance.json)保存源码基底、相关源码 hash、生成器 hash、compiled/projection/frame hash 及序列化方式。四个 compiled hash 与[历史角色图来源](../results/figure-provenance.json)一致；当前 projection/frame hash 单独记录。原图及历史截图 provenance 保持不变，本次没有新截图声明。

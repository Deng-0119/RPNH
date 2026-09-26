# 原生网操作示例

在仓库根目录运行：

```bash
python -m examples.net_operations.compose_serial
```

命令会输出一个完整的两阶段 `ModuleDeclaration`，不会写 Registry，也不会调用模型。
组件符号分别带有 `prepare_` 和 `finish_` 前缀，连接边是显式的，两个 operation 共享同一个
预先声明的预算桶。编译或运行前，调用方仍须提供匹配的可信 component、executor、terminal
和 schema 注册。

[English](README.md)

# RPNH bootstrap 薄入口

## Linux 正式分发方向（待发布）

后续 Linux 版 RPNH 以官方 npm 包及其命令作为安装和更新入口。该入口应调用原 RPNH 安装／更新流程，提供对应发行材料并让实际应用版本完成切换；只更新本薄入口包不等于升级 RPNH。原 Python 内核、生命周期确认与用户数据保护继续由原机制负责。

正式包名、版本映射、前置依赖和面向用户的完整命令由发行阶段确定并验证。下面的 private tarball / 显式 wheel 示例用于当前候选验证；公网安装与真正的 RPNH 跨发行版本更新尚未完成，不能据此写成已发布能力。

此源码包只启动显式选定 RPNH wheel 中的 bootstrap 模块，不再捆绑第二份 Python 安装代码，也不管理应用版本。wheel 同时提供 Python 命令 `rpnh-bootstrap`；正式应用命令仍是 `rpnh`。

当前包 private、未发布。使用本地 npm tarball；不承诺公网包名或正式发行。需要 Node 20+、已安装 uv、固定持久控制器 Python，以及包含 `cpn.rpnh.bootstrap_entry` 的准确 RPNH wheel。缺该模块的旧 wheel 不能使用新入口。

构建和调用示例见[英文说明](README.md)。示例 wheel 文件名只作占位，必须替换为实际构建文件。`--release` 必须对应原 published 安装器支持的 metadata/assets；本包不会生成或虚构发行源，本地控制器 wheel 也不替代发行资产。分隔符前 `--offline` 仅禁止 uv 网络，不会把原发行读取变成离线；离线测试的 metadata 传输替代须明确披露。

显式本地候选使用另一条入口；先准备上述 private npm tarball 和包含此入口的控制器 wheel：

```sh
./bootstrap-tool/node_modules/.bin/rpnh-bootstrap \
  --controller-wheel /absolute/path/controller.whl \
  --controller-python /absolute/path/python3 --offline \
  -- --root /absolute/path/rpnh-root \
  --candidate-wheel /absolute/path/candidate.whl \
  --materials /absolute/path/materials.json --inspect-only --json
```

须替换为实际 wheel 文件名。控制器 wheel 必须包含本地候选入口；candidate wheel 是显式选择的应用载荷。移除 `--inspect-only` 后调用原 `update apply`，沿用原终端 `UPDATE` 确认；检查模式调用原 `update plan`，不虚构 published release。materials 唯一提供准确 platform、runtime bindings 和 extras，原载荷、身份、一致性检查保持生效；本入口不生成材料。

`--release` 与 `--candidate-wheel` 互斥。本地候选必须带 `--materials`，拒绝混用 `--python`、`--runtime-bindings`、`--platform`、`--channel`、`--extra`、`--combination`；published 请求拒绝 `--materials`。本地 `--components FILE`、`--json` 原样传给 plan/apply；`--select` 仅供本地 apply，local `--inspect-only` 时拒绝。检查模式拒绝 `--bin-dir`，只有 apply 成功后才注册命令。

分隔符后的参数传入 wheel 模块：published 完整前端使用 `--runtime-bindings FILE --components FILE`、可选 `--select` 和重复 `--extra mcp/skills`；Basic 可用 `--python`。平台事实仍显式提供，不自动声称 native。控制器 Python 固定且不自动下载；uv只拥有临时控制器环境，原RPNH prepare/probe/current仍唯一拥有应用安装及确认流程。

可选 `--bin-dir DIRECTORY` 在安装完成后把原受管 `rpnh` dispatcher链接到已有真实目录，不修改PATH、不覆盖其它命令。注册失败退出3，已成功安装的应用保留，可重试注册或使用root/bin/rpnh。当前链接注册证据仅Linux，不是原生Windows支持。private标记用于避免误发布，不是另一套产品权限。

Python使用 `-I -m`，定位wheel模块时忽略调用目录和PYTHONPATH，避免旧checkout替代指定wheel。

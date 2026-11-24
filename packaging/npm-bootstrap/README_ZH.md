# RPNH bootstrap 薄入口

## 已发布的 RC9 与本目录源码模板

[RPNH v0.1.0rc9](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc9) 是当前 GitHub 预发行。已发布的 npm 包是 [`rpnh-bootstrap@0.1.0-rc.9`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.9)；发布后 `latest` 与 `next` 均指向 RC9。在 WSL2 Linux x86_64／持久 CPython 3.12.13 的 Basic 范围内，`npm exec --package=rpnh-bootstrap -- rpnh` 已通过匿名全新安装、激活、首次配置与离线 doctor 检查。验收不涵盖升级、回滚、其他平台或可选前端。 前置条件见[安装指南](../../docs/guides/linux-npm-candidate_ZH.md)。仅更新 npm 引导包不会升级 RPNH 应用；原安装器与生命周期确认仍负责实际安装更新。

仓库内的 npm 源码模板是私有包。下列是本地模板 tarball 的验证方式，不是 npm 公网包的安装命令。需要 Node.js 20+、`uv`、持久控制器 Python，以及包含 `cpn.rpnh.bootstrap_entry` 的匹配 RPNH wheel。缺该模块的旧 wheel 不能使用此入口。模板仅调用指定 wheel，不捆绑第二份安装器，也不管理应用版本。wheel 同时提供 Python 命令 `rpnh-bootstrap`，安装后的应用命令为 `rpnh`。

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

## 组装发行包

运行 `prepare_first_run_candidate.py`，传入同版本的核心 wheel、启动器源码、许可证、npm 版本及新的输出目录。应用版本 `0.1.0rc9` 对应 npm 版本 `0.1.0-rc.9`。

`--public-cli` 只组装 `rpnh` tarball，其中的命令为 `rpnh`，npm 发布元数据目标为 `latest`；组装不等于发布。以 `rpnh` 为包名的 RC9 发布尝试被拒绝。已发布的包是 `rpnh-bootstrap`。`--public-preview` 组装该包，包含 `rpnh` 和 `rpnh-bootstrap` 两个命令，发布元数据目标为 `next`。默认输出为私有候选包。两种组装方式都使用原安装器与匹配的 GitHub Release 附件。

运行 `python packaging/npm-bootstrap/test-prepare-first-run.py` 可检查离线组装行为。

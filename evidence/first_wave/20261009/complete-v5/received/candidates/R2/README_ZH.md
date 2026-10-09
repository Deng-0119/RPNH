# R2 单 Registry 本地验收包

从 LOCAL_RETURN_TASK_ZH.md 开始。依赖已交付R1v2，不包含R1补丁的重复副本。
代码增量只有 rsi-runtime-validation.patch 的五个 examples/rsi_workflows 新文件。
source/内对应五文件可与file-manifest逐字核验。不要把本包覆盖到仓库根。

作者165项：R2 15＋R1/原compiler兼容150全过；独审15复跑＋5追加单列。
原native AF_UNIX单项EPERM失败完整保留，pipe不是native。最新d92只有有限
三Registry只读组合核验；SQLite可能创建SHM/空WAL，不声称全目录物理零写。

历史failed日志、XML与只读探针保留；未携带原来被否决的R1 runtime terminal overlay代码。
没有原始Registry数据库、工作区、provider profile、凭据或环境依赖。
用户计划v3为交接资料，独立于五文件代码patch。没有上传、push或发布授权。

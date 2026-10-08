# SCB 真实开发前缀与原始失败记录

2026-10-08，用户授权的 `code_search` 前三个检查点已完成，模型沿用 `codex/gpt-5.6-terra`。原始评分如下；case含回归用例，不能累加为独立题数。

| 检查点 | 原始评分 | 真实模型调用 | 业务结果 |
|---|---:|---:|---|
| checkpoint_1 | 13/13 | 5 | 通过 |
| checkpoint_2 | 25/25 | 5 | 通过 |
| checkpoint_3 | 40/47 | 14 | 7个业务用例失败 |

共24次真实调用、0次超限调用，15条容器命令均exit0；另有2次 `arguments_invalid` 动作拒绝，原参数与错误均保留。三点owner、原grader和清理完成；ANY_CASE下CLI exit0不代表全部业务用例通过。没有重跑、从grader反馈修改solver或运行第四/第五检查点。

第三点的7个失败为 `optional_metavar`、`multiple_metavars`、`multiline_python_if_blocks`、`language_filtering`、`literal_dollar_sign`、`multiple_files_sorted`、`special_chars_in_captures`。完整断言上下文见 `run/checkpoint_3/evaluation/stdout.txt` 和 `run/checkpoint_3/evaluation/report.json`，提交源码见各 `run/checkpoint_N/snapshot/`。工具命令exit0、模型自述和最终grader结果分别保留，不能互相替代；两次参数拒绝与七个失败之间未建立单一因果结论。

`MANIFEST.json`登记475个payload，约3MB：358原文件/注册payload字节，117明确标记的元数据序列化。24份request recipe、24份adapter返回和24份规范化响应不是72次模型调用。原件未修改，30个fresh-reader provenance失败单列保留，按精确对象字节回收不代表原来源校验通过或执行授权。

`actual_model_call_counts`的原tuple是settled total / post-limit excess；第二项不是fake次数。真实调用身份来自实际官方Codex endpoint及既有selection，先前native fixture另行记录。未保存的vendor wire和规范化token/USD保持不可得，不补造或填零。

本次实际运行字节对应产品74fad32；runner固定31ceea3、problems固定9cd9ca3。solver network=none，构建/评分host网络；镜像首次nvm下载失败、同版本下载代理/pipefail/retry适配及原Dockerfile/patch均保留。每点最多48次调用、owner等待7200秒，上游cost/net-cost/step caps均0。这是adapted development prefix3，不是完整五点或未经改动的官方AgentRunner实验，不构成harness优势证据。

`reviews/`、`PARENT_PUBLICATION_REVIEW.json`是新的发布审阅；初始MANIFEST/safe-summary内pending字段保持收集时原字节。`collection/README_ZH-original-candidate.md`保留最初候选说明。`reproduction-final/`仅为最后版本帮助脚本，不冒称每个早期脚本版本；原始失败Dockerfile与日志独立保留。

未上传profile、凭据、DB/WAL/SHM、缓存或完整上游测试/oracle/reference solution。历史路径用于诊断，不是访问授权。当前自有daemon已停止，缓存保留。本轮直接GitHub交付，不制作新ZIP。

原pytest报告对五个非空actual_stdout已有省略；缺失中段未重构，详见 `observations/scb-failure-observations.json`。可直接确认的实现观察包括可选占位符后缀识别、字面美元符号转义以及跨语句capture边界；不据此把七项失败都归为同一原因。

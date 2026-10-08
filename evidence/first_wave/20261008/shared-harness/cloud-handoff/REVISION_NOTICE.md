# 候选修订与实测来源

## 当前应用选项

只应用 patches/A-registry-execution-reader.patch 和 patches/B-shared-output-guidance.patch。A 的共享 reader 位于现有 Registry 内，两 consumer 仅提供 context expectations、格式化和各自 body limit；不创建第二套 current matcher 或 PetriNet authority。

## 版本链

1. 旧 HA-only 候选：补丁 4df2d4f5aa02c25f87f49a8dfe3091d21d3917490e1e7e1e5c82a0e220cddc7b；作者/独立各 108 passed。已撤换，不作为新 A 证据，旧 patch 不提供应用。
2. 共享 Registry 实现候选：补丁 5aac652ea4c0c76e8641581b1a972a31c992c1b70dc32bc2aa9094e52883dfc1；作者 242 passed / 162.23s，独立 242 passed / 161.01s，均无 skip。测试针对这一精确候选；不得将它们改标为之后的文件 bytes 实测。
3. 当前冻结 A：补丁 61f2f7115306b8c39846d34e77918c31ae4ccf9f62c0a8835a661c7124400137。相对前一项只补 environment_host 的 None-delivery docstring 及双语架构说明，无执行逻辑变化；针对性 None 回归及最终差异审查见 evidence/review_summary.json。两个 242-case 记录仍绑定第 2 项。
4. B 补丁原字节未变，作者与独立 focused 各 36 passed；其 native main-session direct-text 原始尝试仍是 AF_UNIX EPERM 环境阻断。

environment_host 的旧 None → not_terminal 是“本次调用未提供可交付候选”的协议短路，不是 Registry current query。公共 read_run_execution 的 expected terminal ref 缺省仍会查询当前 authority 的 terminal。不要把两个入口混为一谈。

本版清晰区分历史、实现实测、最终说明性 delta 和未运行的 native gate。没有把旧历史实验、prompt 资源、运行结果或分数回填成新版本。

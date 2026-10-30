# Weekly Guide Sync Implementation Plan

> **For agentic workers:** Use the approved design and execute the checklist in order, with tests before implementation.

**Goal:** 每周检查指南，创建可人工合并的更新 PR。

**Architecture:** Python 标准库与 Git 准备受限目录的更新，GitHub Actions 调度，固定版本的 create-pull-request 动作管理同一条提案。

**Tech Stack:** Python 3.11+、Git、GitHub Actions、GITHUB_TOKEN。

**Spec:** `docs/superpowers/specs/2026-10-30-weekly-guide-sync-design.md`

## Global Constraints

- 周一北京时间 09:17；不自动合并；只更改设计中指定的三处同步内容。
- 不执行上游脚本，保留原始 Git tree；无变化保持工作区及版本记录不变。

## Review Focus

- 脏工作区和手改指南必须在文件更改之前停止。
- 网络或元数据失败不能留下被删除的指南。
- 新来源必须保持隐藏文件、二进制和换行的原始内容。
- 无更新及重复执行不产生新提案。
- Actions 只写自动分支，不更新 main 或个人笔记。

## Task 1: 受限同步与验证

- [x] 在 `tests/test_sync_guide.py` 写真实 Git 集成测试并确认实现缺失时失败。
- [x] 在 `scripts/sync_guide.py` 实现 `prepare_update(root, upstream_url, today)`；返回 changed、版本和文件变化计数，更新受限文件。
- [x] 运行 `python -B -m unittest discover -s tests -v`，11 项测试全部通过（Windows 使用已发现的 Python 完整路径）。

## Task 2: 工作流与发布

- [x] 添加机器版本记录、UPSTREAM 同步区块、每周及手动触发工作流、阅读说明。
- [x] 检查工作流结构、完整测试、变更路径及原指南 tree，进行独立代码审阅；审阅发现的重新暂存转换问题通过两个红绿回归测试修复。
- [ ] 启用仓库所需 PR 创建权限，发布工作流并实际触发一次；核对运行结果及是否创建/更新提案。

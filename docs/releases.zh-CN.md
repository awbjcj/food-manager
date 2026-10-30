# 版本标签与发布

[English](releases.md) | [简体中文](releases.zh-CN.md)

生产版本使用 `v主版本.次版本.修订版本` Git 标签。README 中的 **version**
徽章显示最高的稳定版本标签，**release** 徽章显示最新发布的 GitHub Release。
所有版本都来自 Railway 部署的 `master` 分支；`dev` 仍是默认开发分支。

## 自动版本标签

当前 `master` 提交的 **CI/CD** 推送检查全部通过后，
[Version and release](../.github/workflows/release.yml) 会检查自最高稳定祖先标签
以来的提交，并采用其中幅度最大的版本升级：

| 提交标记 | 版本变化 | 示例 |
| --- | --- | --- |
| `type!:`，或 `BREAKING CHANGE:` / `BREAKING-CHANGE:` 页脚 | 主版本 | `v6.14.1` → `v7.0.0` |
| `feat:` / `feat(scope):` | 次版本 | `v6.14.1` → `v6.15.0` |
| 修复、性能优化、重构、回退、依赖更新或未分类提交 | 修订版本 | `v6.14.1` → `v6.14.2` |
| 仅 `docs:`、`test:`、`ci:`、`style:` 或不涉及依赖的 `chore:` / `build:` | 不新增标签 | 版本保持不变 |

请使用 Conventional Commits 提交标题，让新功能和不兼容变更获得正确的版本号。
一批提交只生成一个标签。失败、取消、未完成、PR、Fork 或已被更新的生产提交取代
的 CI 检查都不能授权新增标签。已有版本标签不会被移动或替换。

## 每周集中发布

每周五 **14:23 UTC**，工作流检查当前生产 CI，为尚未标记的变更生成版本，
并按需发布最新版本。发布说明涵盖自上一个**已发布 Release** 以来的所有提交，
包括中间版本标签对应的变更。已有 Release 的版本不会重复发布。
公开发布之间至少间隔七天，手动运行同样受此限制。

在 GitHub Actions 中打开 **Version and release → Run workflow**，可选 `tag`
补齐版本标签，或选 `publish` 检查是否需要发布每周合集。定时和手动触发需要工作流
存在于默认分支，因此 `dev` 和 `master` 都保留该文件；执行时始终检出 `master`。
工作流使用仓库自带的 `GITHUB_TOKEN`，仅授予 `contents: write` 和 `actions: read`，
无需额外配置个人访问令牌。

这些版本记录源代码状态。Railway 部署健康状况、真实模型调用和支付验收需分别确认。
工作流不会发布到 PyPI，也不会修改 Python 分发包独立的版本元数据。

## 补齐历史版本

[version-history.json](../.github/version-history.json) 记录从最初机器人到当前生产
版本的已完成里程碑。所有标签都包含三段数字；仅选取**六个**版本创建 GitHub
Release，按主版本汇总，其余里程碑只添加标签。

早期路线图中的版本有时并未按编号顺序完成，例如 v4.9 的完整食谱来源链在 v5.0
稳定性工作之后落地。因此，这些旧标签对应功能完成时的代码快照，并不代表严格按
时间排序的发布序列。没有明确完成点的 v4.3–v4.5 和 v5.4 不会凭空生成。
原计划中的 **v5.5** 实际在 v6.0 之后完成，故记录为 **v6.1.0**，保证之后的生产
版本号持续递增。后续 v6 次版本按功能组划分，修订版本记录其间的维护更新。

先预览完整历史计划，再执行写入：

```text
python bin/release.py backfill --repo awbjcj/food-manager
python bin/release.py backfill --repo awbjcj/food-manager --apply
```

脚本在写入前验证全部目标都属于生产历史，并检查所有已有标签是否冲突。
重复执行会跳过已经正确创建的标签和 Release；不会覆盖标签，也不会创建超过六个
历史 Release。一次性的历史补齐不受每周发布间隔限制。

实现见 [bin/release.py](../bin/release.py)，回归检查命令：
`uv run pytest tests/test_release.py`。

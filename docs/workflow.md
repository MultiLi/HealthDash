# 开发流程：分支、worktree、门禁

> 本文回答：**改这个仓库的代码，正确的动作顺序是什么。**
> 判读怎么算对不在这里（见 `AGENT.md` 与 `skills/body-health/SKILL.md`），
> 部署怎么发不在这里（见 `docs/operations.md`）。

---

## 一、为什么要有流程

2026-11-01 凌晨发生过一次现场：一个会话把前端功能改到一半（20 个文件、859 行），
没提交、没发布；另一个会话要动同一个仓库，只能在「等」和「覆盖别人的改动」之间选。

同一天还验证了另一件事：那 859 行改动里修掉的一个 bug，**在 main 上是活的**——
`smoke_site.mjs` 用「当月」当锚点读数据分片，每月 1 号必然 `ENOENT` 崩溃。
工作区因为堆着未提交的改动，一直没暴露；基于 main 拉的干净 worktree 一跑就炸。

两件事指向同一个结论：**没有隔离和门禁，改动会互相掩盖。**
「本地能跑」不等于「main 能跑」——main 可能一直是坏的，只是没人从干净状态跑过它。

---

## 二、分支模型

| 分支 | 谁能推 | 说明 |
|---|---|---|
| `main` | 只能靠 PR 合并 | 已配分支保护：必须 PR、`check` 必须绿、禁强推、禁删 |
| `feat/*` `fix/*` `ci/*` `docs/*` | 自由推 | 一个功能一个分支，用完即删 |

分支名前缀不带也行，`wt.py` 会补成 `feat/`。

**拦截分两层**，缺一不可：

1. **远端**：GitHub 分支保护。这是真保护，任何机器、任何客户端都绕不过。
   配置：`GH_TOKEN=... ./tools/ci/setup_branch_protection.sh`，或照脚本末尾的网页步骤手点。
2. **本地**：`tools/hooks/pre-push`。拦得住手滑，拦不住 `--no-verify`。
   安装：`cp tools/hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`

引导期（保护还没生效时）确实需要直推 main，用 `ALLOW_MAIN_PUSH=1 git push origin main`。
这个开关是留给「修 CI 自身、仓库初始配置」的，不是日常通道。

---

## 三、worktree：并行开发不打架

一个功能一个目录，同一个仓库。

```bash
python3 tools/ci/wt.py new feat/nudge-v2      # 建分支 + 目录 + 接数据
cd .worktrees/feat-nudge-v2                   # 干活
python3 tools/ci/check.py                     # 动手前后各跑一次
# …改代码…
git add -A && git commit -m '...'
git push -u origin feat/nudge-v2              # 然后开 PR
```

```bash
python3 tools/ci/wt.py list        # 所有 worktree、脏状态、数据是否接上
python3 tools/ci/wt.py link        # 补接数据（老 worktree 或手动建的）
python3 tools/ci/wt.py rm feat/nudge-v2       # 合并后清理
```

### 数据和产物怎么过去（这决定 worktree 能不能用）

| 类型 | 处理 | 原因 |
|---|---|---|
| `data/*` | **符号链接** | 唯一数据源。多份数据比共享数据危险，见 `AGENT.md` §5 |
| `tools/deploy.json` | 符号链接 | 本机凭据，只读用 |
| `site/index.json`、`site/daily/` | **复制** | 派生产物。链接会让 worktree 里的重建覆盖主仓库 |
| `site/.data/`、推送令牌 | 不接 | 推送是独占操作，一律回主仓库做 |

两个坑，都是实测踩出来的：

**坑 1：`data/` 不能整个目录链。** `data/README.md` 是被跟踪的，新 worktree
checkout 完就有一个「存在但只有 README」的 `data/`。按目录判断会跳过链接，
于是脚本拿到一个真实的空数据目录——**不报错，只是全部读空**。所以 `wt.py`
逐个子项链（`data/raw`、`data/db`……）。

**坑 2：`site/index.json` 不能链。** 链过去之后，worktree 里跑
`build_homepage_data.py` 会把主仓库的产物覆盖掉（实测 md5 会变）。
而不重建、直接拿主仓库最新的 `index.json` 去冒烟，测的是「旧分支代码 + 新数据」
的组合，红得莫名其妙，agent 会去修一个不存在的问题。
所以它是**复制**的，各分支独立。

### worktree 之间什么共享、什么不共享

| 共享 | 隔离 |
|---|---|
| `data/`（唯一一份） | 工作区文件、暂存区、当前分支 |
| git 对象库与远端 | 构建产物（`site/index.json`） |
| `.git/hooks/`（钩子对全部 worktree 生效） | |

因此**以下操作是独占的**，同一时刻只能有一个 agent 做（同 `AGENT.md` §8）：
`slot.py mark`、`push_data.py`、`collect.py` 的推送动作、应用发布。
worktree 隔离代码，隔离不了这些。

---

## 四、门禁：一条命令，两边同一份

```bash
python3 tools/ci/check.py           # 本地
```

CI 跑的是**完全相同的这一句**（`.github/workflows/ci.yml`）。
所以本地绿 = CI 绿，不存在「本地过了 CI 挂」的猜谜。

| 检查项 | CI 里 | 本地 | 说明 |
|---|---|---|---|
| `guard` | ✅ | ✅ | 已跟踪文件里不许有身体数值 |
| `py:compile` | ✅ | ✅ | 语法 + `pyproject.toml` 声明的 3.9 下限 |
| `js:syntax` | ✅ | ✅ | 全部 `.js`/`.mjs` 过 `node --check` |
| `path:anchor` | ✅ | ✅ | `settings.py` 能在干净 checkout 里自己找到根 |
| `structure` | ✅ | ✅ | `.gitignore` 数据规则完整、`data/` 无跟踪文件 |
| `smoke` | ⏭️ **跳过** | ✅ | 要 `site/index.json`，不入库 |
| `golden` | ⏭️ **跳过** | ✅ | 要 `data/db/health.db`，不入库 |

**跳过的四项不是通过，是没验。** CI 的 Summary 页会单独列出未覆盖范围，
每次运行也会在 Actions 顶部挂一条 notice。看到全绿先看清楚绿的是哪几项。

想让 CI 覆盖前端，唯一正道是造一份**脱敏的 fixture** 代替真实 `index.json`，
再让 `smoke_site.mjs` 支持从指定路径读。这件事没做，记在 `notes/backlog.md`。

---

## 五、一次完整开发的动作序列

```bash
# 0. 起点：main 必须是绿的。不绿先修，不要在红的基线上叠东西。
git switch main && git pull
python3 tools/ci/check.py

# 1. 开分支 + worktree
python3 tools/ci/wt.py new feat/xxx && cd .worktrees/feat-xxx

# 2. 改代码（改 site/ 的话，先 build 再冒烟）
python3 tools/build_homepage_data.py && node tools/smoke_site.mjs

# 3. 自检
python3 tools/ci/check.py

# 4. 提交并推分支
git add -A && git commit -m 'feat: ...' && git push -u origin feat/xxx

# 5. 开 PR（网页或 gh pr create --fill），等 check 绿

# 6. 合并后清理
cd <主仓库> && python3 tools/ci/wt.py rm feat/xxx

# 7. 如果改了 site/：发布是独立的动作，别忘
python3 tools/deploy.py            # republish → 重新发布应用 → --mark-app-deployed
```

第 7 步最容易漏。`git push` 不等于上线——2026-10-31 那轮重构就是这么积压的。

---

## 六、PR 的尺寸纪律

一个 PR 只做一件事。判据不是行数，是**能不能用一句话说清它改了什么**。

反例（2026-11-01 那 859 行）：采集四态判定 + 补数提示条 + 三个真 bug + 文档
混在一个工作区里，没有 commit 边界。后果是没人说得清「哪几行修了 1 号崩溃」，
也没法只回滚补数功能而保留 bug 修复。

对照组：`rules.json` 的改动必须单独 PR，并在描述里附 `backtest.py diff` 的改判清单
——因为那是口径变更，和功能改动不是一类东西。

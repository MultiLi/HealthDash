# 开发流程：分支、worktree、门禁

> 本文回答：**改这个仓库的代码，正确的动作顺序是什么。**
> 判读怎么算对不在这里（见 `AGENT.md` 与 `skills/body-health/SKILL.md`），
> 部署怎么发不在这里（见 `docs/operations.md`）。

---

## 一、为什么要有流程

没有隔离和门禁，改动会互相掩盖。两个必然后果：

1. **一个会话改到一半（几十个文件、未提交未发布），另一个会话只能选「等」或「覆盖别人的改动」。** 前者卡住进度，后者直接丢工作。
2. **「本地能跑」不等于「main 能跑」。** main 可能一直是坏的，只是没人从干净状态跑过它——工作区堆着未提交的改动时，那批改动的修复会把 main 上的老 bug 一起遮住，基于 main 拉干净 worktree 一跑就炸。

---

## 二、分支模型

| 分支 | 谁能推 | 说明 |
|---|---|---|
| `main` | **约定**只能靠 PR 合并 | ⚠️ **远端没有强制**（免费账号限制，见下）。`check` 必须绿、禁强推、禁删——这些目前靠本地钩子和你自己的遵守，不靠 GitHub 拦 |
| `feat/*` `fix/*` `ci/*` `docs/*` | 自由推 | 一个功能一个分支，用完即删 |

分支名前缀不带也行，`wt.py` 会补成 `feat/`。

**拦截分两层**，缺一不可：

1. **远端**：GitHub 分支保护。这是真保护，任何机器、任何客户端都绕不过。
   配置：`GH_TOKEN=... ./tools/ci/setup_branch_protection.sh`，或照脚本末尾的网页步骤手点。
2. **本地**：`tools/hooks/pre-push`。拦得住手滑，拦不住 `--no-verify`。
   安装：`cp tools/hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`

### ⚠️ 计划限制：免费账号 + 私有仓库 = 没有远端保护

这不是配置问题，是产品边界。GitHub 官方两页文档（About protected branches / About rulesets）
的「Who can use this feature」写着同一句话：

> 私有仓库的分支保护与 Rulesets 需要 **GitHub Pro、Team 或 Enterprise**；
> Free 计划只在**公开**仓库可用。

本仓库所在账号是个人账号（`MultiLi`，type=User），在 Settings → Rules 里创建 ruleset 会被提示「won't be enforced until you upgrade to GitHub Team or Enterprise」。**经典分支保护同样不可用**，换入口解决不了。

可选路径（按代价排序）：

| 路径 | 代价 | 效果 |
|---|---|---|
| 升级 **GitHub Pro** | $4/月（个人账号，不是 Team） | 经典保护 + Rulesets 全部可用 |
| 只留本地 hook | $0 | 拦得住手滑，拦不住 `--no-verify`、别的机器、网页直接编辑 |
| 换 GitLab | 迁移成本 | Free 计划**所有 tier** 都有 protected branches |
| 仓库转公开 | 数据暴露风险 | 免费获得完整保护，但本仓有过泄露前科，不可接受 |

**在升级之前，「不许直推 main」只是约定，不是强制。** 写在这里，是为了让每一个
agent 知道：`git push origin main` 在远端不会失败——所以更不能推。

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

## 五、合入 → 清理 → 部署

三段，顺序固定。**顺序不是建议，是判据**：任何一步提前或跳过，线上就会和 git 分家，
而 `git log` 看起来一切正常。整条链路由 `tools/release.py` 兜住（体检默认不动作，`--run` 才执行）。

```bash
python3 tools/release.py          # 体检：告诉你现在该做什么，不改任何东西
python3 tools/release.py --run    # 真跑：构建 → 按需热更新 → 核验 → 清理
```

### 5.1 合入

```bash
# 0. 起点：main 必须绿。不绿先修，不要在红的基线上叠东西。
git switch main && git pull --ff-only
python3 tools/ci/check.py

# 1. 开分支（并行改动多时用 worktree）
python3 tools/ci/wt.py new feat/xxx && cd .worktrees/feat-xxx

# 2. 改代码 → 自检 → 提交 → 推分支 → 开 PR
python3 tools/ci/check.py
git add -A && git commit -m 'feat: ...' && git push -u origin feat/xxx
gh pr create --base main --head feat/xxx

# 3. 合并：**门禁是这条 grep，不是 --watch 的退出码**
gh pr checks N --watch >/dev/null 2>&1
gh pr view N --json statusCheckRollup \
  -q '[.statusCheckRollup[].conclusion] | all(. == "SUCCESS")' | grep -qx true \
  || { echo "CI 未全绿，停止合并"; exit 1; }
gh pr merge N --squash --delete-branch
```

`gh ... -q` 在结论为 `false` 时只打印、退出码仍是 0——直接接 `&& gh pr merge` 会在红 PR 上照样合并（2026-11-01 两次，见 `incidents.md` #23）。**任何「先打印再判断」的命令都不算门禁，只有退出码算。**

三个约束：

| 约束 | 理由 |
|---|---|
| main 只靠 PR 进，且只允许 **squash** | 仓库设置如此；squash 正文要写清 why |
| 合并前必须看到 `statusCheckRollup` **全绿** | `gh pr checks --watch` 的退出码不反映检查结论，2026-11-01 因此在 CI 红下合并过一次 |
| 合并后立刻 `pull --ff-only` | 不拉回来，下一次改动就从旧基线起步，越拖越难合 |

**不在分支上构建。** 产物的内容指纹要与 main 的代码同源；分支上 build 出来的指纹
拿到 main 上会被当成「又改过」，永远对不上。

### 5.1.1 门禁：写进钩子，不靠记性

两条纪律曾经反复被违反，原因都不是不懂，是「顺手」。所以它们不再靠文档执行：

```bash
python3 tools/ci/setup_hooks.py          # 一次即可；--check 只看状态
```

| 钩子 | 拦什么 | 为什么 |
|---|---|---|
| `pre-commit` | 在 `main` 上提交 | main 只经 PR（只允许 squash）。main 上出现直推提交，「main 一定绿」就不成立了 |
| `pre-commit` | `?v=` 指纹与内容不符 | 指纹只提交一半会造出双模块实例（`store` 不共享），CI 红在离问题很远的地方 |
| `pre-push` | 直接 push `main` | 同上；远端分支保护才是真保护，这道是兜底 |

实现走 `core.hooksPath -> tools/hooks`，不往 `.git/hooks` 拷副本——副本会漂移，而漂移没有症状。worktree 共用 `.git/config`，设一次全都生效。

**逃生口**（显式、需写理由，别让它变成习惯）：`ALLOW_COMMIT_MAIN=1`、`ALLOW_MAIN_PUSH=1`。

CI 侧对应检查项 `asset:fingerprint`（钩子可被 `--no-verify` 绕过，CI 不行）；`hooks:installed` 只在本机跑，防止换机器后门禁静默消失。

### 5.2 清理

| 对象 | 动作 | 为什么 |
|---|---|---|
| 已合并的本地分支 | `git branch --merged main` 后删掉 | 留着会让 `git log --all` 变得难读，也容易在上面继续改旧代码 |
| worktree | `python3 tools/ci/wt.py rm <分支>` | 它有一份完整源码树，废弃后就是第二个真相 |
| `site/` 产物指纹 | 交付后还原（`git checkout -- site/`），**不入库** | `?v=` / `?b=` 是构建副产物：线上那份已经上传，本地留一堆未提交改动只会让下个会话误以为有人改了前端 |
| 部署游标与快照 | 不动 | `data/state/deploy.lock.json` 由 deploy.py 维护，手改会造出「假已上线」 |

`release.py --run` 会做完这些；`--keep-artifacts` 可以保留产物差异（排查线上差异时用）。

### 5.3 部署

顺序是硬的：**代码先上线，再推数据。**

```bash
python3 tools/release.py --run
```

它会按固定顺序走：① 起点校验 → ② 在 main 上构建 → ③ `deploy.py --check` 判动作
→ ④ 需要发布时**停下**并给出下一步 → ⑤ 热更新 + 回读核验 → ⑥ 登记游标 → ⑦ 清理 → ⑧ 线上核验。

需要重新发布时（`deploy.py` 报 `republish`），**三条命令，一条都不能省**：

```bash
# 1) 在 WorkBuddy 里发布 site/ 应用（参数见 tools/deploy.json）
# 2) 发布成功后登记游标（只有这一步能证明线上真的是这版代码）
python3 tools/deploy.py --mark-app-deployed
# 3) 再跑一次，这次才推数据
python3 tools/release.py --run
```

**为什么不能先推数据**：新数据键（如 `views/`）在旧服务端上不存在，`POST /api/push`
会整包 400——连当天的 index 一起推不上去，而失败发生在凌晨无人值守时。
2026-11-01 实见过一次，现在的应急降级只是兜底，不是正确顺序。

**`git push` 不等于上线。** 两者的差集不会自己显形：2026-10-31 的重构就是这么积压的
（本地构建了、冒烟过了、git 推了，线上打开还是旧那版）。

**而 `deploy.py --check` 本身也可能骗人。** 它比较的是「本地前后两次指纹」：只能发现
「我改了」，发现不了「我改了、但从来没上线」。所以游标里有 `code_verified`：

| 谁写 | 写什么 | 为什么 |
|---|---|---|
| `init`（首次建基线） | `false` | 那时只知道本地什么样，不知道线上 |
| `--mark-app-deployed` | `true` | 一次真实发布成功，是唯一可信证据 |
| `plan()` 见到 `false` | 拉线上比对 | 一致就自愈成 `true`，不一致就报 `republish` |

排查「到底上线没有」时用强制核验，**这是唯一不依赖游标说谎的判据**：

```bash
python3 tools/deploy.py --check --verify-live
```

---

## 六、PR 的尺寸纪律

一个 PR 只做一件事。判据不是行数，是**能不能用一句话说清它改了什么**。

反例：采集四态判定 + 补数提示条 + 三个 bug 修复 + 文档，混在一个工作区里没有 commit 边界。后果是没人说得清「哪几行修了 1 号崩溃」，也没法只回滚补数功能而保留 bug 修复。

对照组：`rules.json` 的改动必须单独 PR，并在描述里附 `backtest.py diff` 的改判清单
——因为那是口径变更，和功能改动不是一类东西。

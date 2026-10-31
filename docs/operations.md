# 运维手册

> 本文只写**做什么、什么时候做、怎么验证**。前端细节见 `docs/frontend.md`，判读纪律见 skill，架构见 `docs/architecture.md`。
>
> 约定：`$W` = 工作区根，`$S` = `$W/skills/body-health/scripts`（旧路径 `$W/.workbuddy/skills/body-health/scripts` 经符号链接仍可用）。

---

## 一、日常节奏：两个班次

| 时间 | 班次 | 目的 | 做什么 | 不做什么 |
|---|---|---|---|---|
| 14:00 | 采集 | 数据一落云就进盘 | `collect.py --days 3`（含 InBody 刷新 + 闸门 + 推主页） | 不出报告、不碰账本、不调 slot.py |
| 02:00 | 健康报告 | 出前一日报告 | `resolve` → `collect`（采集含 InBody，**别再单独 `--fetch`**）→ 闸门 → facts → 判读 → mark | 不新判已完成日 |

**不要再加回这些班次**：09:30 晨起补数、10:00 采集、11:30 兜底、23:30 主班。上午与傍晚各加一班只会让「哪天该出报告」出现多个答案——Sir 已定「下午、凌晨两点各一次，剩下取消」。

与之配套的三条口径：

1. **报告只在 02:00 出，锚定前一晚 23:30 的 slot**（`slot_time` 边界未变，`--job evening` 账本键照旧）。
2. **02:00 是最后一班 → 数据 partial 时禁止 `waiting_data`，直接出「截断终版」**（`--allow-truncated --data-cutoff`）。没有第二班接力。`slot.py mark` 的 publish_by（D+1 11:00）只是提示文案，代码不校验时间，技术上 02:00 完全可以关账。
3. **`--job morning` 已无调用方**，账本里若残留 pending 的 morning slot 属正常孤儿，不要为它补报告。

**两个铁律**：

1. **目标日由排程时刻决定，不由运行时刻决定。** 第一步永远是 `slot.py resolve --job evening`，取 `targets[].date`。**严禁用 shell `date` 推导。** targets 空 = 已完成，禁止重出。迟到 ≠ 改期。
2. **有补跑嫌疑时，收尾要再 resolve 一次。** 只 resolve 一次会漏掉中途被提升为待办的目标日。

### 文案约定：每班收尾必须带线上地址

两个班次的最后一行固定是：

```
线上主页：https://health-daily-86790.app.workbuddy.host
```

理由很实际：**不给链接就没人知道从哪点开**。消息里写出「总评 + 是否截断」而没有入口，
等于让人自己去翻收藏夹。

- **地址的唯一来源是 `tools/deploy.json` 的 `origin`**。`collect.py` 与
  `build_homepage_data.py` 都会在输出末尾打一行「线上主页: …」，
  prompt 里要求「与输出不一致时以输出为准」，所以脚本换了域名不会被旧文案绑住。
- **失败也要给链接。** 采集失败、`targets` 为空、报告没出——主页照样能打开，
  只是数据没更新。只在成功时给链接，等于把失败藏起来。
- ⚠️ **换域名时要改两处**：`tools/deploy.json`，以及两个 automation prompt 里那行字面量。
  prompt 存在宿主配置里，脚本读不到、无法自动校验——这是全项目**唯一一处
  无法用「单一来源」消除的重复**，属于已知取舍（宿主不给 prompt 提供变量注入）。
  改完跑一次 `build_homepage_data.py`，比对输出与 prompt 是否一致。

---

## 二、数据链路（每天跑，不需要动代码）

```bash
# ① 采集 + 自动回补截断日 + 重建物化层 + 推主页（一条命令做四件事）
python3 $S/collect.py --days 3

# ② 核验线上与本地逐文件一致（首选核验方式）
python3 tools/push_data.py --check        # exit 0 = 一致
```

`collect.py` 的六步：拉目标日 → 回补截断日 → 打印完整性一览 → `index.py build` → 推主页 → InBody 刷新 + 闸门。

**它不出报告、不做判读、不碰账本。** 这是有意的：「拉数」与「出报告」解耦，采集可以高频跑。

**不要靠提高拉数频率催数据。** 云端没有就是没有（10-18 连拉三次截止仍是 19:34）。高频的真实价值是「一落云就进盘」+「截断日自动回补」。

---

## 三、部署：数据热更新 vs 应用发布

**这是两条完全不同的路，走错一条就等于没上线。**

| 改了什么 | 后果 | 代价 |
|---|---|---|
| 只有数据（`data/`：raw、报告、`rules.json`、判读结果） | **热更新**：一个 POST，秒级，几乎无感 | 无 |
| 前端代码（`site/js/`、`site/css/`、`index.html`、`server.py`） | **必须重新发布应用** | 有失败面、要等缓存预热 |

### 3.1 一条命令判断该走哪条

```bash
python3 tools/deploy.py          # 检查 + 需要热更新就自动推（日常用这个）
python3 tools/deploy.py --check  # 只看，不动任何东西
```

| 输出 | 含义 | 该做什么 |
|---|---|---|
| `noop` | 代码与数据都与上次上线一致 | 什么都不用做 |
| `hot_update` | 只有数据变了 | 默认模式已自动推；`--check` 时手动跑一次 |
| `republish` | 前端代码变了 | **必须重新发布应用**，然后 `--mark-app-deployed` |

退出码：`0` 无需动作 / 已完成，`2` **需要重新发布**，`3` 热更新失败。
（定时任务与钩子读退出码，别改语义。）

### 3.2 判据怎么算的（别改成「看文件修改时间」）

两个指纹，存在 `data/state/deploy.lock.json`：

- **代码指纹**：`site/js/*.js`、`site/css/*.css`、`index.html` 的内容哈希，
  **剥掉 `?v=` 与 `?b=` 两种构建注入的指纹**之后再算。
- **数据指纹**：复用 `assetver.data_token()`（`index.json` + 月度分片内容）。

⚠️ **不能直接用 `assetver.asset_version()` 当判据**：它只剥 `?v=`、不剥 `?b=`，
而 `data.js` 里嵌着 `index.json?b=<token>`。数据每晚重算、token 就变、`data.js`
内容跟着变，那个指纹**天天都不一样**——拿它判断「代码改了没」会每天误报一次
「该重新发布了」。这是本模块唯一一个容易踩的坑，改代码时别把 `_STAMP` 那条正则删了。

### 3.3 重新发布的必带参数

```
directory=/Users/tian.li/Documents/Health/site
language=python
startCmd="python3 server.py"
installCmd=""
appId=wbapp_3G1QX7kZjmy7RNbU9gBdT5
```

- **漏掉 `language=python` 会退回纯静态**：页面看起来正常，但热更新失效，
  数据冻在打包那一刻，**且没有任何报错**。
- 传同一个 `appId` 就是覆盖同一个应用，**链接不变**——不需要（也没有）
  `updateExistingApp` 这类参数了，发布工具按 `appId` 复用。
- `server.py` 必须读 `PORT` 环境变量并 bind `0.0.0.0`（已满足，见 `server.py:455`）。

地址唯一来源 `tools/deploy.json`（线上 `https://health-daily-86790.app.workbuddy.host/`）。

### 3.4 为什么数据不用重新上线

`site/server.py` 提供同源可写端点，数据落服务端 `.data/`，更新 = 一个 `POST /api/push`
（带 `X-Push-Token`）。前端取数走 `js/data.js`：**端点优先，失败回落随包静态副本并标琥珀**——
所以推送失败不会白屏，只是回到打包时的数据。

### 3.5 三道触发点（靠机制，不靠记性）

| # | 触发点 | 做什么 |
|---|---|---|
| 1 | **改完代码当次** | agent 收尾必跑 `deploy.py`，`republish` 就发布 + 登记（`AGENT.md` §8 硬约束） |
| 2 | **git push 时** | `.git/hooks/pre-push` 发现 `republish` 打印警告，**不阻断推送**。重装：`cp tools/hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push` |
| 3 | **每天班次** | 14:00 / 02:00 各跑一次 `deploy.py`，发现积压就在消息里说明 |

> **为什么第 1 道最重要**：「改代码」和「上线」是两个动作，中间全靠人记得。只靠第 2、3 道，最坏要等一天才生效——而这一天里，线上跑的是别人以为已经改掉的旧逻辑。

---

## 四、补数（历史日 / 截断日）

**判据一律用字段扫描，禁止用报告文本搜索。**

```bash
# 看某天的实际截止时刻
python3 - <<'PY'
import json
d=json.load(open("data/raw/2026-09-09.json"))
print(d["usersummary_daily"]["wellnessEndTimeLocal"])
PY
```

落在次日 00:00 = 完整；否则截断。

**标准动作**：

```bash
python3 $S/sync.py --date D --days 1 --force     # 窗口只含 D，会一并重采 dailyhr/vo2/activities
python3 $S/fact_pack.py --date D --days 4        # 重算事实包
# 改写该日 md + judgment（单一终版口径）
python3 $S/fact_pack.py --date D --days 4 --merge-judgment
```

**补完历史日必须重跑相邻日的 fact_pack**——趋势窗里的旧 raw 已被覆盖。

**三个已知陷阱**：

1. **`--force` 会低估久坐这类累计字段**。截断日补全后久坐普遍上抬 3–5 小时（终值稳定 14h45m–15h12m）。**报告里出现「久坐 10–11 小时」量级时，先怀疑数据截断，而不是身体变好。**
2. **缺 `rhr` / `hr_hourly` 时先跑 `sync.py --date D --days 4`（不带 `--force`）**，通常一次补齐，不必上 `--backfill`。`--force` 会抹掉历史日的 dailyhr，且对无记录日的 `activities` 会返回 `fetch_failed`（正常）。
3. **无睡眠记录的日子，久坐会被抬到 ≈ 24 小时**（分母 `1440 − 睡眠 − 清醒 − 午睡` 退化）。判据：把三个活动分档加总，接近 1439min 就是分母伪影。**这类日子的久坐占比不可与长期序列并列。**

---

## 五、验证体系

跑完任何改动，按改动范围选：

| 改了什么 | 必跑 | 项数 |
|---|---|---|
| 口径（`rules.json` / `metrics.json`） | `backtest.py snapshot` → 改 → `backtest.py diff` | 改判清单 |
| 断言 / 物化 | `index.py verify` + `backtest.py golden` | 11 项 + 12 条 |
| 判读报告 | `verify_judgment.py --date D` | 5 项 |
| `site/js/` | `node tools/smoke_site.mjs` | 113 项 |
| 交互 | `python3 tools/probe_interactions.py` | 8 用例 |
| 视觉 | headless Chrome 截图（命令见 `docs/frontend.md`） | 人工看 |

**一条纪律：不要为了让测试变绿去改期望值。** 先分清是真错还是已知假警报。

### 冒烟测试的锚点纪律

**当前没有已知假警报。** 纪律：**锚点不许写死具体日期**，能构造输入的就在索引副本上构造，必须用真实数据的就动态挑、挑不到打印「– 跳过」并说明原因。

病根永远同一个：**测试把「某一天的真实状态」当成了不变量**。那天后来变了，断言就失效，看起来像产品坏了，其实只是日子不再是那样。典型三例：

| 断言 | 错误锚点 | 为什么失效 |
|---|---|---|
| 覆盖不足的卡要「不给结论」 | 写死某一天 | 该日 raw 被重采补全成 complete |
| 四项达标计数恒为 4 | `ix.today` | 当天整日无数据，无数据日按设计不渲染达标项 |
| 准备度日内轨迹 | 最后一个完整日 | 单点轨迹按设计返回空串（`detail.js`），取决于那天有没有运动后重算 |

现在的做法：
- **能构造输入的，就构造**——覆盖不足的文案分支直接在索引副本上改成 `na` + `c=0.5` 再渲染，与真实数据有没有截断日无关，永远可测。
- **必须用真实数据的，动态挑**——无数据日按「有行但值全空」挑（不是「索引里没这一行」，主页跨度含断档空白格），并排除今天（今天标「进行中」，过去的空日标「无采集记录」）。
- **挑不到就打印 `– 跳过` 并说明原因**，不 fail。注定失败或随机通过的测试只会训练人忽略红灯。

---

## 六、故障排查

| 症状 | 先查什么 | 结论 |
|---|---|---|
| Python 报 `Connection refused`，curl 正常 | 系统代理是否在跑（`scutil --proxy`） | 代理失效，命令前加 `no_proxy='*' NO_PROXY='*'`。**别去查佳明鉴权** |
| 今天没数据 | `usersummary.lastSyncTimestampGMT`（+8h = 本地） | 手表没同步 ≠ 拉数失败。这是常态，不是故障 |
| `lastNightAvg` 为 None + `sleep_daily` 空 | `lastSyncTimestampGMT` 有没有推进 | 没有 = 手表还没传，不是 bug |
| raw 文件结构完整但值全 null | `totalSteps` 是否 null、`stressValuesArray` 长度是否 0 | **未佩戴日的空占位响应**，不是拉数失败 |
| 报告说「覆盖全天」但数字偏低 | 直接查 `wellnessEndTimeLocal` | 报告文本不可信，必须查字段 |
| 主页数据冻住不更新 | 上次发布是否漏了 `language=python` | 退化成纯静态了，重新发布 |
| 主页显示「静态副本」琥珀胶囊 | `push_data.py --check` | 热更新端点不可达，回落了随包副本 |

---

## 七、账号与凭据

| 凭据 | 位置 | 谁能动 |
|---|---|---|
| Garmin garth token | `~/.config/garmin-cn/garth` | 只有 `sync.py`。**严禁二次登录** |
| InBody | `~/.config/inbody-cn/` | 只有 `sync_inbody.py` / `check_inbody.py` |
| 主页推送令牌 | `site/.push_token` | 不入库。缺失则端点半闭并如实上报 |

**任何情况下 `.push_token` 与个人健康原始数据都不能进公共仓。** 当前仓库是私有的，但 `.gitignore` 仍按公开标准写——私有只决定暴露面，不决定是否需要边界。

---

## 八、不入库的东西，丢了怎么办

`.gitignore` 排除了大量文件。排除它们的前提是**能重建**——下表是逐项核对的结果。（换机器、重建工作区时按此表处理。）

| 路径 | 丢了会怎样 | 能否重建 | 怎么恢复 |
|---|---|---|---|
| `data/raw/*.json` | 无法判读任何历史日 | ✅ 大部分能 | `sync.py --date D --days 1 --force` 按日重拉。**但如果 Garmin 云端已清理该日数据就永久丢失** |
| `data/inbody/` | 体成分面板空 | ✅ 能 | `sync_inbody.py` 全量分页重拉（账号里都有） |
| `data/db/health.db` | 无影响 | ✅ 能 | `index.py build`，约 2 秒 |
| `data/state/slots.json` | 账本丢失，可能重复派发已完成的日子 | ⚠️ 不能精确重建 | 会按落盘产物自愈（报告在就判 done），但需人工核对一遍 |
| `site/index.json` `site/daily/` | 主页无数据 | ✅ 能 | `build_homepage_data.py` |
| `site/.data/` | 服务端回了打包时的旧副本 | ✅ 能 | 重新 `push_data.py` |
| `site/.push_token` | 无法推数据 | ✅ 能 | 重新生成并同步到 `push_data.py` |
| `data/reports/*.md` `*.json` | **历史判读全丢，主页失去所有判读层** | ❌ **不能** | **必须独立备份。** LLM 叙述不可复现，重跑只能得到骨架版 |
| `data/logs/daily_log.jsonl` | 主观数据（体重等）全丢 | ❌ **不能** | **必须独立备份** |
| `data/logs/events.jsonl` | 健康事件归档全丢 | ❌ **不能** | **必须独立备份** |
| `data/tests/golden.json` | 口径回归失去锚点，改阈值时不知道偏了多少 | ❌ **不能** | 只能人工按 `docs/incidents.md` 逐条重建，成本极高 → **纳入备份** |
| `data/profile.json` | 引擎回落到示例档案，**Z2 区间 / 心率判读全用错数** | ⚠️ 手填 | 照 `references/profile.example.json` 重填七项，5 分钟 |

### 三个必须独立备份的东西

`data/reports/`、`data/logs/daily_log.jsonl`、`data/logs/events.jsonl` —— **它们不在 git 里，且无法用代码重建。**

**整个 `data/` 都不在 git 里**（`.gitignore` 一行 `data/*` 封死），所以「不在 git 里」不是这三样的特征，而是全部数据的共同状态。真正要区分的是**能不能重建**：`db/` `raw/`（大部分）能重拉重建，而 `reports/` `logs/` `tests/golden.json` 不能——**备份优先级按这张表来，不是按目录大小。**

git 管代码与口径，这三样管「实际发生了什么」。

**加密备份脚本**（`tools/backup_health.py`）：

```bash
# 交互式（提示输口令；口令不落盘、不进 ps）
python3 tools/backup_health.py --out ~/Documents/HealthBackups

# 外部盘（推荐，抗硬件故障）
python3 tools/backup_health.py --out /Volumes/你的盘名/HealthBackups

# 先看会打包什么、缺什么
python3 tools/backup_health.py --list

# 非交互（定时任务用）；口令从环境变量读
HEALTH_BACKUP_PASS=... python3 tools/backup_health.py --no-confirm
```

产出 `health_backup_<时间戳>.tar.gz.enc`（openssl aes-256-cbc + PBKDF2 20 万次迭代），明文压缩包生成后立即删除。解密：

```bash
HEALTH_BACKUP_PASS=<口令> openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass env:HEALTH_BACKUP_PASS -in health_backup_xxx.tar.gz.enc -out restore.tar.gz
```

**已验证**：加密 → 解密 → 解压 → 抽查 09-03（C2 改后为红）与 10-24（带 `downgrade_reason`）内容一致；错口令被 openssl 拒（`bad decrypt`）。

### 备份的两条纪律

1. **默认输出与项目在同一块盘**（`~/Documents/HealthBackups/）。这防误删、防「改错了想回退」，**不防硬件故障**。要抗硬件故障，`--out` 必须指向外部盘或另一台机器。
2. **云存储只能当副本之一，不能是唯一一份。** 云服务可用性绑定订阅状态——官方文档明确「`billingStatus` 为 `credits_exhausted` 时数据库读写全部失败」。把唯一备份放在一个会因欠费而锁定的地方，不叫备份。

**尚未自动化**：口令目前靠环境变量或交互输入，没进钥匙圈，所以还不能挂定时任务。要自动备份，需先把口令存进 macOS Keychain 并让脚本从那里读——见 `notes/backlog.md` B10。

---

## 九、改动前后必读

| 改什么 | 先读 |
|---|---|
| 主页 / 前端 | `docs/frontend.md`（已知缺口、缓存策略、验证手段） |
| 阈值 / 指标定义 | `docs/architecture.md` §1-§3 + `notes/confidence_model.md` |
| 判读写法 | `skills/body-health/SKILL.md` |
| 分层与目录 | `docs/architecture.md` §4-§6 |
| 文档该写哪 | `docs/README.md` |

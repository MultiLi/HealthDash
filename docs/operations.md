# 运维手册

> 本文只写**做什么、什么时候做、怎么验证**。前端细节见 `docs/frontend.md`，判读纪律见 skill，架构见 `docs/architecture.md`。
>
> 约定：`$W` = 工作区根，`$S` = `$W/skills/body-health/scripts`（2026-10-25 H3 起；旧路径 `$W/.workbuddy/skills/body-health/scripts` 经符号链接仍可用）。

---

## 一、日常节奏：两个班次（2026-10-31 精简）

| 时间 | 班次 | 目的 | 做什么 | 不做什么 |
|---|---|---|---|---|
| 14:00 | 采集 | 数据一落云就进盘 | `collect.py --days 3`（含 InBody 刷新 + 闸门 + 推主页） | 不出报告、不碰账本、不调 slot.py |
| 02:00 | 健康报告 | 出前一日报告 | `resolve` → `collect`（采集含 InBody，**别再单独 `--fetch`**）→ 闸门 → facts → 判读 → mark | 不新判已完成日 |

**已撤销的班次**（2026-10-31，Sir 定「下午、凌晨两点各一次，剩下取消」）：09:30 晨起补数、10:00 采集、11:30 兜底、23:30 主班。

撤销带来的三条口径变更，必须记住：

1. **报告只在 02:00 出，锚定前一晚 23:30 的 slot**（`slot_time` 边界未变，`--job evening` 账本键照旧）。
2. **02:00 是最后一班 → 数据 partial 时禁止 `waiting_data`，直接出「截断终版」**（`--allow-truncated --data-cutoff`）。以前靠 11:30 兜底，现在没有第二班接力。`slot.py mark` 的 publish_by（D+1 11:00）只是提示文案，代码不校验时间，技术上 02:00 完全可以关账。
3. **`--job morning` 已无调用方**，账本里若残留 pending 的 morning slot 属正常孤儿，不要为它补报告。

**两个铁律**：

1. **目标日由排程时刻决定，不由运行时刻决定。** 第一步永远是 `slot.py resolve --job evening`，取 `targets[].date`。**严禁用 shell `date` 推导。** targets 空 = 已完成，禁止重出。迟到 ≠ 改期。
2. **有补跑嫌疑时，收尾要再 resolve 一次。** 旧 02:30 班曾因只 resolve 一次漏掉被提升为待办的 09-12。

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

## 三、代码链路（只有改前端时才需要）

**关键区分**：

| 改了什么 | 要做什么 |
|---|---|
| 数据（一次判读、`rules.json`、raw） | 只需 `build_homepage_data.py --push`，**不需要重新发布** |
| 前端代码（`site/js/`、`site/css/`、`index.html`、`server.py`） | **必须重新发布**，且参数不可省 |

重新发布的必带参数（漏一个就静默退化）：

```
language=python
startCmd="python3 server.py"
installCmd=""
appId=wbapp_3G1QX7kZjmy7RNbU9gBdT5
updateExistingApp=true
```

**漏掉 `language=python` 会退回纯静态：页面看起来正常，但热更新失效，数据冻在打包那一刻，且没有任何报错。**

地址的唯一来源是 `tools/deploy.json`（线上 `https://health-daily-86790.app.workbuddy.host/`）。

**为什么"换数据"不需要重新上线**：`site/server.py` 提供同源可写端点，数据落服务端 `.data/`。更新 = 一个 `POST /api/push`（带 `X-Push-Token`）。前端取数走 `js/data.js`：端点优先，失败回落随包静态副本并标琥珀。

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

### 一个已知假警报

- **`smoke_site.mjs` 在 23:30 后跑「进行中当天」会固定误报 2 项**——期望值假设当天已收工。判断方法：看失败项是否都关于今天且当前时间在 23:30 之后。

（原先「9 月均准备度期望值陈旧」那一条已在 2026-10-25 修正为自洽断言，不再是假警报。）

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
| 云端胶囊显示「N 天不一致」 | `health_days` 最近 7 天 | 跑一次管理通道同步（唯一写路径） |

---

## 七、账号与凭据

| 凭据 | 位置 | 谁能动 |
|---|---|---|
| Garmin garth token | `~/.config/garmin-cn/garth` | 只有 `sync.py`。**严禁二次登录** |
| InBody | `~/.config/inbody-cn/` | 只有 `sync_inbody.py` / `check_inbody.py` |
| 主页推送令牌 | `site/.push_token` | 不入库。缺失则端点半闭并如实上报 |
| 云端写入 | 只走管理通道（MCP `db_exec_sql`） | 没有 token、没有匿名写接口 |

**任何情况下 `.push_token` 与个人健康原始数据都不能进公共仓。** 当前仓库是私有的，但 `.gitignore` 仍按公开标准写——这样以后转公开不需要回洗历史。

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

**2026-11-01 起整个 `data/` 都不在 git 里**（`.gitignore` 一行 `data/*` 封死），
所以「不在 git 里」不再是这三样的特征，而是全部数据的共同状态。
真正要区分的是**能不能重建**：`db/` `raw/`（大部分）能重拉重建，
而 `reports/` `logs/` `tests/golden.json` 不能——**备份优先级按这张表来，不是按目录大小。**

git 管代码与口径，这三样管「实际发生了什么」。

**2026-10-25 已落地加密备份脚本**（`tools/backup_health.py`）：

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

**尚未自动化**：口令目前靠环境变量或交互输入，没进钥匙圈，所以还不能挂定时任务。要自动备份，需先把口令存进 macOS Keychain 并让脚本从那里读——见 `docs/decisions.md` D6。

---

## 九、改动前后必读

| 改什么 | 先读 |
|---|---|
| 主页 / 前端 | `docs/frontend.md`（已知缺口、缓存策略、验证手段） |
| 阈值 / 指标定义 | `docs/architecture.md` §1-§3 + `notes/confidence_model.md` |
| 判读写法 | `skills/body-health/SKILL.md` |
| 分层与目录 | `docs/architecture.md` §4-§6 |
| 文档该写哪 | `docs/README.md` |

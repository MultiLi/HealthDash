---
name: garmin-cn
description: >
  查询 Garmin Connect CN（佳明中国）的个人健康与运动数据。只要用户提到
  Garmin/佳明、询问跑步配速/睡眠质量/HRV/Body Battery/VO2 Max/训练状态等
  任何健康指标，或想导出 FIT/GPX/TCX 文件、分析某次活动的分段数据，都应
  优先激活此技能——即使用户没有明说"查 Garmin"或"用佳明"。
---

# garmin-cn — 佳明中国数据查询技能

连接 Garmin Connect CN（connect.garmin.cn），查询个人健康与运动数据。

**确定 SKILL_DIR**：本文档路径去掉末尾 `/SKILL.md` 即是；找不到就在文件系统搜
`garmin_cli.py`（`find ~ -maxdepth 8 -name "garmin_cli.py" -path "*garmin*" 2>/dev/null`）。
后续所有 `<SKILL_DIR>` 替换为该绝对路径。

---

## 架构总览：两条通道，A 主 B 备（2026-09-05 修订）

> **2026-09-05 重大修正**：此前记录的「usersummary=403」「通道 A 拿不到 dailyHeartRate」
> 都是**路径风格**造成的假象。改用 **query 风格**（`?calendarDate=` / `?date=`）后
> 两通道全部 200。步数/卡路里/距离/久坐/强度分钟/楼层/体重/BB 充放电**全部可得**。
> 详见文末「报告页逆向收获」。

| 通道 | 鉴权 | RUNNER | 能查 | 不能查（勿重试） |
|---|---|---|---|---|
| **A. Web 网关 /gc-api**（`scripts/garmin_gc_api.py`） | JWT_WEB cookie + csrf，会话存 `/tmp/garmin_gc_session.json` | `uv run --python 3.11 --with curl_cffi python <SKILL_DIR>/scripts/garmin_gc_api.py <cmd>` | **query 风格全通**：睡眠/HRV/压力/BB 充放电/呼吸/血氧/准备度/训练状态/VO2 Max/活动列表 + **usersummary 日汇总（步数·卡路里·距离·久坐秒·强度分钟·楼层）+ 15 分钟活动分级 + 体重序列 + 饮水 + 逐日心率** | 路径风格端点（见禁令 4）；`weight-service/*` → 404；`usersummary/stats/*` → 404 |
| **B. Mobile Bearer**（garth → connectapi.garmin.cn） | OAuth2 Bearer，token 已落盘 `~/.config/garmin-cn/garth` | `uv run --python 3.11 --with garth python`（内联脚本） | 与 A 高度重叠（usersummary / dailyHeartRate / socialProfile…），**作为备份**；A 会话 401 时启用 | 同上；路径风格一律 403 |

### 四条禁令（实测踩坑，违者必翻车）

1. **一次任务内严禁触发第二次登录。** 佳明单会话轮换：新 JWT_WEB 签发即作废旧的。
   实测：23:03 拿到可用 JWT_WEB，23:26 跑了一次 `gc_api refresh` 全新登录 → 旧 JWT_WEB
   立即 403，之后三次重试登录全部失败。
2. **会话/token 有效时，403 = 端点权限或路由问题**——换端点或换通道，绝不换会话重登。
3. **消费 ticket 时带旧 cookie 会被弹回 `/signin/`**；`gc_api build_session` 用空 jar
   消费是正确姿势，别"优化"它。
4. **任何 403 先试 query 风格，别急着判"CN 封了"。** 同一服务路径风格 403、query 风格 200
   已反复验证（`dailyHeartRate/{date}`✗ vs `?date=`✓；`usersummary/daily/{date}`✗ vs
   `?calendarDate=`✓）。**例外**：`hydration/daily/{date}` 与 `maxmet/latest/{date}`
   只认路径风格。拿不准就两种都试一次，各一次即止。

### 通道 A 会话建立（仅两种情况执行）

- `/tmp/garmin_gc_session.json` 不存在；或请求返回 401。
- 之外的情况**永远跳过 refresh**，直接用现有会话。
- 前提：用户浏览器登录过 connect.garmin.cn（CASMFA cookie 30 天有效）。
  脚本自动完成：Edge cookie 解密 → CASMFA 免短信签票 → 空 jar 消费 → 取 csrf。
- 凭据位置：`~/.config/garmin-cn/credentials.json`（`{"email","password"}`，权限 600）。

### 通道 B 标准用法（已验证 2026-09-04，token 复用零登录）

```python
from garth import Client
c = Client(domain="garmin.cn")
c.load("~/.config/garmin-cn/garth")   # 波浪号路径可用；token 失效才需重新 login
c.sess.trust_env = False              # 必须！沙箱代理 env 会让 requests 502
hr = c.connectapi("/wellness-service/wellness/dailyHeartRate",
                  params={"date": "2026-09-04"})
```

- **dailyHeartRate 只认 query 参数**；路径风格 `/dailyHeartRate/{date}` 返回 403——
  是路由问题不是权限问题，别在这上面浪费重试。
- token 失效才重走登录：Edge CASMFA cookie 注入 `c.sess.cookies`（domain 前加 `.`）→
  `c.login(email, pw, prompt_mfa=None, return_on_mfa=True)`；返回 `("needs_mfa", ...)`
  说明 CASMFA 已过期——立即停手请用户浏览器重登，**不要重试**（会烧短信码）。

### 已废弃路径（别再碰）

- `garmin_cli.py login` / `status`：MFA 账号死路（`MFA_CODE_INVALID`，短信码会烧光）。
- `scripts/garmin_mfa_login.py`：CN 短信码全部无效，仅留参考。
- `scripts/garmin_use_jwt.py`：依赖 `/tmp/garmin_jwt_web.txt`，JWT_WEB 会被后续任何登录作废，不可靠。
- ghs.connect.garmin.cn：cookie 访问整体 520；connectapi 用 cookie 访问：403（只认 Bearer）。
- Edge cookie 解密细节（钥匙串 `Microsoft Edge Safe Storage` → PBKDF2-HMAC-SHA1
  salt `saltysalt` 1003 轮 → AES-128-CBC IV 16 空格）已封装在 gc_api 脚本内，无需手写。

---

## 标准任务流程：每日数据分析（照抄，勿自由发挥）

**两步**：通道 A `pull`（已含 usersummary/summary_chart/BB 充放电）→ 通道 A `get` 活动列表
→ 本地解析。逐日心率 `pull` 不含，按需单点取（通道 A query 风格即可，无需 garth）。
不要再试探端点：确权清单见下节，已封的试了也是 404。

```bash
G="uv run --python 3.11 --with curl_cffi python <SKILL_DIR>/scripts/garmin_gc_api.py"
$G pull --start <DATE> --end <DATE> --out /tmp/garmin_day.json
$G get "activitylist-service/activities/search/activities?start=1&limit=5"
$G get "wellness-service/wellness/dailyHeartRate?date=<DATE>"      # 逐日心率（query 风格）
$G get "userprofile-service/userprofile/personal-information/weight"  # 体重样本序列
```

仅当通道 A 401 且无法重建时，才用通道 B（garth）：

```python
from garth import Client
c = Client(domain="garmin.cn"); c.load("~/.config/garmin-cn/garth")
c.sess.trust_env = False
hr = c.connectapi("/wellness-service/wellness/dailyHeartRate", params={"date": "<DATE>"})
```

VO2 Max：单日 pull 返回 `[]`，用单点 `$G get "metrics-service/metrics/maxmet/latest/<DATE>"`。

---

## 解析手册（实测沉淀）

### pull 输出结构与字段

顶层键：`sleep_daily / hrv_daily / stress_daily / usersummary_daily / summary_chart /
bb_reports / respiration_daily / spo2_daily / readiness_daily / ts_daily /
bb_range / stress_range / vo2max`。

| 键 | 取值字段 | 备注 |
|---|---|---|
| `sleep_daily[0].dailySleepDTO` | `sleepTimeSeconds`、`deep/light/rem/awakeSleepSeconds`、`sleepScores.overall.value/qualifierKey`、`avgHeartRate`、`avgSleepStress`、`sleepStart/EndTimestampGMT`、`sleepNeed`/`nextSleepNeed`（`.actual` 分钟 + `.feedback`） | GMT +8h = 本地（真 GMT，已验证）。同响应另含 `avgOvernightHrv`、`restingHeartRate`、`restlessMomentsCount`、`breathingDisruptionData`、`avgSkinTempDeviationC`（`skinTempDataExists=true` 时有效）、`hrvStatus` |
| `hrv_daily[0].hrvSummary` | `lastNightAvg`、`lastNight5MinHigh`、`baseline.balancedLow/balancedUpper`、`status` | `status` 可能是 `NONE`（`ONBOARDING_2`），别当错误 |
| `stress_daily[0]` | `avgStressLevel`、`maxStressLevel`、`stressValuesArray`（逐 30s） | 逐时聚合见下 |
| `stress_range[0].values` | `overallStressLevel`、`high/medium/low/restStressDuration`（秒） | |
| **`usersummary_daily[0]`** | **活动量**：`totalSteps`、`totalDistanceMeters`、`activeKilocalories`、`bmrKilocalories`、`totalKilocalories`、`floorsAscended/Descended`、`activeSeconds`、`highlyActiveSeconds`、`sedentarySeconds`、`sleepingSeconds`、`moderateIntensityMinutes`、`vigorousIntensityMinutes`、`intensityMinutesGoal`、`dailyStepGoal`；**心率**：`restingHeartRate`、`minHeartRate`、`maxHeartRate`、`lastSevenDaysAvgRestingHeartRate`；**压力**：`averageStressLevel`、`maxStressLevel`、`stressQualifier`、各等级时长；**BB**：`bodyBatteryChargedValue`/`DrainedValue`/`HighestValue`/`LowestValue`/`AtWakeTime`/`DuringSleep`/`ActivityEventList`；**呼吸**：`avgWakingRespirationValue`、`lowest/highest` | **步数/卡路里/距离的主来源**，见下节。值为 `null` = 当天没戴表或没同步，**不是接口错** |
| **`summary_chart[0]`** | 96 条 15 分钟格：`startGMT`/`endGMT`、`steps`、`pushes`、`primaryActivityLevel`（`sedentary`/`active`/`sleeping`/`generic`）、`activityLevelConstant` | **久坐分析金矿**：直接算连续 sedentary 时长、活动分布时段，无需靠心率反推 |
| **`bb_reports[0]`** | `date`、`charged`、`drained`、`bodyBatteryValuesArray`（全天曲线）、`startTimestampLocal`/`endTimestampLocal` | 替代已废弃的 `bb_events`（后者基本为空）；≤28 天分块 |
| `readiness_daily[0][0]` | **嵌套双层数组**；`level`、`score`、`feedbackShort`、`recoveryTime`（分钟） | |
| `ts_daily[0].latestTrainingStatusData.{deviceId}` | `trainingStatusFeedbackPhrase`、`sinceDate`、`acuteTrainingLoadDTO` | `{deviceId}` 键动态取 |
| `respiration_daily[0]` | `avgWaking/SleepRespirationValue`、`lowest/highestRespirationValue` | |
| `spo2_daily[0]` | `avgSleepSpO2`、`averageSpO2`、`lowestSpO2`、`latestSpO2`、`spO2HourlyAverages`、`spO2SingleValues` | **走 `wellness/daily/spo2/{date}`（路径风格，通道 A 200）**；全 `null` = **手表端血氧检测开关未开**或当天无睡眠采样，不是服务端限制。用户 2026-09-05 已开启开关，此后应有值 |
| `spo2_acclimation` | 高原适应数据 | **与血氧读数是两个端点**，别混用：`daily/spo2acclimation/{date}` ≠ `daily/spo2/{date}` |

### 活动量与久坐（2026-09-05 新增，优先用实测值）

```python
u = usersummary_daily[i]                      # 某天
steps      = u["totalSteps"]                  # 步数；null = 当天无数据
km         = u["totalDistanceMeters"] / 1000
act_kcal   = u["activeKilocalories"]          # BMR 另有 bmrKilocalories
sed_min    = u["sedentarySeconds"] // 60      # 久坐分钟，实测值
int_min    = (u["moderateIntensityMinutes"] or 0) + (u["vigorousIntensityMinutes"] or 0)

# 15 分钟级活动分级 → 连续久坐最长块
grid = summary_chart[i]                       # 96 格
runs, cur = [], 0
for g in grid:
    if g["primaryActivityLevel"] in ("sedentary", "sleeping"):
        cur = 0
    else:
        cur += 15
    runs.append(cur)
longest_sed_block = ...                       # 反向：连续 sedentary 的最长格数 × 15
```

判读口径（33 岁男性，本人实测参考值 2026-09-01~05：步数 6102–12187、久坐 10.6–17.5 h）：
久坐 &gt;8 h/日 需主动打断；强度分钟周目标 150（≈ 中+高强度分钟之和）；步数 &lt;5000 为低活动日。

### 时间戳换算（两次踩坑定论，唯一实现在 body-health 技能 `adapters/gtime.py`）

1. **沙箱机器时区不可信：一律禁止 `datetime.fromtimestamp()`**（实测把 03:14 显示成 11:14）。
   改用显式换算：`local = epoch_ms/1000 + 28800`，对 UTC 格式化。
2. **两类时间戳语义不同，勿混**：`*TimestampGMT` 是真 UTC epoch（本地钟面 = +8h）；
   `*TimestampLocal` 是**「当地挂钟时间按 UTC 存」**（按 UTC 取钟面即可，再 +8h 会翻倍，
   实测把入睡 02:27 显示成 10:27）。换算函数用 `gtime.py` 的
   `gmt_ms_to_local_hhmm()` / `local_ms_to_hhmm()`，禁止手搓。
3. 数组 epoch 编码不保证一致（曾遇"本地当 UTC"，也复测过真 GMT）。**每次用锚定法**：
   `stress_daily[0].startTimestampLocal`（本地 00:00）对应 `stressValuesArray` 首点 epoch；
   `小时 = (epoch_s - 首epoch_s) // 3600`，直接得本地小时。睡眠时间戳用 `+8h`。
4. 不确定的数组拿已知锚点对齐（入睡/醒来时刻、`endTimestampLocal`），别猜。

### 逐时压力聚合（找运动时段/画图用）

```python
base = <stressValuesArray 首点 epoch 秒>   # = 本地 00:00
hours = {}
for t, v in arr:            # arr = stressValuesArray
    if v >= 0:              # -1=未测, -2=无数据，必须过滤
        hours.setdefault((t//1000 - base) // 3600, []).append(v)
hourly = [round(mean(hours[h])) if h in hours else None for h in range(21)]
```

### 逐时心率聚合（通道 B 返回后）

```python
vals = hr["heartRateValues"]          # [[ts_ms, hr|None], ...]，None 必须过滤
base = <同上锚点>                      # 与压力曲线同一锚
hours = {}
for t, v in vals:
    if v is None: continue
    hours.setdefault((t // 1000 - base) // 3600, []).append(v)
hourly_avg = {h: round(mean(v)) for h, v in hours.items()}
hourly_max = {h: max(v) for h, v in hours.items()}
```

### 判读标准（用户 RHR 53 / maxHR 182）

- 逐时心率 avg≥100 且 max≥150 的整块小时窗 = **真实运动**；
  avg 60–90 且 max<120 = 日常/情绪波动。用它区分"运动"和"脑力紧绷"，别猜。
- 持续压力 60–88 = 持续体力消耗；压力高但心率平 = 纯脑力/情绪。
- **「没记成 activity」解读**：先查活动列表，没有就是没按开始键（Move IQ 不落活动）。
  **2026-09-05 起优先看 `usersummary_daily` 的 `moderateIntensityMinutes` + `activeSeconds`**：
  实测 9/4 活动列表仅有数百米，但 usersummary 显示 12187 步 / 9.77 km / 强度分钟 98。
  **activity 列表严重低估真实活动量，不要拿它当活动量结论。**
- BB 充放电：夜间充电量 `bodyBatteryChargedValue` 与日间消耗 `DrainedValue` 之差可判断
  恢复是否入不敷出（连续 charged &lt; drained 即电量净亏损）。

### 端点确权清单（2026-09-05 复核，两通道一致）

| 端点 | query 风格 | 路径风格 |
|---|---|---|
| `usersummary-service/usersummary/daily` | **200** | 403 |
| `wellness-service/wellness/dailyHeartRate` | **200** | 403 |
| `wellness-service/wellness/dailySummaryChart` | **200** | — |
| `wellness-service/wellness/bodyBattery/reports/daily` | **200**（`startDate`/`endDate`） | — |
| `sleep-service/sleep/dailySleepData` | **200** | — |
| `userprofile-service/userprofile/personal-information` | **200**（另 `/weight` 子路径 = 体重样本序列） | — |
| `usersummary-service/usersummary/hydration/daily/{date}` | — | **200** |
| `metrics-service/metrics/maxmet/latest/{date}` | — | **200** |
| `metrics-service/metrics/trainingstatus/aggregated/{date}` | — | **200** |

**确权不可用（勿重试）**：`weight-service/*`（全 404，体重改走 personal-information/weight）、
`usersummary-service/usersummary/stats/*`（404）、`usersummary/dailyRange`（404）、
`usersummary/steps`（404）、`wellness/daily/spo2?date=`（404，**走 `daily/spo2/{date}` 路径风格，通道 A 200**；注意不是 `spo2acclimation`，后者是高原适应）、
`wellness/daily/respiration?date=`（404，走 `wellness/daily/respiration/{date}`）、
`wellness/dailyStress?date=`（404，走 `wellness/dailyStress/{date}`）。
日汇总**无区间端点**，多天只能按 `calendarDate` 循环（1 天 1 请求）。

### 其他要点

- Web 网关请求必须带全套 `Sec-Fetch-*` + `Sec-CH-UA-*` 头 + `Connect-Csrf-Token`
  （已封装在 gc_api 脚本里，直接用脚本，别自己裸写请求）。
- `usersummary/stats/bodybattery|stress` 区间接口 ≤28 天，否则 400。
- CN 端点路径与 .com 不同（VO2 Max 是 `metrics-service/metrics/maxmet/latest/{date}`）；
  完整端点地图见 gc_api 脚本内 `ENDPOINTS`。
- `localStorage "token"` 在 CN 前端从不落盘，别去 leveldb 找。

---

## 命令参考

### 通道 A：garmin_gc_api.py（主力，MFA 账号可用）

```
<RUNNER> refresh            # 重建会话（仅会话缺失或 401 时）
<RUNNER> get <gc-api路径>    # 单点请求，401 自动重建会话
<RUNNER> pull --start <DATE> --end <DATE> --out <FILE>   # 按天全量拉取
```

`pull` 覆盖：睡眠/HRV/压力/**usersummary 日汇总（步数·卡路里·距离·久坐·强度分钟）**/**15 分钟
活动分级**/**BB 充放电**/呼吸/血氧/准备度/训练状态。活动列表、体重、饮水、逐日心率用 `get` 单点。

### 通道 B：内联 garth 脚本

见「通道 B 标准用法」。适合 dailyHeartRate 及其他 connectapi 端点的单点查询。

### CLI：garmin_cli.py（仅非 MFA 账号；本机账号是 MFA，别用 login）

```
<RUNNER_B> <SKILL_DIR>/scripts/garmin_cli.py <命令> [参数]
# RUNNER_B = uv run --python 3.11 --with garminconnect
```

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `activities` | 活动列表 | `--start/--end/--days(7)/--type`（running/walking/cycling…），返回 `activity_id` |
| `detail <id>` | 活动详情：分段/圈数/心率分区/天气/装备/训练效果 | |
| `run [id]` | 跑步专项：分圈配速+近期 5 次对比 | |
| `sleep` | 睡眠区间：时长/评分/HRV/BB | `--start/--end/--days(7)` |
| `health` | 健康区间：`--metrics hrv,rhr,stress,bb,spo2,respiration` | 同上 |
| `export <id>` | 导出 FIT/GPX/TCX/CSV | `--format fit` `--output <DIR>` |
| `summary [--date]` | 综合摘要（走 .com 路径，CN 结果不准，勿用；改看 pull 的 usersummary_daily） | |

---

## 报告页逆向收获（2026-09-05，从 /app/report/3/all/last_seven_days 入手）

**方法**：页面是 SPA（`/web-react/static/js/index__bundle__5.28.0.26.js` 只有 49 KB 入口，
业务代码在动态 chunk 里，静态 grep 拿不到端点）。真正的突破口是**把历史"403"结论按
query 风格重试**——报告页必然要显示步数，那就一定有可用端点。

**结论：步数/卡路里/距离/楼层/久坐/强度分钟/体重/饮水全部可得**，此前"CN 不提供"的判断作废。

新增可用端点（已并入 `ENDPOINTS` 与 `pull`）：

| 端点 | 拿到什么 | 分析价值 |
|---|---|---|
| `usersummary-service/usersummary/daily?calendarDate=` | 步数、距离、活动/基础/总卡路里、上下楼层、活动秒/高强度秒/**久坐秒**、中/高强度分钟 + 目标、静息/最低/最高心率、7 日均 RHR、平均/峰值压力、stressQualifier、清醒呼吸率、BB 充放电与起睡电量、BB 事件列表 | 活动量、久坐、能量消耗、恢复三角 —— 全部可直接实测 |
| `wellness-service/wellness/dailySummaryChart?date=` | 96×15 分钟格：steps、pushes、primaryActivityLevel（sedentary/active/sleeping/generic） | 连续久坐时长、活动时段分布，无需心率反推 |
| `userprofile-service/userprofile/personal-information` | 身高、体重、出生日期、年龄、时区、VO2 Max | 用户档案自动同步（实测 182 cm / 87 kg / 33 岁 / VO2 Max 39） |
| `.../personal-information/weight` | **体重样本序列**（含 bmi/bodyFat/muscleMass/visceralFat 字段，非体脂秤时为 null） | 体重趋势，实测样本：2025-11-06 90 kg → 2026-09-04 87 kg |
| `usersummary-service/usersummary/hydration/daily/{date}` | 饮水量、目标、出汗损失 | 补水依从性（本人未记录，valueInML 为 null） |
| `wellness-service/wellness/bodyBattery/reports/daily?startDate=&endDate=` | charged/drained + 全天曲线 | 替代基本为空的 `bb_events` |
| `sleep-service/sleep/dailySleepData?date=`（响应新增字段） | `avgOvernightHrv`、`breathingDisruptionData`、`restlessMomentsCount`、`avgSkinTempDeviationC`、`skinTempDataExists`、`bodyBatteryChange` | **睡眠呼吸紊乱代理**（呼吸暂停筛查线索）、皮肤温度偏差、夜间不安次数 |

**数据深度**：usersummary 可回溯至至少 2026-02；更早（2025 及以前）返回 null。
但**是间歇性的**——实测 6/1、7/1、7/20、8/15 无数据，8/1、9/1–9/5 有，说明取决于是否戴表/同步。
无数据日必须在简报里声明为缺口，**不要当成"零活动"**。

**警示**：`activitylist`（活动列表）只含按了开始键的记录，会**严重低估**真实活动量。
本人 2026-09-04 activity 合计仅数百米，而 usersummary 显示 12187 步 / 9.77 km。
凡涉及"今天动了没有/动了多少"的结论，一律以 usersummary 为准。

### fit-parse：解析 FIT 文件

```
uv run --python 3.11 --with fitparse python <SKILL_DIR>/scripts/fit_file_parser.py <file.fit> [--targets 1,5,10,last] [--output <JSON>] [--pretty] [--strict]
```

输出 `fit_header` / `parse.message_counts` / `parse.metrics`（hr/cadence/stride 可得性）/
`km_samples`。退出码：0 成功，1 解析失败，2 参数错误。

---

## 长周期批量查询

端到端目录结构与命令序列见 `references/advanced-usage.md`（区间接口注意 ≤28 天限制）。

## 输出格式

所有脚本输出标准化 JSON：成功 `{"status":"success","command":...,"data":...}`；
部分失败附 `warnings` 数组；空结果 `"data":[]` + 描述性 `message`；日期一律 `YYYY-MM-DD`。

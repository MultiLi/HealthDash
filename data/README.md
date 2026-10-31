# data/ —— 唯一的个人数据根

**这个目录整体不入库**（`.gitignore` 里一行 `data/*` 封死）。本文件是唯一例外，
它只描述结构、不含任何实测值。

## 为什么全部数据都放这里

只有一条规则：**有身体数据的东西，必须在 `data/` 下**。
新增数据目录不需要改 `.gitignore`，天然被排除。

## 结构

```
data/
  raw/                  Garmin 采集快照，一天一个 JSON（真相层，不可变）
  inbody/               体成分扫描（事件型）：raw/ + index.json + latest.json
  db/health.db          SQLite 主库（L1 观测 / L2 单元 / L3 断言 / 报告 / 日志镜像）
  reports/              每日判读产物 body_brief_YYYY-MM-DD.{md,json}
  reports/backtest/     回测快照与 diff
  logs/daily_log.jsonl  主观标量，一行一天（体重等）
  logs/events.jsonl     健康事件自由输入归档（症状/作息/情绪/检查）
  state/slots.json      定时任务续跑账本（目标日与欠账的唯一依据）
  tests/golden.json     金标用例（含真实历史数值，故不入库）
  archive/              已退役文档与会话流水的历史原件（清理归档，只查不改）
  profile.json          个人档案：年龄/性别/最大心率/静息心率
```

## 三层的职责边界

| 位置 | 是什么 | 丢了怎么办 |
|---|---|---|
| `raw/` `inbody/raw/` | **真相**：源数据快照 | 只能重拉，且历史时刻拉不回 → **必须备份** |
| `db/` `reports/` `site/index.json` | 派生：可确定性重建 | `index.py build --all` + `build_homepage_data.py` |
| `logs/` `reports/*.md` | **不可重建**：主观输入与判读叙述 | 只能靠 `tools/backup_health.py` 加密备份 |

最后一行是关键：**数据库能重建，主观日志和判读叙述不能**。所以备份的优先级
是 `logs/` 与 `reports/` 高于 `db/`——不要以为「有库就够了」。

## 重建顺序（换机器时）

```bash
# 1. 放回不可重建物：logs/ 与 reports/（从加密备份解出）
# 2. 重拉或放回 raw/ 与 inbody/raw/
# 3. 重建派生层
cd skills/body-health/scripts && python3 index.py build --all
cd ../../.. && python3 tools/build_homepage_data.py
# 4. 建个人档案（仓库只给了模板）
cp skills/body-health/references/profile.example.json data/profile.json
# 5. 自检
python3 tools/guard_no_data.py
```

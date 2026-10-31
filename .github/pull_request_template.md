## 改了什么

<!-- 一句话说清。说不清就说明这个 PR 太大了，拆开。 -->

## 影响面

- [ ] 只改文档/注释
- [ ] 改引擎或口径（`skills/body-health/scripts/`、`references/rules.json`）
- [ ] 改前端（`site/`）—— **合并后必须重新发布应用，否则线上还是旧版**
- [ ] 改采集通道（`skills/garmin-connect-cn/`）
- [ ] 改 CI / 仓库配置

## 自查（提交前就该是绿的）

- [ ] `python3 tools/ci/check.py` 全绿（本地）
- [ ] CI 的 `check` 变绿（PR 页面底部）
- [ ] 改动了 `site/` 的话：`python3 tools/build_homepage_data.py && node tools/smoke_site.mjs` 通过

## 按改动类型追加

<details><summary>改了口径 / rules.json</summary>

- [ ] `backtest.py diff` 的改判清单贴在下面
- [ ] `backtest.py golden` 仍 12/12

```
（贴改判清单）
```
</details>

<details><summary>改了前端</summary>

- [ ] 窄屏（390px）看过
- [ ] `probe_interactions.py` 通过
</details>

<details><summary>改了 CI / 仓库配置</summary>

- [ ] 在 worktree 里从干净基线跑过一次 `check.py`
- [ ] 没有需要 `ALLOW_MAIN_PUSH=1` 的理由了
</details>

## 已知缺口 / 风险

<!--
没验证到的部分如实写。「CI 绿」不覆盖依赖 data/ 的检查（smoke、golden 在 CI 里跳过），
也不覆盖真实数据下的表现。写下你确信的和你不确定的。
-->

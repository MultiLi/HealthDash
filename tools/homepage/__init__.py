# -*- coding: utf-8 -*-
"""健康主页数据构建包（tools/homepage/）。

把 reports/body_brief_*.json + data/raw/*.json 转成 site/data.json 的各层构建逻辑，
按功能拆分模块：
- paths.py    项目路径常量 + gtime 导入
- timeutil.py 时间换算与格式化（唯一来源，禁别处手搓）
- loaders.py  读取简报 / 原始日数据
- recovery.py 恢复层（昨夜睡眠 / HRV / 血氧 / 睡眠心率 / 准备度）
- load.py     消耗层（身体电量 / 压力 / 能量账本 / 全天心率）
- activity.py 活动层（步数 / 爬楼 / 小时分布 / 正式活动）
- scoring.py  每日综合指数
- summary.py  汇总层（达标率 / 均值 / 趋势行）
- brief.py    判读简报 payload
- build.py    主构建 build()

入口保持 tools/build_homepage_data.py 不变（23:30 自动化依赖该路径）。
"""

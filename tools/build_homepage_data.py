#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 reports/body_brief_*.json + data/raw/*.json 转成健康主页的数据层。

构建逻辑在 tools/homepage/ 包内，按层拆分：
  recovery / load / activity / scoring / summary / brief / build / indexlayer
本文件只是入口。

产出（2026-09-12 起分层，替代原先的单文件 data.json）：
  site/index.json            轻量逐日索引——首屏唯一载荷，日历/聚合/趋势全用它
  site/daily/YYYY-MM.json    完整日详情按月分片——点开某天才按需拉

用法: python3 tools/build_homepage_data.py [--push]
      --push  构建完顺手推到热更新端点（tools/push_data.py），**不需要重新上线**；
              端点不可用时只告警、不影响构建产物——静态副本照旧是兜底。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from homepage.build import build  # noqa: E402
from homepage.paths import SITE, load_rules  # noqa: E402
from homepage.indexlayer import write_index_layer  # noqa: E402
from homepage.assetver import stamp_all  # noqa: E402


def main():
    data = build()
    rules = load_rules()
    os.makedirs(SITE, exist_ok=True)
    idx, shards = write_index_layer(SITE, data, rules)

    # 发布走 CDN，URL 不变就会一直回旧副本：把「内容变了」翻译成「URL 变了」（见 assetver）
    ver = stamp_all(SITE)

    # 主观日志与判读产物镜像进 health.db（2026-10-31）。
    # 放在这里而不是采集脚本里：报告是随构建才重算的，库要跟着构建走才不会落后于文件。
    # 失败不阻断构建——呈现层不依赖库，库只是查询镜像。
    try:
        subprocess.run([sys.executable, os.path.join(HERE, "sync_local_store.py")],
                       check=False, stdout=subprocess.DEVNULL)
    except Exception:
        pass

    idx_path = os.path.join(SITE, "index.json")
    print("written:", idx_path, "| days:", len(idx["days"]),
          "| default:", idx["default_day"], "| span:", idx["span"]["n"])
    print("shards:", ", ".join("%s(%d)" % (m, len(v)) for m, v in sorted(shards.items())))
    print("fingerprint: 资源 v=%s（模块图 %d 个文件） 数据 b=%s（%d 个文件） index.html %s"
          % (ver["assets_version"], ver["import_files"], ver["data_token"],
             ver["data_files"], "已重写" if ver["index_changed"] else "无变化"))

    if "--push" in sys.argv:
        print("\n-- push 到热更新端点 --")
        rc = subprocess.call([sys.executable, os.path.join(HERE, "push_data.py")])
        if rc != 0:
            print("! 推送未成功；构建产物本身没问题，静态副本仍是兜底。", file=sys.stderr)
            sys.exit(rc)


if __name__ == "__main__":
    main()

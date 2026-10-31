#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互探针：用无头浏览器真的去点，抓「点完就冻死」这类只有交互层才暴露的问题。

为什么需要它（2026-09-16 事故）：
  切到「日」尺度、或点日历上任何没有记录的一格（今天 / 月初 / 跨月空格），
  整个标签页冻死。根因是 renderBrief 在「分片已加载、但没有这一天」时会异步重试自己，
  形成微任务自旋。**这种 bug 静态检查抓不到，DOM 桩也抓不到**——
  桩里的 addEventListener 是空实现，点不动按钮；只有真浏览器里真点一下才会现形。

怎么做：把 index.html 复制成临时探针页，注入一小段脚本按 URL 参数模拟点击，
再用 Chrome --dump-dom 把结果（探针把状态写在 <html> 的属性里）取回来比对。
每个用例带**看门狗**：真冻死就 kill 掉并判 HUNG，不会把整个探针一起挂住。

用法:
  python3 tools/probe_interactions.py
  python3 tools/probe_interactions.py --timeout 40 --keep
  python3 tools/probe_interactions.py --case cell-no-data      # 只跑某个用例
退出码非 0 表示有用例失败（冻死 / 探针没跑完 / 文案不符预期）。
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import sys
import time
import urllib.request
from urllib.error import URLError

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")
PROBE_NAME = "_probe_interactions.html"
CHROME_DEFAULT = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 注入脚本：按 ?act= 里的动作序列去点，然后把状态写进 <html> 的属性，供 --dump-dom 取回。
PROBE_JS = """
<script>
(function () {
  var q = new URLSearchParams(location.search);
  var act = q.get("act") || "none";
  var delay = parseInt(q.get("d") || "1200", 10);
  function set(k, v) { document.documentElement.setAttribute(k, v); }
  function cs(sel) { var el = document.querySelector(sel); if (!el) return "NO-ELEM"; el.click(); return "ok"; }
  function run(seq) {
    var log = [];
    seq.split(",").forEach(function (op) {
      var p = op.split(":");
      if (p[0] === "scale") log.push("scale-" + p[1] + ":" + cs('[data-scale="' + p[1] + '"]'));
      else if (p[0] === "cell") log.push("cell-" + p[1] + ":" + cs('.cal-cell[data-date="' + p[1] + '"]'));
      else if (p[0] === "detail") log.push("detail:" + cs(".mx-more"));
      else if (p[0] === "month") log.push("month-" + p[1] + ":" + cs('[data-month-shift="' + p[1] + '"]'));
    });
    return log.join(" ");
  }
  set("data-probe", "loaded");
  setTimeout(function () { set("data-click", run(act)); }, delay);
  setTimeout(function () {
    set("data-probe", "ALIVE");
    set("data-click", (document.documentElement.getAttribute("data-click") || "") + " | 全部执行完");
    set("data-brief-sub", ((document.getElementById("brief-sub") || {}).textContent || "").slice(0, 60));
    set("data-brief-body", ((document.querySelector("#brief-grid") || {}).textContent || "").slice(0, 60));
    set("data-scope", ((document.getElementById("unit-scope") || {}).textContent || "").slice(0, 44));
    set("data-score", ((document.getElementById("unit-score") || {}).textContent || "").slice(0, 40));
    set("data-detail", ((document.querySelector(".mx-detail") || {}).textContent || "(未展开)").slice(0, 50));
    set("data-loaderr", document.querySelector(".load-error") ? "有" : "无");
  }, delay + 3000);
})();
</script>
"""


def fail(msg):
    print("! " + msg, file=sys.stderr)
    sys.exit(2)


def read_index():
    with open(os.path.join(SITE, "index.json"), encoding="utf-8") as f:
        return json.load(f)


def build_cases(ix):
    """用例从真实索引推出来，不写死日期——数据变了探针也不会失效。"""
    dates = [r["date"] for r in ix["days"]]
    with_row = set(dates)
    month = ix["default_day"][:7]
    y, m = int(month[:4]), int(month[5:7])
    import calendar
    ndays = calendar.monthrange(y, m)[1]
    all_days = ["%s-%02d" % (month, d) for d in range(1, ndays + 1)]
    no_data = next((d for d in all_days if d not in with_row), None)
    with_data = ix["default_day"]
    today = ix.get("today") or ""
    prev_last = "%04d-%02d-%02d" % (y - (1 if m == 1 else 0), 12 if m == 1 else m - 1,
                                    calendar.monthrange(y - (1 if m == 1 else 0), 12 if m == 1 else m - 1)[1])

    cases = [
        ("基线（不点）", "none", None, "页面能加载完"),
        ("切到日尺度", "scale:day", r"分析日|没有采集记录", "切尺度不冻死，且落在有数据的那天"),
        ("点有数据的一天", "cell:%s" % with_data, r"分析日", "点有数据的一天 → 出判读正文"),
    ]
    if no_data:
        cases += [
            ("点无数据的一天", "cell:%s" % no_data, r"没有采集记录", "过去的空日 → 明确空态（不是转圈）"),
            ("无数据日展开细分", "cell:%s,detail" % no_data, None, "细分给空态，不误报「加载失败」"),
        ]
    cases += [
        ("点跨月空格", "cell:%s" % prev_last, r"没有采集记录", "跨月空格 → 空态，不冻死"),
        ("有数据日展开细分", "cell:%s,detail" % with_data, r".", "细分正常产出"),
        ("连续狂点 尺/日/周/月/日", "cell:%s,scale:week,scale:month,scale:day,scale:day" % with_data,
         None, "连点不冻死，状态自洽"),
    ]
    return cases, no_data, today, with_row


def wait_health(port, timeout=15.0):
    url = "http://127.0.0.1:%d/api/health" % port
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with op.open(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (URLError, OSError):
            time.sleep(0.3)
    return False


# ⚠️ 血的教训（2026-09-16，浪费了将近一小时）：
#   本机上**只要给无头 Chrome 传了 --user-data-dir 指向一个新建目录，它就永远不返回**，
#   无论是否每例一个。症状是每个用例都被看门狗判成 HUNG——**连「基线不点」也 HUNG**。
#   那不是页面挂了，是测量工具坏了。写探针时一定要先跑一个「基线不点」用例做自检：
#   基线都能 HUNG，就该怀疑工具而不是被测对象。
#   缓存隔离改用 `--incognito`：既复用默认 profile（唯一秒回的姿势），又不吃旧 js 磁盘缓存。


def run_case(chrome, url, timeout, fresh_profile=False, profile_dir=None):
    """返回 (hung, dom)。看门狗：真冻死就 kill，不让探针跟着挂。"""
    profile = tempfile.mkdtemp(prefix="health-probe-") if fresh_profile else profile_dir
    try:
        argv = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-proxy-server"]
        if profile:
            argv.append("--user-data-dir=" + profile)
        else:
            # 不指定 profile = 复用默认 profile。**本机唯一不卡死的姿势**（见文件头说明）；
            # 无痕模式负责别吃到旧 js 的磁盘缓存。
            argv.append("--incognito")
        argv += [
            "--disk-cache-size=1",
            # 防「首次运行」联网初始化：缺了它们无头 Chrome 也可能长时间不返回
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-component-update",
            "--disable-sync", "--disable-extensions",
            "--virtual-time-budget=15000", "--dump-dom", url,
        ]
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # ⚠️ 必须用 communicate()，不能「轮询 poll 再 read」。
        # --dump-dom 的输出走管道，而管道缓冲区只有 ~64KB：页面一旦长到超过它，
        # Chrome 就阻塞在 write 上永远不退出，看门狗于是把**每一个**用例都判成 HUNG
        # ——包括不需要点击的基线，看起来像产品冻死，其实是测量工具自锁
        # （2026-11-01 实证：加了逐日矩阵与判读流水后 DOM 从 55KB 涨到 69KB，
        #  6 个用例全 HUNG；而同样参数手工跑 curl 式 dump-dom 只花 2 秒）。
        # communicate() 会边读边等，缓冲区不会写满。
        try:
            out, _ = p.communicate(timeout=timeout)
            return False, out.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                p.communicate(timeout=5)
            except Exception:
                pass
            return True, ""
    finally:
        # 共享 profile 要活到最后一个用例，这里只在「一次性 profile」模式下删
        if fresh_profile:
            shutil.rmtree(profile, ignore_errors=True)


def attr(dom, key):
    m = re.search(key + r'="([^"]*)"', dom)
    return m.group(1) if m else "(缺失)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", default=CHROME_DEFAULT)
    ap.add_argument("--port", type=int, default=8810)
    ap.add_argument("--timeout", type=float, default=45.0, help="单用例看门狗秒数")
    ap.add_argument("--case", default=None, help="只跑名字含该子串的用例")
    ap.add_argument("--keep", action="store_true", help="保留探针页便于手工排查")
    ap.add_argument("--fresh-profile", action="store_true",
                    help="每个用例新建 --user-data-dir。本机实测这样会让无头 Chrome 全部卡死（表现为所有用例 HUNG），别用；"
                         "默认复用默认 profile + 无痕隔离缓存")
    args = ap.parse_args()

    if not os.path.exists(args.chrome):
        fail("找不到 Chrome：%s（用 --chrome 指定）" % args.chrome)

    ix = read_index()
    cases, no_data, today, with_row = build_cases(ix)

    probe_path = os.path.join(SITE, PROBE_NAME)
    src = open(os.path.join(SITE, "index.html"), encoding="utf-8").read()
    # 探针页去掉外网字体：无头浏览器每次都要等 fonts.googleapis.com，
    # 既慢又不确定（网络一抖就误报 HUNG）。交互逻辑与字体无关。
    src = re.sub(r'\s*<link[^>]*fonts\.(googleapis|gstatic)\.com[^>]*>', "", src)
    marker = '<script type="module" src="js/main.js'
    if marker not in src:
        fail("index.html 里找不到 main.js 的 script 标签")
    open(probe_path, "w", encoding="utf-8").write(src.replace(marker, PROBE_JS + marker))

    env = dict(os.environ)
    env.update({"PORT": str(args.port), "no_proxy": "*", "NO_PROXY": "*"})
    srv = subprocess.Popen([sys.executable, os.path.join(SITE, "server.py")],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           preexec_fn=os.setsid)

    failed = []
    try:
        if not wait_health(args.port):
            fail("本地服务起不来（PORT=%d），探针无法运行" % args.port)
        print("数据：%s · 锚点月 %s · 无数据日 %s · 今天 %s\n"
              % (ix["default_day"], ix["default_day"][:7], no_data or "（无）", today or "（无）"))
        print("%-24s %-8s %-26s %s" % ("用例", "结果", "点击", "状态"))
        print("-" * 108)
        ran = 0
        for name, act, expect, why in cases:
            if args.case and args.case not in name:
                continue
            ran += 1
            url = "http://127.0.0.1:%d/%s?act=%s" % (args.port, PROBE_NAME, act)
            hung, dom = run_case(args.chrome, url, args.timeout, args.fresh_profile)
            if hung:
                print("%-24s %-8s %-26s %s" % (name, "HUNG!!", act, "页面被挂死（看门狗 kill）"))
                failed.append(name + "：挂死")
                continue
            probe = attr(dom, "data-probe")
            clicks = attr(dom, "data-click")
            brief = attr(dom, "data-brief-sub")
            loaderr = attr(dom, "data-loaderr")
            problems = []
            if probe != "ALIVE":
                problems.append("探针没跑完（probe=%s）" % probe)
            if loaderr == "有":
                problems.append("页面出现 load-error")
            if "NO-ELEM" in clicks:
                problems.append("点击目标不存在：%s" % clicks)
            if expect and not re.search(expect, brief):
                problems.append("文案不符：brief=%r 期望 /%s/" % (brief, expect))
            state = ("OK" if not problems else "｜".join(problems))
            print("%-24s %-8s %-26s %s" % (name, "ok" if not problems else "FAIL", clicks.split(" | ")[0], state))
            if problems:
                failed.append(name + "：" + "；".join(problems))

        print("-" * 108)
        if failed:
            print("失败 %d / %d：" % (len(failed), ran))
            for f in failed:
                print("  ✗ " + f)
        else:
            print("全部通过：%d 项（无挂死、无 load-error、文案符合预期）" % ran)
    finally:
        if not args.keep:
            try:
                os.remove(probe_path)
            except OSError:
                pass
        try:
            os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
        except Exception:
            srv.kill()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

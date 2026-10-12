#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全球热点快报 / Global Buzz
==========================

一个零第三方依赖的 RSS 聚合器。它做三件事：
  1. 并行抓取一组新闻源的 RSS/Atom；
  2. 解析、去重、按分类归并、按时间倒序；
  3. 生成一个自包含的静态站点（HTML + JSON）。

设计约束（重要）：
  - 只用 Python 3 标准库。目标机器是国内的轻量服务器，装第三方包既慢又容易失败。
  - 任何一个源抓取失败都不能中断整轮生成，失败信息记入 meta.json。
  - 输出是纯静态文件，交给已有的 Caddy / nginx 直接托管，不需要常驻进程。

用法：
    python3 buzz.py                 # 生成到 ./dist
    BUZZ_OUT=/srv/site/buzz python3 buzz.py
"""

import html
import json
import os
import re
import sys
import time
import socket
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from string import Template
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------- 基本配置

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15          # 单个源抓取超时（秒）
MAX_WORKERS = 8       # 并发抓取线程数
PER_SOURCE_CAP = 15   # 每个源最多保留多少条
PER_CAT_CAP = 60      # 每个分类最多保留多少条
CST = timezone(timedelta(hours=8))

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("BUZZ_OUT", os.path.join(HERE, "dist"))
NO_TRANS = os.environ.get("BUZZ_NO_TRANS", "").lower() in ("1", "true", "yes")

# 翻译：默认用 Google 的免密钥 gtx 接口（需要出网，国内走代理）；
# 任何失败都降级为「不翻译」，保证整轮生成不会卡死。
GT_BASE = "https://translate.googleapis.com/translate_a/single"
TRANS_CACHE = os.path.join(HERE, "translations.json")
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# ---------------------------------------------------------------- 分类定义

CATEGORIES = [
    ("funny", "奇葩搞笑", "怪新闻、讽刺、荒唐事", "#ff8a3d"),
    ("world", "全球要闻", "国际主流媒体头条", "#4aa8ff"),
    ("tech", "科技极客", "技术圈与产品圈", "#5ce1a4"),
    ("cn", "中文圈", "国内科技媒体", "#c48cff"),
]

# ---------------------------------------------------------------- 新闻源清单
#
# 这些源是逐一在目标服务器（腾讯云上海轻量，大陆线路）上实测过可达性的，
# 状态码写在每行注释里。标注 "blocked" 的源在中国大陆服务器上连接失败，
# 一律不收录，避免生成时反复超时。

SOURCES = [
    # ---- 奇葩搞笑 -------------------------------------------------------
    dict(id="onion", cat="funny", name="The Onion",
         home="https://www.theonion.com/",
         url="https://www.theonion.com/rss",
         note="美国最老牌的讽刺假新闻媒体，1988 年创刊 · 实测可达"),   # 200
    dict(id="boingboing", cat="funny", name="Boing Boing",
         home="https://boingboing.net/",
         url="https://boingboing.net/feed",
         note="怪奇科技文化博客，1988 年创刊 · 实测可达"),             # 200
    dict(id="atlasobscura", cat="funny", name="Atlas Obscura",
         home="https://www.atlasobscura.com/",
         url="https://www.atlasobscura.com/feeds/latest",
         note="全世界的冷门奇景与怪知识 · 实测可达"),                   # 200
    dict(id="snopes", cat="funny", name="Snopes",
         home="https://www.snopes.com/",
         url="https://www.snopes.com/feed/",
         note="事实核查网站，专门追网络谣言与都市传说 · 实测可达"),     # 200

    # ---- 全球要闻 -------------------------------------------------------
    dict(id="foxnews", cat="world", name="Fox News",
         home="https://www.foxnews.com/",
         url="https://moxie.foxnews.com/google-publisher/latest.xml",
         note="美国福克斯新闻最新稿 · 实测可达"),                       # 200
    dict(id="npr", cat="world", name="NPR",
         home="https://www.npr.org/",
         url="https://www.npr.org/rss/rss.php?id=1001",
         note="美国国家公共电台头条 · 实测可达"),                       # 200
    dict(id="skynews", cat="world", name="Sky News World",
         home="https://news.sky.com/world",
         url="https://feeds.skynews.com/feeds/rss/world.xml",
         note="英国天空新闻国际版 · 实测可达"),                         # 200
    dict(id="france24", cat="world", name="France 24",
         home="https://www.france24.com/en/",
         url="https://www.france24.com/en/rss",
         note="法国 24 小时国际新闻 · 实测可达"),                       # 200
    dict(id="sciencealert", cat="world", name="ScienceAlert",
         home="https://www.sciencealert.com/",
         url="https://www.sciencealert.com/rss",
         note="科普媒体，选题偏猎奇 · 实测可达"),                       # 200

    # ---- 科技极客 -------------------------------------------------------
    dict(id="techmeme", cat="tech", name="Techmeme",
         home="https://www.techmeme.com/",
         url="https://www.techmeme.com/feed.xml",
         note="硅谷科技新闻头条聚合 · 实测可达"),                       # 200
    dict(id="arstechnica", cat="tech", name="Ars Technica",
         home="https://arstechnica.com/",
         url="https://feeds.arstechnica.com/arstechnica/index",
         note="老牌硬核科技媒体 · 实测可达"),                           # 200
    dict(id="theverge", cat="tech", name="The Verge",
         home="https://www.theverge.com/",
         url="https://www.theverge.com/rss/index.xml",
         note="消费科技与数码文化 · 实测可达"),                         # 200
    dict(id="wired", cat="tech", name="WIRED",
         home="https://www.wired.com/",
         url="https://www.wired.com/feed/rss",
         note="连线杂志 · 实测可达"),                                   # 200
    dict(id="hnrss", cat="tech", name="Hacker News",
         home="https://news.ycombinator.com/",
         url="https://hnrss.org/frontpage",
         note="HN 首页（经 hnrss.org 中转，直连被墙）· 实测可达"),      # 200
    dict(id="lobsters", cat="tech", name="Lobsters",
         home="https://lobste.rs/",
         url="https://lobste.rs/rss",
         note="技术链接社区 · 实测可达"),                               # 200
    dict(id="producthunt", cat="tech", name="Product Hunt",
         home="https://www.producthunt.com/",
         url="https://www.producthunt.com/feed",
         note="新产品发布榜 · 实测可达"),                               # 200
    dict(id="lifehacker", cat="tech", name="Lifehacker",
         home="https://lifehacker.com/",
         url="https://lifehacker.com/feed/rss",
         note="生活效率与工具 · 实测可达（源体积大，已截断）"),         # 200

    # ---- 中文圈 ---------------------------------------------------------
    dict(id="sspai", cat="cn", name="少数派",
         home="https://sspai.com/",
         url="https://sspai.com/feed",
         note="效率工具与数字生活 · 实测可达"),                         # 200
    dict(id="ithome", cat="cn", name="IT之家",
         home="https://www.ithome.com/",
         url="https://www.ithome.com/rss/",
         note="国内科技快讯 · 实测可达"),                               # 200
]

# ---------------------------------------------------------------- 抓取与解析


def fetch(url):
    """抓取一个 URL，返回 bytes。禁用压缩以免拿到 gzip 二进制。"""
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "identity",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    })
    with urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _localname(tag):
    """去掉 XML 命名空间前缀，统一转小写。"""
    return tag.rsplit("}", 1)[-1].lower()


def _find_text(node, names):
    """在 node 的直接子节点里找第一个名字命中的，返回其文本。"""
    for child in node:
        if _localname(child.tag) in names:
            text = (child.text or "").strip()
            if text:
                return text
    return ""


def _find_link(node):
    """RSS 的 link 是文本；Atom 的 link 是 href 属性。两种都要兼容。"""
    fallback = ""
    for child in node:
        if _localname(child.tag) != "link":
            continue
        href = child.get("href")
        rel = (child.get("rel") or "alternate").lower()
        if href:
            if rel == "alternate":
                return href.strip()
            fallback = fallback or href.strip()
        elif (child.text or "").strip():
            return child.text.strip()
    return fallback


def _parse_time(node):
    """把 pubDate / published / updated / dc:date 统一成 Unix 时间戳。"""
    for child in node:
        if _localname(child.tag) in ("pubdate", "published", "updated", "date", "created"):
            raw = (child.text or "").strip()
            if not raw:
                continue
            try:
                dt = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                dt = None
            if dt is None:
                iso = raw.replace("Z", "+00:00")
                try:
                    dt = datetime.fromisoformat(iso)
                except ValueError:
                    continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
    return 0


_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def parse_feed(raw):
    """解析 RSS 2.0 / Atom。遇到非法字符先清一遍再重试。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        text = raw.decode("utf-8", "replace")
        text = _ILLEGAL.sub("", text)
        root = ET.fromstring(text)

    items = []
    for node in root.iter():
        if _localname(node.tag) not in ("item", "entry"):
            continue
        title = _find_text(node, ("title",))
        link = _find_link(node)
        if not title or not link:
            continue
        items.append({
            "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
            "link": link,
            "ts": _parse_time(node),
        })
    return items


def collect_one(src):
    """抓取并解析单个源。返回 (source_id, items, error_or_None)。"""
    try:
        raw = fetch(src["url"])
        items = parse_feed(raw)
        now = int(time.time())
        for it in items:
            if it["ts"] <= 0 or it["ts"] > now + 3600:
                it["ts"] = now
            it["src"] = src["name"]
            it["cat"] = src["cat"]
            it["src_id"] = src["id"]
        return src["id"], items[:PER_SOURCE_CAP], None
    except HTTPError as exc:
        return src["id"], [], "HTTP %s" % exc.code
    except URLError as exc:
        return src["id"], [], "网络错误 %s" % (exc.reason,)
    except socket.timeout:
        return src["id"], [], "超时"
    except ET.ParseError as exc:
        return src["id"], [], "XML 解析失败 %s" % exc
    except Exception as exc:  # noqa: BLE001 —— 单源失败绝不能中断整轮
        return src["id"], [], "%s: %s" % (type(exc).__name__, exc)


def collect_all():
    """并发抓全部源，返回 (items, stats)。"""
    items, stats = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(collect_one, s): s for s in SOURCES}
        for fut in as_completed(futures):
            src = futures[fut]
            _sid, got, err = fut.result()
            by_id = {s["id"]: s for s in SOURCES}
            stats.append({
                "id": src["id"], "name": src["name"], "cat": src["cat"],
                "url": src["url"], "home": src.get("home", ""),
                "note": src.get("note", ""),
                "count": len(got), "error": err,
            })
            if err:
                print("  [x] %-16s %s" % (src["name"], err), file=sys.stderr)
            else:
                print("  [v] %-16s %d 条" % (src["name"], len(got)), file=sys.stderr)
            items.extend(got)
    order = {s["id"]: i for i, s in enumerate(SOURCES)}
    stats.sort(key=lambda s: order.get(s["id"], 999))
    return items, stats


def dedupe(items):
    """同一个链接或同一个标题只留最新的一条。"""
    seen_link, seen_title, out = set(), {}, []
    for it in sorted(items, key=lambda x: x["ts"], reverse=True):
        key = it["link"].split("?")[0]
        tkey = re.sub(r"\W+", "", it["title"]).lower()[:60]
        if key in seen_link or tkey in seen_title:
            continue
        seen_link.add(key)
        seen_title[tkey] = 1
        out.append(it)
    return out


# ---------------------------------------------------------------- 翻译

def load_cache():
    try:
        with open(TRANS_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    tmp = TRANS_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, TRANS_CACHE)


def _translate_one(title):
    """翻译单个英文标题为中文，返回中文串。失败抛异常由调用方兜底为空。"""
    q = Request(
        "?".join([GT_BASE,
                   urlencode({"client": "gtx", "sl": "en", "tl": "zh-CN",
                              "dt": "t", "q": title})]),
        headers={"User-Agent": UA},
    )
    with urlopen(q, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return data[0][0][0].strip()


def translate_titles(titles):
    """批量翻译英文标题为中文。命中缓存直接返回，失败项留空字符串。"""
    cache = load_cache()
    need = [t for t in titles if t and not CJK_RE.search(t) and t not in cache]
    if need:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(_translate_one, t): t for t in need}
            for fut in as_completed(futs):
                src = futs[fut]
                try:
                    cache[src] = fut.result()
                except (URLError, HTTPError, socket.timeout, ValueError,
                        KeyError, IndexError, json.JSONDecodeError) as exc:
                    print("  [!] 翻译跳过：%s" % exc, file=sys.stderr)
        save_cache(cache)
    return [cache.get(t, "") for t in titles]


# ---------------------------------------------------------------- 渲染


def human_time(ts):
    delta = int(time.time() - ts)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return "%d 分钟前" % (delta // 60)
    if delta < 86400:
        return "%d 小时前" % (delta // 3600)
    if delta < 86400 * 7:
        return "%d 天前" % (delta // 86400)
    return datetime.fromtimestamp(ts, CST).strftime("%m-%d")


def esc(text):
    return html.escape(text or "", quote=True)


def domain_of(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else ""


CARD = Template("""      <article class="card" data-src="$src" data-text="$hay">
        <div class="meta"><span class="badge">$src</span><time>$ago</time></div>
        <h3><a href="$link" target="_blank" rel="noopener noreferrer">$zh</a></h3>
$enline      </article>
""")

SECTION = Template("""  <section class="cat" id="cat-$id" data-cat="$id">
    <div class="cat-head">
      <h2><span class="dot"></span>$label</h2>
      <span class="cat-sub">$sub · $count 条</span>
    </div>
    <div class="grid">
$cards    </div>
  </section>
""")


def render_sections(items):
    blocks = []
    for cid, label, sub, color in CATEGORIES:
        rows = [i for i in items if i["cat"] == cid][:PER_CAT_CAP]
        cards = []
        for it in rows:
            zh = it.get("zh") or ""
            en = it["title"]
            main = zh if zh else en
            en_line = ('        <p class="orig">%s</p>\n' % esc(en)) if (zh and zh != en) else ""
            cards.append(CARD.substitute(
                src=esc(it["src"]),
                ago=esc(human_time(it["ts"])),
                link=esc(it["link"]),
                zh=esc(main),
                enline=en_line,
                hay=esc((main + " " + en + " " + it["src"]).lower()),
            ))
        blocks.append(SECTION.substitute(
            id=cid, label=esc(label), sub=esc(sub), count=len(rows),
            cards="".join(cards) or '      <p class="empty">这一轮没有抓到内容。</p>\n',
        ))
    return "".join(blocks)


TABS_TPL = Template("""    <button class="tab$active" data-cat="$id">$label
      <span class="n">$sum</span></button>
""")


def render_tabs(items):
    out = ['    <button class="tab active" data-cat="all">全部'
           '<span class="n">%d</span></button>\n' % len(items)]
    for cid, label, _sub, _color in CATEGORIES:
        n = sum(1 for i in items if i["cat"] == cid)
        out.append(TABS_TPL.substitute(id=cid, label=esc(label), sum=n, active=""))
    return "".join(out)


SOURCE_ROW = Template("""        <tr><td>$name</td><td><a href="$url" target="_blank" rel="noopener">$host</a></td>
          <td class="ok">$count</td><td class="note">$note</td></tr>
""")


def render_sources(stats):
    rows = []
    for s in sorted(stats, key=lambda x: x["name"]):
        rows.append(SOURCE_ROW.substitute(
            name=esc(s["name"]),
            url=esc(s["url"]),
            host=esc(domain_of(s["url"])),
            count=esc(str(s["count"])) if not s["error"] else esc(s["error"]),
            note=esc(s.get("note", "")),
        ))
    return "".join(rows)


PAGE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全球热点快报 · 奇葩搞笑与全球要闻</title>
<meta name="description" content="每小时更新的全球热点快报：奇葩搞笑新闻、国际主流媒体头条、科技极客资讯，一处读完。共聚合 $n 条来自 $src 个信源的内容。">
<meta name="robots" content="index,follow,max-image-preview:large">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%94%A5%3C/text%3E%3C/svg%3E">
<meta property="og:title" content="全球热点快报 · 奇葩搞笑与全球要闻">
<meta property="og:description" content="聚合 $src 个信源、$n 条内容，每 30 分钟自动刷新。">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1017; --bg2:#141924; --card:#161c28; --line:#232b3a;
  --ink:#e8edf6; --ink2:#9aa7bd; --ink3:#66738a;
  --accent:#ff8a3d; --accent2:#4aa8ff;
}
html{-webkit-text-size-adjust:100%}
body{
  background:radial-gradient(1200px 600px at 50% -200px,#1b2436 0%,var(--bg) 60%) no-repeat,var(--bg);
  color:var(--ink);min-height:100vh;
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
}
a{color:inherit;text-decoration:none}
.wrap{max-width:1180px;margin:0 auto;padding:0 18px}
header{padding:34px 0 18px;border-bottom:1px solid var(--line)}
h1{font-size:26px;letter-spacing:.5px;display:flex;align-items:center;gap:11px;flex-wrap:wrap}
h1 .flag{font-size:13px;font-weight:600;color:var(--accent);border:1px solid rgba(255,138,61,.4);
  background:rgba(255,138,61,.08);padding:3px 9px;border-radius:999px;letter-spacing:1px}
.sub{color:var(--ink2);font-size:13.5px;margin-top:9px}
.sub b{color:var(--ink);font-weight:600}
.bar{position:sticky;top:0;z-index:20;background:rgba(13,16,23,.9);
  backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.bar-in{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 0}
.tabs{display:flex;gap:7px;flex-wrap:wrap}
.tab{background:var(--card);border:1px solid var(--line);color:var(--ink2);
  padding:7px 13px;border-radius:9px;cursor:pointer;font-size:13.5px;
  font-family:inherit;display:flex;align-items:center;gap:7px;transition:.16s}
.tab:hover{color:var(--ink);border-color:#31405a}
.tab.active{color:#0d1017;background:var(--accent);border-color:var(--accent);font-weight:600}
.tab .n{font-size:11.5px;opacity:.72;font-variant-numeric:tabular-nums}
input[type=search]{flex:1;min-width:160px;background:var(--card);border:1px solid var(--line);
  color:var(--ink);padding:8px 13px;border-radius:9px;font-size:13.5px;font-family:inherit;outline:none}
input[type=search]:focus{border-color:#3a4a68}
main{padding:24px 0 60px}
.cat{margin-bottom:34px}
.cat-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:14px;
  padding-bottom:9px;border-bottom:1px solid var(--line)}
.cat-head h2{font-size:17px;display:flex;align-items:center;gap:9px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block}
#cat-world .dot{background:var(--accent2)}
#cat-tech .dot{background:#5ce1a4}
#cat-cn .dot{background:#c48cff}
.cat-sub{color:var(--ink3);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:11px}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;
  padding:13px 15px;transition:.16s;display:flex;flex-direction:column;gap:9px}
.card:hover{border-color:#3a4a68;background:#1a2130;transform:translateY(-2px)}
.meta{display:flex;align-items:center;justify-content:space-between;gap:8px}
.badge{font-size:11.5px;color:var(--ink2);background:#1e2634;border:1px solid var(--line);
  padding:2px 8px;border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card time{font-size:11.5px;color:var(--ink3);white-space:nowrap;flex-shrink:0}
.card h3{font-size:14.5px;font-weight:500;line-height:1.5}
.card h3 a:hover{color:var(--accent2)}
.orig{font-size:12px;color:var(--ink3);line-height:1.45;margin-top:2px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.empty{color:var(--ink3);font-size:13.5px}
.hidden{display:none !important}
footer{border-top:1px solid var(--line);padding:26px 0 44px;color:var(--ink3);font-size:12.5px}
footer h2{font-size:14px;color:var(--ink2);margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td{padding:6px 10px 6px 0;border-bottom:1px solid #1a2130;vertical-align:top}
td:first-child{color:var(--ink2);white-space:nowrap}
td a{color:var(--accent2)}
td.ok{color:#5ce1a4;font-variant-numeric:tabular-nums;white-space:nowrap}
td.note{color:var(--ink3)}
.tips{margin-top:16px;line-height:1.9}
@media(max-width:640px){
  header{padding:24px 0 14px}
  h1{font-size:21px}
  .grid{grid-template-columns:1fr}
  .card:hover{transform:none}
}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>全球热点快报 <span class="flag">GLOBAL BUZZ</span></h1>
    <p class="sub">已聚合 <b>$n</b> 条内容，来自 <b>$src</b> 个信源 ·
      最后更新 <b>$updated</b> · 标题已机器翻译成中文</p>
  </div>
</header>

<div class="bar">
  <div class="wrap bar-in">
    <div class="tabs">
$tabs    </div>
    <input type="search" id="q" placeholder="搜标题或来源…" autocomplete="off">
  </div>
</div>

<main class="wrap">
$sections  <p class="empty hidden" id="noresult">没有匹配的内容。</p>
</main>

<footer>
  <div class="wrap">
    <h2>信源清单（$src 个）</h2>
    <table>
      <tbody>
$sources      </tbody>
    </table>
    <p class="tips">
      本页由 <code>buzz.py</code> 自动生成，内容版权归各原始媒体所有。<br>
      标题已用机器翻译成中文（Google 免密钥接口，逐条缓存，失败则回退英文原文）。<br>
      排除的信源：Fark、Reddit、BBC、纽约时报、卫报、半岛电视台 —— 这些站点在中国大陆服务器上
      连接失败或被反爬拦截，已从清单中移除，以免每轮生成都空跑超时。<br>
      生成时间 $generated
    </p>
  </div>
</footer>

<script>
(function(){
  'use strict';
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var cats = Array.prototype.slice.call(document.querySelectorAll('.cat'));
  var q = document.getElementById('q');
  var nores = document.getElementById('noresult');
  var current = 'all';

  function apply(){
    var kw = (q.value || '').trim().toLowerCase();
    var shown = 0;
    cats.forEach(function(sec){
      var catOk = (current === 'all' || sec.dataset.cat === current);
      var cards = sec.querySelectorAll('.card');
      var visible = 0;
      cards.forEach(function(c){
        var hit = !kw || (c.dataset.text || '').indexOf(kw) !== -1;
        var on = catOk && hit;
        c.classList.toggle('hidden', !on);
        if (on) visible++;
      });
      sec.classList.toggle('hidden', visible === 0);
      shown += visible;
    });
    nores.classList.toggle('hidden', shown !== 0);
  }

  tabs.forEach(function(t){
    t.addEventListener('click', function(){
      current = t.dataset.cat;
      tabs.forEach(function(o){ o.classList.toggle('active', o === t); });
      apply();
    });
  });
  q.addEventListener('input', apply);
})();
</script>
</body>
</html>
""")


def render_page(items, stats, started):
    now = datetime.now(CST)
    return PAGE.substitute(
        n=len(items),
        src=len([s for s in stats if not s["error"]]),
        updated=now.strftime("%m-%d %H:%M"),
        generated=now.strftime("%Y-%m-%d %H:%M:%S"),
        tabs=render_tabs(items),
        sections=render_sections(items),
        sources=render_sources(stats),
    )


# ---------------------------------------------------------------- 主流程


def main():
    started = time.time()
    print("全球热点快报 —— 开始抓取 %d 个信源" % len(SOURCES), file=sys.stderr)

    items, stats = collect_all()
    items = dedupe(items)
    items.sort(key=lambda x: x["ts"], reverse=True)

    if NO_TRANS:
        print("翻译已关闭（BUZZ_NO_TRANS 已设置）", file=sys.stderr)
        for it in items:
            it["zh"] = ""
    else:
        zh_list = translate_titles([it["title"] for it in items])
        for it, zh in zip(items, zh_list):
            it["zh"] = zh

    os.makedirs(OUT_DIR, exist_ok=True)
    page = render_page(items, stats, started)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    with open(os.path.join(OUT_DIR, "data.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "generated": datetime.now(CST).isoformat(),
            "total": len(items),
            "items": items,
        }, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "generated": datetime.now(CST).isoformat(),
            "elapsed_sec": round(time.time() - started, 1),
            "total": len(items),
            "ok_sources": len([s for s in stats if not s["error"]]),
            "sources": stats,
        }, fh, ensure_ascii=False, indent=1)

    ok = len([s for s in stats if not s["error"]])
    print("完成：%d 条 / %d 个信源可用 / 耗时 %.1fs → %s"
          % (len(items), ok, time.time() - started, OUT_DIR), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# 全球热点快报 / Global Buzz

一个零第三方依赖的 RSS 聚合站：抓取 → 标题机器翻译成中文 → 去重 → 按分类归并 → 生成一个自包含的静态页面。
没有数据库、没有 Node、没有 Docker，只有 Python 标准库。

线上地址：<https://global-buzz.app.workbuddy.host/>

## 它长什么样

深色单页，四个分类标签 + 一个搜索框。**中文为主标题、英文原文为灰字副标题**：

| 分类 | 内容 |
|---|---|
| 奇葩搞笑 | The Onion（讽刺假新闻）、Boing Boing（怪奇）、Atlas Obscura（冷门奇景）、Snopes（谣言核查） |
| 全球要闻 | Fox News、NPR、Sky News World、France 24、ScienceAlert |
| 科技极客 | Techmeme、Ars Technica、The Verge、WIRED、Hacker News、Lobsters、Product Hunt、Lifehacker |
| 中文圈 | 少数派、IT之家 |

## 翻译是怎么做的

用 Google 的免密钥接口 `translate.googleapis.com/translate_a/single?client=gtx`，逐条并行翻译，
结果缓存在本地 `translations.json`（已 gitignore），下次只翻新出现的标题。
接口挂了或者没网就自动降级成英文原文，不会让整轮生成失败。

注意：这个接口在国内**直连不通**，生成机器需要能出外网（走代理即可）。
只想抓取不翻译的话，设环境变量 `BUZZ_NO_TRANS=1`。

## 为什么是这些源，而不是别的

所有源都在目标服务器（腾讯云上海轻量，大陆线路）上逐一实测过可达性。
下面这些知名源**在中国大陆服务器上连接失败或被反爬拦截**，所以没有收录：

| 信源 | 实测结果 | 原因 |
|---|---|---|
| Fark (`fark.com/fark.rss`) | HTTP 403 | Cloudflare 反爬 |
| Reddit `r/nottheonion` | 连接失败 | 被墙 |
| BBC / 卫报 / 纽约时报 / 半岛电视台 | 连接失败 | 被墙 |
| Hacker News 直连 (`news.ycombinator.com/rss`) | 连接失败 | 被墙，改用 [hnrss.org](https://hnrss.org) 中转后可达 |
| UPI Odd News / digg / Bored Panda | 连接失败或 403 | 被墙 / 反爬 |

想自己加源：改 `buzz.py` 顶部的 `SOURCES` 列表，加一行 `dict(...)` 即可，
字段含义见文件内注释。单个源挂了不影响其它源。

## 使用

```bash
# 本地生成一份预览（输出到 ./dist，含翻译，需要能出外网）
python3 buzz.py

# 只抓取、不翻译
BUZZ_NO_TRANS=1 python3 buzz.py

# 指定输出目录
BUZZ_OUT=/tmp/xx python3 buzz.py
```

## 部署

**方式一（当前用法）：静态托管。** 本地跑 `python3 buzz.py` 生成 `dist/`，把 `dist/` 交给任意静态托管
（Caddy / nginx / 各类 Pages 平台）即可。

**方式二：自带定时刷新（国内轻量服务器）。**

```bash
git clone https://github.com/MablishAdo/global-buzz.git
cd global-buzz
sudo bash deploy/install.sh
```

安装脚本会：把仓库放到 `/opt/global-buzz` → 生成一次页面到 `/srv/site/buzz/` →
装上 `global-buzz.timer`（每 30 分钟刷一次）→ 自检。
注意：这种方式下翻译会因为没有外网出口而自动降级为英文原文。

已部署的服务器上更新代码：

```bash
cd /opt/global-buzz && sudo git pull
sudo systemctl restart global-buzz.service   # 立刻重新生成一次
```

## 输出

| 文件 | 说明 |
|---|---|
| `index.html` | 自包含的成品页，CSS/JS/数据全部内联，直接交给 Caddy 或 nginx 托管 |
| `data.json` | 聚合结果原始数据（含中文翻译），可供其它程序调用 |
| `meta.json` | 本次运行的统计：耗时、每个源抓到几条、失败原因 |
| `translations.json` | 标题翻译缓存（gitignore），删掉它就会全部重翻 |

## 依赖

只有 Python 3 标准库（`urllib` / `xml.etree` / `concurrent.futures` / `string.Template`）。
Ubuntu 24.04 自带的 Python 3.12 与 macOS 自带的 Python 3.9+ 都能直接跑，无需 `pip install` 任何东西。

## License

MIT

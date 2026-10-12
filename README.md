# 全球热点快报 / Global Buzz

一个零第三方依赖的 RSS 聚合站：抓取 → 去重 → 按分类归并 → 生成一个自包含的静态页面。
服务器上跑一个 Python 标准库脚本加一个 systemd 定时器就够了，没有数据库、没有 Node、没有 Docker。

线上地址：<http://115.159.76.17:8080/buzz/>

## 它长什么样

深色单页，四个分类标签 + 一个搜索框：

| 分类 | 内容 |
|---|---|
| 奇葩搞笑 | The Onion（讽刺假新闻）、Boing Boing（怪奇）、Atlas Obscura（冷门奇景）、Snopes（谣言核查） |
| 全球要闻 | Fox News、NPR、Sky News World、France 24、ScienceAlert |
| 科技极客 | Techmeme、Ars Technica、The Verge、WIRED、Hacker News、Lobsters、Product Hunt、Lifehacker |
| 中文圈 | 少数派、IT之家 |

## 为什么是这些源，而不是别的

所有源都在目标服务器（腾讯云上海轻量，大陆线路）上逐一实测过可达性。
下面这些知名源**在中国大陆服务器上连接失败或被反爬拦截**，所以没有收录，
收录它们只会让每轮生成白等十几秒超时：

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
# 本地生成一份预览（输出到 ./dist）
python3 buzz.py

# 指定输出目录
BUZZ_OUT=/tmp/xx python3 buzz.py
```

## 部署到服务器

```bash
git clone https://github.com/MablishAdo/global-buzz.git
cd global-buzz
sudo bash deploy/install.sh
```

安装脚本会：把仓库放到 `/opt/global-buzz` → 生成一次页面到 `/srv/site/buzz/` →
装上 `global-buzz.timer`（每 30 分钟刷一次）→ 自检。

已部署的服务器上更新代码：

```bash
cd /opt/global-buzz && sudo git pull
sudo systemctl restart global-buzz.service   # 立刻重新生成一次
```

## 输出

| 文件 | 说明 |
|---|---|
| `index.html` | 自包含的成品页，CSS/JS/数据全部内联，直接交给 Caddy 或 nginx 托管 |
| `data.json` | 聚合结果原始数据，可供其它程序调用 |
| `meta.json` | 本次运行的统计：耗时、每个源抓到几条、失败原因 |

## 依赖

只有 Python 3 标准库（`urllib` / `xml.etree` / `concurrent.futures` / `string.Template`）。
Ubuntu 24.04 自带的 Python 3.12 直接能跑，无需 `pip install` 任何东西。

## License

MIT

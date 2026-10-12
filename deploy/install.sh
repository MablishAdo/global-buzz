#!/usr/bin/env bash
#
# 在服务器上安装全球热点快报（在服务器上执行）：
#   sudo bash deploy/install.sh
#
# 它做四件事：
#   1. 把仓库放到 /opt/global-buzz
#   2. 跑一次 buzz.py 生成 /srv/site/buzz/
#   3. 装上 systemd 定时器（每 30 分钟刷新）
#   4. 自检：文件生成了吗、定时器激活了吗
#
set -euo pipefail

REPO_DIR="/opt/global-buzz"
OUT_DIR="/srv/site/buzz"

if [ "$(id -u)" = "0" ]; then SUDO=""; else SUDO="sudo"; fi

echo "==> 1/4 放置程序到 $REPO_DIR"
$SUDO mkdir -p "$(dirname "$REPO_DIR")"
if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR" && $SUDO git pull --ff-only
else
  $SUDO git clone https://github.com/MablishAdo/global-buzz.git "$REPO_DIR"
fi

echo "==> 2/4 生成一次静态页面到 $OUT_DIR"
$SUDO mkdir -p "$OUT_DIR"
$SUDO env BUZZ_OUT="$OUT_DIR" /usr/bin/python3 "$REPO_DIR/buzz.py"

echo "==> 3/4 安装 systemd 定时器"
$SUDO cp "$REPO_DIR/deploy/global-buzz.service" /etc/systemd/system/
$SUDO cp "$REPO_DIR/deploy/global-buzz.timer"   /etc/systemd/system/
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now global-buzz.timer

echo "==> 4/4 自检"
ls -la "$OUT_DIR"
$SUDO systemctl list-timers global-buzz.timer --no-pager

echo
echo "完成。访问 http://<服务器IP>:8080/buzz/ 即可。"

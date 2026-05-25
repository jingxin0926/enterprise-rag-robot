#!/bin/bash
# ================================================================
# 服务器初始化脚本（新租的服务器执行一次）
# 适用于：Ubuntu 22.04 / CentOS 8+
#
# 使用方法：
#   curl -sSL https://raw.githubusercontent.com/jingxin0926/smart-qa-system/main/deploy/server-setup.sh | bash
# 或：
#   chmod +x deploy/server-setup.sh && ./deploy/server-setup.sh
# ================================================================

set -e

echo "🔧 服务器初始化开始..."

# 更新系统
echo "📦 更新系统包..."
apt-get update -y && apt-get upgrade -y 2>/dev/null || yum update -y

# 安装基础工具
echo "🔨 安装基础工具..."
apt-get install -y git curl wget vim htop unzip 2>/dev/null || yum install -y git curl wget vim htop unzip

# 安装 Docker
if ! command -v docker &> /dev/null; then
    echo "🐳 安装 Docker..."
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
    # 允许当前用户免 sudo 运行 docker
    usermod -aG docker $USER
    echo "✅ Docker 安装完成: $(docker --version)"
fi

# 安装 Docker Compose（Docker 20.10+ 自带）
echo "Docker Compose: $(docker compose version)"

# 配置防火墙（开放必要端口）
echo "🔥 配置防火墙..."
if command -v ufw &> /dev/null; then
    ufw allow 22/tcp    # SSH
    ufw allow 80/tcp    # HTTP
    ufw allow 443/tcp   # HTTPS
    ufw allow 8000/tcp  # 应用（调试时直接暴露，生产用 Nginx 代理）
    ufw --force enable
elif command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-port=22/tcp
    firewall-cmd --permanent --add-port=80/tcp
    firewall-cmd --permanent --add-port=443/tcp
    firewall-cmd --permanent --add-port=8000/tcp
    firewall-cmd --reload
fi

# 创建工作目录
echo "📁 创建工作目录..."
mkdir -p /opt/smart-qa-system
cd /opt/smart-qa-system

echo ""
echo "✅ 服务器初始化完成！"
echo ""
echo "📋 下一步："
echo "   1. git clone https://github.com/jingxin0926/smart-qa-system.git /opt/smart-qa-system"
echo "   2. cd /opt/smart-qa-system"
echo "   3. cp .env.example .env && vim .env  # 填入 DEEPSEEK_API_KEY"
echo "   4. ./deploy/deploy.sh               # 一键部署"

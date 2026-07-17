#!/bin/bash
# ================================================================
# 服务器初始化脚本（新租的服务器执行一次）
# 适用于：Ubuntu 22.04 / CentOS 8+
#
# 使用方法：
#   SSH_ALLOWED_CIDR=<你的公网IP>/32 ./deploy/server-setup.sh
#
# 说明：生产部署只需要开放 22、80、443。22 应在云安全组和 UFW 中限制为
# 可信公网 IP，8000、6379、6333 均不应对公网开放。
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
    ufw default deny incoming
    ufw default allow outgoing
    if [ -n "${SSH_ALLOWED_CIDR:-}" ]; then
        ufw allow from "$SSH_ALLOWED_CIDR" to any port 22 proto tcp
    else
        echo "⚠️  未设置 SSH_ALLOWED_CIDR，暂时仅对 SSH 启用限速。"
        echo "   建议使用 SSH_ALLOWED_CIDR=<你的公网IP>/32 重新执行本脚本。"
        ufw limit 22/tcp
    fi
    ufw allow 80/tcp    # HTTP
    ufw allow 443/tcp   # HTTPS
    ufw --force enable
elif command -v firewall-cmd &> /dev/null; then
    if [ -n "${SSH_ALLOWED_CIDR:-}" ]; then
        firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=${SSH_ALLOWED_CIDR} port port=22 protocol=tcp accept"
    else
        firewall-cmd --permanent --add-service=ssh
    fi
    firewall-cmd --permanent --add-port=80/tcp
    firewall-cmd --permanent --add-port=443/tcp
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
echo "   1. git clone --branch dev https://github.com/jingxin0926/smart-qa-system.git /opt/smart-qa-system"
echo "   2. cd /opt/smart-qa-system"
echo "   3. cp .env.example .env && vim .env  # 填入 DEEPSEEK_API_KEY"
echo "   4. ./deploy/deploy.sh               # 一键部署"

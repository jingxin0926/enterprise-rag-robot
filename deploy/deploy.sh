#!/bin/bash
# ================================================================
# 一键部署脚本（在服务器上执行）
#
# 使用方法：
#   1. 把项目代码传到服务器（git clone 或 scp）
#   2. cd 到项目根目录
#   3. chmod +x deploy/deploy.sh
#   4. ./deploy/deploy.sh
# ================================================================

set -e

echo "🚀 开始部署 Smart QA System..."

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装，开始安装..."
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
    echo "✅ Docker 安装完成"
fi

# 检查 Docker Compose
if ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose 未安装"
    exit 1
fi

# 检查 .env 文件
if [ ! -f .env ]; then
    echo "⚠️  .env 文件不存在，从模板创建..."
    cp .env.example .env
    echo "📝 请编辑 .env 文件填入 DEEPSEEK_API_KEY"
    echo "   vim .env"
    exit 1
fi

# 加载环境变量
export $(grep -v '^#' .env | xargs)

# 构建并启动
echo "📦 构建镜像..."
docker compose -f deploy/docker-compose.yml build

echo "🔄 启动服务..."
docker compose -f deploy/docker-compose.yml up -d

echo ""
echo "✅ 部署完成！"
echo ""
echo "📍 服务地址："
echo "   API:     http://$(hostname -I | awk '{print $1}'):8000"
echo "   文档:    http://$(hostname -I | awk '{print $1}'):8000/docs"
echo "   健康检查: http://$(hostname -I | awk '{print $1}'):8000/api/v1/health"
echo ""
echo "📋 常用命令："
echo "   查看日志:   docker compose -f deploy/docker-compose.yml logs -f app"
echo "   重启服务:   docker compose -f deploy/docker-compose.yml restart app"
echo "   停止服务:   docker compose -f deploy/docker-compose.yml down"
echo "   查看状态:   docker compose -f deploy/docker-compose.yml ps"

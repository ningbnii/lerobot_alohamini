#!/usr/bin/env bash
# ==============================================================================
# AlohaMini Raspberry Pi 5 Auto-Start Service Installer & Diagnostic Suite
# Installs, enables, and verifies the background systemd service for zero-touch boot.
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_SRC="${REPO_ROOT}/systemd/alohamini.service"
SERVICE_DST="/etc/systemd/system/alohamini.service"
PI_PASS="${1:-123456}"

echo "=========================================================="
echo "🚀 开始在树莓派本地注册并启动 AlohaMini 常驻自启后台服务..."
echo "=========================================================="

if [ ! -f "${SERVICE_SRC}" ]; then
    echo "❌ 错误: 服务单元文件 ${SERVICE_SRC} 不存在！"
    exit 1
fi

# Authenticate sudo with provided password if needed
if [ -n "${PI_PASS}" ]; then
    echo "${PI_PASS}" | sudo -S -v 2>/dev/null || true
fi

echo "1. 终止可能残留的前台 host 进程..."
pkill -f "alohamini_host" 2>/dev/null || true

echo "2. 复制 systemd 单元文件到 /etc/systemd/system/..."
sudo cp "${SERVICE_SRC}" "${SERVICE_DST}"
sudo chmod 644 "${SERVICE_DST}"

echo "3. 重载 systemd 守护进程..."
sudo systemctl daemon-reload

echo "4. 启用开机自启 (enable)..."
sudo systemctl enable alohamini.service

echo "5. 重启服务 (restart)..."
sudo systemctl restart alohamini.service || true

echo "6. 等待硬件初始化与端口绑定 (最多轮询 8 秒)..."
PORT_BOUND=0
for i in {1..8}; do
    if ss -tulpn 2>/dev/null | grep -E "5555" >/dev/null 2>&1 || netstat -tuln 2>/dev/null | grep -E "5555" >/dev/null 2>&1; then
        echo "   ✅ 端口 5555 已就绪！(耗时 ${i} 秒)"
        PORT_BOUND=1
        break
    fi
    sleep 1
done

if [ "${PORT_BOUND}" -eq 0 ]; then
    echo "   ℹ️ 提示: 硬件设备（串口/摄像头）仍在枚举中，继续打印当前状态..."
fi

echo ""
echo "==================== [自检 1: 服务运行状态] ===================="
sudo systemctl status alohamini.service --no-pager || true

echo ""
echo "==================== [自检 2: 网络端口监听状态] ===================="
ss -tulpn 2>/dev/null | grep -E "5555|5556|5557" || netstat -tuln 2>/dev/null | grep -E "5555|5556|5557" || echo "⚠️ 暂未检测到 5555/5556/5557 端口监听（可能硬件连接异常，请查看下方日志快照）"

echo ""
echo "==================== [自检 3: 运行诊断日志快照] ===================="
sudo journalctl -u alohamini.service -n 35 --no-pager || true

echo "=========================================================="
echo "✅ 树莓派自检与注册脚本执行完成！"
echo "=========================================================="

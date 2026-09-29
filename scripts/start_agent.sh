#!/usr/bin/env bash
# ==============================================================================
# AlohaMini: Start Local Industrial PC Edge Agent
# Bridges Web Teleoperation / WebRTC P2P to Raspberry Pi 5 Host
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Default configurations (can be overridden by environment variables or CLI flags)
ROBOT_HOST_IP="${ROBOT_HOST_IP:-192.168.8.109}"
ROBOT_MODEL="${ROBOT_MODEL:-alohamini2}"
ROBOT_CMD_PORT=5555
GO2RTC_PORT=1984

echo "=========================================================="
echo "🚀 正在启动 AlohaMini 本地工控机 Edge Agent..."
echo "目标实体小车 (树莓派 5): ${ROBOT_HOST_IP}"
echo "小车硬件构型: ${ROBOT_MODEL}"
echo "工作目录: ${REPO_ROOT}"
echo "=========================================================="

# 1. Locate Python runtime environment (.venv preferred, fallback to system/conda)
PYTHON_BIN=""
if [ -f "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
    echo "✅ 使用本地虚拟环境: .venv"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
    echo "ℹ️ 使用系统 Python: ${PYTHON_BIN}"
else
    echo "❌ 错误: 未找到可用的 Python 解释器！请先安装环境并创建 .venv。"
    exit 1
fi

# Ensure pyzmq dependency is satisfied for ZMQ communication
if ! "${PYTHON_BIN}" -c "import zmq" >/dev/null 2>&1; then
    echo "📦 检测到缺少 pyzmq 通信依赖，正在自动安装 (pip install pyzmq)..."
    "${PYTHON_BIN}" -m pip install pyzmq || true
fi

# 2. Check network connectivity to Raspberry Pi
echo "1. 检查与树莓派的网络连通性 (${ROBOT_HOST_IP})..."
if ping -c 1 -W 2 "${ROBOT_HOST_IP}" >/dev/null 2>&1; then
    echo "   ✅ 树莓派网络可达 (PING OK)"
else
    echo "   ⚠️ 警告: 无法 ping 通 ${ROBOT_HOST_IP}，请确认工控机与小车处于同一局域网 Wi-Fi。"
fi

# 3. Check Robot Host ZMQ Command Port (:5555)
echo "2. 探测小车服务端口 (${ROBOT_HOST_IP}:${ROBOT_CMD_PORT})..."
if command -v nc >/dev/null 2>&1 && nc -z -w 2 "${ROBOT_HOST_IP}" "${ROBOT_CMD_PORT}" >/dev/null 2>&1; then
    echo "   ✅ 小车常驻后台服务已就绪 (端口 ${ROBOT_CMD_PORT} 开放)"
else
    echo "   ℹ️ 提示: 端口 ${ROBOT_CMD_PORT} 暂未响应或 nc 工具不可用，Agent 启动后将持续自动重试连接。"
fi

# 4. Check Optional go2rtc video gateway
echo "3. 检查本地视频流网关 (go2rtc :${GO2RTC_PORT})..."
if curl -s "http://127.0.0.1:${GO2RTC_PORT}/api/version" >/dev/null 2>&1; then
    echo "   ✅ go2rtc 已在本地运行，视频流网关就绪"
else
    echo "   ℹ️ go2rtc 暂未运行（如需 Web 端低延迟视频预览，可另行启动 go2rtc 服务）"
fi

# 5. Start Agent process
echo ""
echo "=========================================================="
echo "🎯 正在启动 Agent 守护进程（按 Ctrl+C 可随时安全退出与急停）..."
echo "=========================================================="

cd "${REPO_ROOT}"
export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/src:${PYTHONPATH:-}"

exec "${PYTHON_BIN}" -m agent.main \
    --robot-host-ip "${ROBOT_HOST_IP}" \
    --robot-model "${ROBOT_MODEL}" \
    "$@"

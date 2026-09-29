#!/usr/bin/env bash
# ==============================================================================
# AlohaMini: Deploy Persistent Auto-Start Service to Raspberry Pi 5 (192.168.8.109)
# Optimized: SSH ControlMaster connection multiplexing (Only prompts password ONCE!)
# ==============================================================================

set -euo pipefail

PI_USER="pi5"
PI_HOST="192.168.8.109"
PI_PASS="123456"
REMOTE_DIR="/home/pi5/lerobot_alohamini"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=========================================================="
echo "🚀 正在部署 AlohaMini 常驻自启后台服务到树莓派 5..."
echo "目标机器: ${PI_USER}@${PI_HOST}"
echo "=========================================================="

# 1. Test ping connectivity
echo "1. 测试网络连通性..."
if ! ping -c 1 -W 2 "${PI_HOST}" >/dev/null 2>&1; then
    echo "⚠️ 警告: 无法 ping 通 ${PI_HOST}，请确保工位电脑与树莓派处于同一局域网 Wi-Fi。"
fi

# 2. Setup SSH Connection Multiplexing (ControlMaster)
# Creates a temporary master socket so the user is only asked for their password once!
SSH_SOCKET="/tmp/alohamini_ssh_${PI_HOST}_$$.sock"

cleanup() {
    if [ -e "${SSH_SOCKET}" ]; then
        ssh -O exit -S "${SSH_SOCKET}" "${PI_USER}@${PI_HOST}" 2>/dev/null || true
        rm -f "${SSH_SOCKET}"
    fi
}
trap cleanup EXIT INT TERM

echo "2. 正在建立持久安全通信通道（🔑 全程仅需输入此一次密码: ${PI_PASS}）..."

# Check if sshpass is available
SSHPASS_PREFIX=""
if command -v sshpass >/dev/null 2>&1; then
    SSHPASS_PREFIX="sshpass -p ${PI_PASS}"
fi

${SSHPASS_PREFIX} ssh -M -S "${SSH_SOCKET}" -fnNT \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR \
    -o ControlPersist=10m \
    "${PI_USER}@${PI_HOST}"

run_ssh() {
    local cmd="$1"
    ssh -S "${SSH_SOCKET}" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR \
        "${PI_USER}@${PI_HOST}" "${cmd}"
}

run_scp() {
    local src="$1"
    local dst="$2"
    scp -o "ControlPath=${SSH_SOCKET}" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR \
        "${src}" "${PI_USER}@${PI_HOST}:${dst}"
}

# 3. Ensure remote directory structure exists
echo "3. 检查并准备树莓派目录..."
run_ssh "mkdir -p ${REMOTE_DIR}/src/lerobot/robots/alohamini ${REMOTE_DIR}/systemd ${REMOTE_DIR}/scripts"

# 4. Synchronize core files over multiplexed socket
echo "4. 同步代码与服务配置（复用通道，秒级免密）..."
run_scp "${REPO_ROOT}/src/lerobot/robots/alohamini/alohamini_host.py" "${REMOTE_DIR}/src/lerobot/robots/alohamini/alohamini_host.py"
run_scp "${REPO_ROOT}/systemd/alohamini.service" "${REMOTE_DIR}/systemd/alohamini.service"
run_scp "${REPO_ROOT}/scripts/install_autostart_service.sh" "${REMOTE_DIR}/scripts/install_autostart_service.sh"

# 5. Remote batch installation, systemd registration, and verification
echo "5. 注册 systemd 服务并执行自检..."
run_ssh "chmod +x ${REMOTE_DIR}/scripts/install_autostart_service.sh && bash ${REMOTE_DIR}/scripts/install_autostart_service.sh ${PI_PASS}"

echo "=========================================================="
echo "🎉 部署与自检全部完成！全程仅验证了一次密码。"
echo "树莓派当前已进入开机免标定常驻后台服务状态。"
echo "=========================================================="

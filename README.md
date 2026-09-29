# lerobot_alohamini

Shared software layer for the AlohaMini product line, built on HuggingFace LeRobot. Supports both the full AlohaMini robot (dual-arm + mobile base + lift) and the AM-ARM200 arm.

> Haven't assembled your hardware yet? Start here: [AlohaMini](https://github.com/liyiteng/alohamini) · [AM-ARM200](https://github.com/liyiteng/AM-ARM)

## Updates
- **[2025-07-11]** merge upstream LeRobot v0.6

## Documentation

Start with setup, then follow the workflow for your hardware. Use the reference pages when you need exact flags, commands, or low-level debug tools.

### Recommended Path

1. [Install](docs/alohamini/install.md) — prepare the environment, serial port permissions, and Hugging Face login.
2. Pick your robot workflow:
   - [AM-ARM200](docs/alohamini/am-arm200.md) — single-arm workflow on one PC: calibration, teleoperation, dataset recording, training, and evaluation.
   - [AlohaMini 1 / 2 / 2 Pro](docs/alohamini/alohamini.md) — dual-arm workflow with Pi + PC: calibration, teleoperation, dataset recording, training, and evaluation.

### AlohaLab Edge Agent (工位边缘守护进程)

工位电脑作为边缘代理（Agent），下连树莓派小车硬件（ZMQ :5555 / :5557），上接 Web 实训平台（WebRTC P2P 直连）：

```bash
# 方式 1：一键脚本启动（内置树莓派网络与端口探活）
bash scripts/start_agent.sh

# 方式 2：原生 Python 虚拟环境启动
source .venv/bin/activate
python -m agent.main --robot-host-ip 192.168.8.109 --robot-model alohamini2
```

---

### References

| Reference | Use it for |
|-----------|------------|
| [Hardware Profiles](docs/alohamini/profiles.md) | `--arm_profile` and `--robot_model` flag meanings |
| [Command Cheat Sheet](docs/alohamini/commands.md) | Copy-paste commands for setup, host, teleoperation, recording, training, evaluation, and common checks |
| [Debug Tools](examples/debug/README.md) | Low-level motor, wheel, lift axis, servo ID, phase, midpoint, torque, and scripted-action debug functions |

---

## Team & Contact

AlohaMini is created by **Li Yiteng** and **Wu Zhiyong**.

- Email: liyiteng+github@gmail.com
- WeChat: liyiteng

## Acknowledgements

- [LeRobot](https://github.com/huggingface/lerobot) — the software stack this repository targets
- [ALOHA](https://tonyzhaozh.github.io/aloha/) — the bimanual teleoperation paradigm
- [SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100) — pioneered the low-cost open arm design pattern

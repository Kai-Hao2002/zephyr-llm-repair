# OpenHands baseline 的沙箱映像

`core/openhands_pipeline.py` 預設使用 `zephyr-oh-agent:1.55.1-z1`。建置步驟（2026-10-18）：

1. 中介映像：`zephyr-sandbox` 預設以 `user`（uid 1000）執行，agent-server 的 Dockerfile 需要 root 才能安裝套件。
   ```bash
   docker build -t zephyr-sandbox-root:latest -f tools/openhands_image/Dockerfile.root-base tools/openhands_image
   ```
2. OpenHands agent-server（SDK v1.55.1，`binary-minimal` target）疊在中介映像上，沿用既有的 `user` 帳號：
   ```bash
   git clone --depth 1 --branch v1.55.1 https://github.com/OpenHands/software-agent-sdk.git
   cd software-agent-sdk
   docker buildx build --build-arg BASE_IMAGE=zephyr-sandbox-root:latest \
       --build-arg USERNAME=user --build-arg UID=1000 --build-arg GID=1000 \
       --target binary-minimal -f openhands-agent-server/openhands/agent_server/docker/Dockerfile \
       -t zephyr-oh-agent:1.55.1 --load .
   ```
3. `-z1` 層：
   - 清掉 agent-server 設定的 `LD_LIBRARY_PATH`。它會讓 Zephyr SDK 的 `dtc` 發生 `Floating-point exception` 而 crash。
   - 加入 `zephyr-run`：用和評測端相同的 build 加 run 指令，看到 `PROJECT EXECUTION ...` 就停掉 QEMU。不加的話，QEMU 測試會一直跑到 timeout，實測要 300 秒，加了只要約 60 秒。
   ```bash
   docker build -t zephyr-oh-agent:1.55.1-z1 -f tools/openhands_image/Dockerfile.z1 tools/openhands_image
   ```

agent 端需要 Python 3.12 以上的 venv：
```bash
python3.12 -m venv venv_oh
venv_oh/bin/pip install openhands-sdk==1.55.1 openhands-tools==1.55.1 openhands-workspace==1.55.1 openhands-agent-server==1.55.1
```

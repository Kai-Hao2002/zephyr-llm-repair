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

4. `-z2` 層（預設，2026-10-18）：網路隔離。
   - OpenHands V1 的 LLM 呼叫是由**容器內**的 agent-server 發出的，所以不能完全斷網。
   - 容器的 `netlock-entrypoint` 用 iptables 丟掉所有對外封包，只放行三種：loopback；主機主動連進來的連線的回應（SDK 透過發佈的 port 和 agent-server 溝通）；以及連到主機上白名單代理（`tools/egress_proxy.py`）的連線。
   - 代理的 IP 會在擋網路之前先查好，寫進 `/etc/hosts`，因為擋掉之後容器裡就沒有 DNS 了。
   - 設定完成後，用 `capsh` 拿掉 `CAP_NET_ADMIN` 和 `CAP_NET_RAW`，再啟動 agent-server。agent 雖然是 root，也改不了規則。
   - 代理只接受 HTTP CONNECT 到目前供應商的 LLM API，例如 Gemini 是 `generativelanguage.googleapis.com:443`，其他目的地一律回 403。每筆結果的 `openhands.egress_log` 會記錄被允許和被拒絕的連線。
   - 設定規則需要在啟動容器時有 `NET_ADMIN`。SDK 沒有這個選項，所以 `tools/openhands_runner.py` 只在啟動 agent-server 容器時加上 `--cap-add NET_ADMIN`。
   - 為什麼不用 Docker 網路設定：在 Docker Desktop for Mac 上，關閉 NAT（`enable_ip_masquerade=false`）的網路仍然連得到外網，實測過。
   ```bash
   docker build -t zephyr-oh-agent:1.55.1-z2 -f tools/openhands_image/Dockerfile.z2 tools/openhands_image
   ```
   驗證（2026-10-18，透過 SDK 的 `execute_command`）：
   - 透過代理連 Gemini API：連得上；
   - 透過代理連 GitHub：403；
   - 直接連 1.1.1.1 或 Gemini 的 IP：被擋；
   - agent 執行 `iptables -F`：Permission denied；
   - fat 案例完整試跑：51 次 LLM 呼叫、8 次 build 都正常。代理只放行了 Gemini API，被拒絕的只有 agent-server 啟動時抓的 `raw.githubusercontent.com`。

FROM ghcr.io/zephyrproject-rtos/ci:v0.26.11

USER root
RUN apt-get -y update && \
    apt-get -y install python3-pip expect && \
    apt-get clean

# 切換回非 root 使用者 (Zephyr CI image 預設使用者為 user)
USER user

# 建立 Zephyr 工作區並下載 Zephyr 原始碼
# 使用 --narrow 與 --depth=1 進行淺層複製，大幅節省下載時間與空間
WORKDIR /zephyrproject
RUN west init -m https://github.com/zephyrproject-rtos/zephyr --mr v3.5.0 . && \
    west update --narrow -o=--depth=1

# 設定 ZEPHYR_BASE 環境變數，讓 west build 能在任何資料夾中呼叫 Zephyr 擴充指令
ENV ZEPHYR_BASE=/zephyrproject/zephyr

# 設定回預期的工作目錄
WORKDIR /workspace

CMD ["/bin/bash"]
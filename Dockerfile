FROM ghcr.io/zephyrproject-rtos/ci:v0.29.2

# v0.29.2 (Ubuntu 24.04) 內建 Python 3.12.3 與 Zephyr SDK 1.0.1，剛好符合
# Zephyr main 分支目前的最低需求 (先前用的 v0.26.11 是 Ubuntu 22.04 + Python
# 3.10 + SDK 0.16.5，跟不上 main，導致每次建置都在 CMake 設定階段就失敗)。
# west、jsonschema、pyelftools 等工具鏈依賴也都已經預先裝好，不需要再額外處理。
#
# v0.29.2 (Ubuntu 24.04) ships Python 3.12.3 and Zephyr SDK 1.0.1 out of the
# box, matching Zephyr main's current minimum requirements (the previously
# used v0.26.11 was Ubuntu 22.04 + Python 3.10 + SDK 0.16.5, too old for main,
# so every build failed at the CMake configure stage). west, jsonschema,
# pyelftools, etc. are already preinstalled too.
USER root
RUN apt-get -y update && \
    apt-get -y install expect && \
    apt-get clean

# 切換回非 root 使用者 (Zephyr CI image 預設使用者為 user)
USER user

# 建立 Zephyr 工作區並下載 Zephyr 原始碼
# 使用 --narrow 與 --depth=1 進行淺層複製，大幅節省下載時間與空間
# 從 main 分支初始化，因為 Zephyr-Eval 的候選案例主要挖掘自近期合併到 main
# 的 bug PR，這樣能讓 build-time 的初始快取跟實際驗證時要 checkout 的
# commit 更接近，減少每次驗證時 west update 要抓的新模組。
# Initialize from main since Zephyr-Eval candidates are mostly mined from
# recent main-branch bug PRs — this keeps the build-time cache closer to
# what each verification run actually needs to check out.
WORKDIR /zephyrproject
RUN west init -m https://github.com/zephyrproject-rtos/zephyr --mr main . && \
    west update --narrow -o=--depth=1

# cppcheck 供 Phase 2 的 StaticCheck 節點使用 (在套用 patch 後、真正
# west build 之前先做一次快速靜態分析，抓明顯的語法/邏輯錯誤，失敗就直接
# 打回 Patch 重生成，不用等一次完整建置)。刻意放在 west init/west update
# 這個很慢的層之後，重建映像檔時才能沿用已經快取的那幾層，不用重跑一次
# 淺層複製。
# cppcheck for Phase 2's StaticCheck node (a quick static-analysis pass
# right after a patch is applied, before a full west build — catches
# obvious syntax/logic errors and bounces straight back to Patch instead of
# waiting out a whole build). Deliberately placed after the slow west
# init/west update layer so rebuilding the image reuses that cached layer
# instead of redoing the shallow clone.
USER root
RUN apt-get -y update && \
    apt-get -y install cppcheck && \
    apt-get clean
USER user

# 設定 ZEPHYR_BASE 環境變數，讓 west build 能在任何資料夾中呼叫 Zephyr 擴充指令
ENV ZEPHYR_BASE=/zephyrproject/zephyr

# 設定回預期的工作目錄
WORKDIR /workspace

CMD ["/bin/bash"]

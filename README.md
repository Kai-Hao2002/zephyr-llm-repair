# Zephyr-LLM-Repair: Autonomous RTOS Bug Repair Framework 
# 基於多代理人閉環驗證之 Zephyr RTOS 錯誤自主修復框架

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)
![Framework](https://img.shields.io/badge/framework-LangGraph-orange)
![RTOS](https://img.shields.io/badge/RTOS-Zephyr-brightgreen)
![Simulation](https://img.shields.io/badge/simulation-QEMU-yellow)

## 📖 Introduction / 專案簡介

**[EN]** This project is an LLM-driven multi-agent closed-loop debugging framework designed specifically for the open-source RTOS, Zephyr. Utilizing LangGraph for a directed graph state machine, this system integrates "Domain-Specific Graph RAG" for Kconfig and DTS files. It performs deterministic builds and QEMU runtime verification within fully isolated Docker containers to achieve highly autonomous and precise bug repair.

**[中]** 本專案是一個基於大型語言模型 (LLM) 的多代理人閉環除錯框架，專為開源即時作業系統 (RTOS) Zephyr 設計。本系統利用 LangGraph 構建有向圖狀態機，結合針對 Kconfig 與 DTS 檔案的「領域特定圖譜檢索 (Graph RAG)」，並在完全隔離的 Docker 容器中進行決定性的建置與 QEMU 執行期驗證，實現高度自主且精確的錯誤修復。

---

## 🏗️ System Architecture / 系統架構

To ensure high scalability and maintainability, the codebase is deeply decoupled based on **"State Management"**, **"Agent Nodes"**, **"Graph Retrieval"**, and **"Toolchains"**. 

為了讓專案具備高擴充性與可維護性，程式碼架構依照「狀態管理」、「代理人節點」、「圖譜檢索」與「工具鏈」進行深度解耦。

---

## 📂 Repository Structure / 專案結構

```text
zephyr-llm-repair/
│
├── main.py                     # 系統進入點：初始化參數、啟動 LangGraph 狀態機
├── requirements.txt            # Python 依賴清單 (langgraph, langchain-google-genai, 等)
├── Dockerfile                  # 決定性建置環境：打包 Zephyr SDK 與相關依賴
├── .env                        # 環境變數設定檔 (如 API Key)
│
├── core/                       # 核心狀態機與工作流程 (LangGraph)
│   ├── state.py                # 定義全域狀態 (Global State)，包含錯誤日誌、重試次數等
│   └── workflow.py             # LangGraph 節點連接與條件邊緣 (Conditional Edges) 路由邏輯
│
├── agents/                     # 多代理人節點實作 (LangGraph Nodes)
│   ├── supervisor.py           # 中央調度：負責判斷是否超過最大重試次數
│   ├── analyzer.py             # 錯誤分析：解讀 DevOps 傳回的精簡日誌，決定檢索策略
│   ├── knowledge_expert.py     # 知識檢索：與 graph_rag 模組互動，取得圖譜上下文
│   └── patch_expert.py         # 修補生成：生成嚴格的 <<<<SEARCH 與>>>>REPLACE 區塊
│
├── graph_rag/                  # 領域特定圖譜檢索模組
│   ├── build_graph.py          # 負責將專案的設定檔轉換為 NetworkX 記憶體圖譜
│   ├── parsers/                # Kconfig 與 DTS 解析器
│   └── retriever.py            # 圖譜走訪 (Graph Traversal) 與上下文格式化
│
├── tools/                      # 代理人呼叫的外部工具鏈
│   ├── patch_applier.py        # 在本地或容器內執行精確的字串匹配與替換
│   ├── log_filter.py           # 分層日誌過濾：剔除常規輸出，提取 Fatal Error
│   └── qemu_oracle.py          # 測試預言：監控 QEMU stdout，捕捉啟動或崩潰特徵
│
├── env_manager/                # 隔離環境與建置管理
│   ├── docker_manager.py       # 負責啟動/重置 Docker 容器
│   └── west_executor.py        # 封裝執行 west build 與 west build -t run 的指令
│
└── dataset/                    # Zephyr-Eval 評估資料集
    ├── scripts/                # 自動化探勘 GitHub 提交記錄的腳本
    └── cases/                  # 測試案例 (包含 broken commit, error log, golden patch)
```
## 🛠️ Environment Setup
The execution of this system is divided into the "Host" (running LLM agent logic) and the "Container" (running Zephyr compilation and QEMU simulation).

本系統的執行分為「主機端 (Host)」(執行 LLM 代理人邏輯) 與「容器端 (Container)」(執行 Zephyr 編譯與 QEMU 模擬)。
1. Prerequisites (先決條件)
* Python 3.10+
```bash
# Create and activate virtual environment
python3.10 -m venv venv
# Windows: .\venv\Scripts\activate
# macOS/Linux: source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```
* Docker Desktop / Docker Engine (用於啟動 Zephyr 決定性沙盒)
* Google Gemini API Key (Or relevant LLM provider key)
  
2. 主機端安裝 (Host Setup)
```bash
# Clone the repository (複製專案原始碼)
git clone [https://github.com/yourusername/zephyr-llm-repair.git](https://github.com/yourusername/zephyr-llm-repair.git)
cd zephyr-llm-repair

# Create and activate a virtual environment (建立並啟動虛擬環境)
python3.10 -m venv venv
# Windows: .\venv\Scripts\activate
# macOS/Linux: source venv/bin/activate

# Install dependencies (安裝 Python 依賴)
pip install --upgrade pip
pip install -r requirements.txt

# Configure environment variables (設定環境變數)
# Create a .env file and add your API key (建立 .env 檔案並寫入 API Key)
echo 'GEMINI_API_KEY="your-api-key-here"' > .env
```
3. 建置決定性沙盒 (Build Deterministic Sandbox)
The system uses a Docker image to ensure every west build is executed in a clean environment to prevent cache contamination.
系統將使用 Docker 映像檔來確保每次編譯都在乾淨的環境下執行，避免快取污染。
```bash
# 進入專案根目錄，打包包含 Zephyr SDK 的容器映像檔
# Build the Docker image containing the Zephyr SDK
docker build -t zephyr-sandbox -f Dockerfile .
```

## 🚀 使用方式 / Usage
You can start the repair process by passing the path of the broken Zephyr project or a specific dataset case via the CLI.
您可以直接透過指令列 (CLI) 傳入發生錯誤的 Zephyr 專案路徑，來啟動修復流程。
```bash
# Run a single repair task (執行單一端到端修復任務)
python main.py --target ./dataset/cases/bug_001 --max_retries 5

# Run automated benchmark evaluation (啟動自動化基準測試 - 開發中)
python evaluate.py --dataset ./dataset/cases/ --model gemini-2.5-pro
```

### ⚠️ `evaluate.py` 設計要求：禁止把 `.git` 暴露給待測 agent / Design requirement: never expose `.git` to the agent under evaluation

**[中]** `verified_zephyr_bugs.json` 裡的每一筆案例都是用 `tools/mutate_inject.py` 對一個已知乾淨的 `broken_commit`（所有案例目前共用同一個：`bc460feabe7038dc876782557e39be791d6c24e9`）做**純 working tree 層級的文字編輯**產生的——它只用 `shutil.copyfile` 備份、直接改檔案內容，從頭到尾不曾呼叫任何 `git` 指令，也不曾建立新的 commit。這代表：如果 `evaluate.py`（或任何未來的跑分 runner）比照 `tools/fault_injector.py` 自己驗證時的做法（`git checkout {broken_commit}` 之後直接套用 mutation），把那個目錄**原封不動、含 `.git`**地交給待測 agent 當工作目錄，那麼 git 的 index/HEAD 其實從頭到尾都停在乾淨的 `broken_commit`——**任何 agent 只要對整個 repo 執行一次 `git checkout -- .` 或 `git restore .`，不需要讀懂、甚至不需要看任何一行程式碼或錯誤訊息，就能讓全部 98 筆（以及未來新增的）案例同時「修復成功」**，讓整個 benchmark 的分數失去意義。

`evaluate.py` 動工時，硬性要求：
1. 準備好要交給 agent 的 broken workspace 之後、把控制權交還給 agent 之前，**必須先移除 `.git` 目錄**（例如把工作目錄用 `tar`/`rsync --exclude=.git` 複製一份乾淨的、不含版本控制歷史的副本，而不是直接掛載或複製那個做完 `git checkout` 的目錄本身）。
2. 若 agent 執行環境有對外網路存取，同樣要考慮 agent 直接 `git clone`/`git fetch` 真正的 upstream Zephyr repo（`broken_commit` 是公開可查的 SHA）來比對差異的殘餘風險——是否要限制 agent sandbox 的對外網路存取，需要在設計 `evaluate.py` 時一併決定，不要等實際跑分才發現漏洞。
3. 把「拿掉 `.git` 之後，agent 的工作目錄裡確實沒有可以拿來 diff 出原始碼的版本控制殘留」這件事，寫成 `evaluate.py` 自己的一個驗收測試（例如跑分前先斷言 workspace 底下沒有 `.git`／沒有任何指向 `broken_commit` 的物件），不要只靠人工檢查。

**[EN]** Every case in `verified_zephyr_bugs.json` is produced by `tools/mutate_inject.py` editing a known-good `broken_commit` (all cases currently share one: `bc460feabe7038dc876782557e39be791d6c24e9`) **purely at the working-tree level** — it backs up via `shutil.copyfile` and edits file contents directly, never invoking `git` or creating a commit. This means: if `evaluate.py` (or any future runner) mirrors how `tools/fault_injector.py` verifies cases internally (`git checkout {broken_commit}`, then apply the mutation) and hands that directory to the agent under evaluation **as-is, with `.git` intact**, the git index/HEAD remain at the pristine `broken_commit` the whole time — **any agent can trivially "resolve" all 98 (and any future) cases by running `git checkout -- .` or `git restore .` on the whole repo, without reading a single line of code or error output**, making the benchmark's scores meaningless.

When building `evaluate.py`, this is a hard requirement:
1. **Strip `.git` before handing control to the agent** — e.g. copy the broken workspace out via `tar`/`rsync --exclude=.git` into a version-control-free directory, rather than mounting or copying the post-`git checkout` directory itself.
2. If the agent's sandbox has outbound network access, also consider the residual risk of it `git clone`/`git fetch`-ing the real upstream Zephyr repo directly (the `broken_commit` SHA is a public, fetchable commit) to diff against — decide whether to sandbox network access as part of `evaluate.py`'s design, not after a real evaluation run reveals the gap.
3. Turn "the agent's workspace genuinely has no version-control residue to diff the original source from" into one of `evaluate.py`'s own pre-flight assertions (e.g. assert no `.git` directory exists before releasing the workspace to the agent), not something only checked by hand.

### 🕒 Baseline commit 多樣性 / Baseline commit diversity

**[中]** `verified_zephyr_bugs.json` 的絕大多數案例目前仍固定在同一個 pinned commit（`bc460feabe7038dc876782557e39be791d6c24e9`，2026-07-24）上——這本身是另一種多樣性風險：任何在這個日期之後訓練、且訓練資料涵蓋公開 GitHub 歷史的通用 LLM，都可能對這個特定原始碼快照本身有先驗記憶，跟它是否真的理解注入的 bug 機制無關。`dataset/scripts/mine_commits.py` 已經支援用第二個（或更多）pinned commit：`INJECTION_CATALOG` 裡的個別 entry 可以帶 `"baseline_commit": SECOND_BASELINE_COMMIT` 覆蓋預設共用的 commit（見該檔案裡 `SECOND_BASELINE_COMMIT` 常數上方的註解）——`tools/fault_injector.py`/`dataset/scripts/verify_cases.py` 本來就是逐案例讀取 `broken_commit`，從來就沒有「整批必須共用同一個 commit」的架構限制。目前只有 `inject_kconfig_fcb_depends_baseline2` 這一筆落在第二個 commit（`4b02c5d60ae620fb23cbea58516e3ea7388c2f75`，2026-05-14）上，用來證明機制可行；未來要真正緩解「幾乎全部案例集中在單一 commit」這個統計，需要之後的 session 持續把新案例分散注入到多個已驗證乾淨的 pinned commit，而不是預設一律沿用最早那一個。

**[EN]** The large majority of cases in `verified_zephyr_bugs.json` still sit on one pinned commit (`bc460feabe7038dc876782557e39be791d6c24e9`, 2026-07-24) — a distinct diversity risk in its own right: any general-purpose LLM trained after that date on public GitHub history could have prior exposure to that exact source snapshot, independent of whether it understands the injected bug's mechanism. `dataset/scripts/mine_commits.py` now supports a second (or further) pinned commit: an individual `INJECTION_CATALOG` entry can carry `"baseline_commit": SECOND_BASELINE_COMMIT` to override the default shared commit (see the comment above the `SECOND_BASELINE_COMMIT` constant in that file) — `tools/fault_injector.py`/`dataset/scripts/verify_cases.py` already read `broken_commit` per-case; there was never an architectural requirement for a whole batch to share one commit. Only `inject_kconfig_fcb_depends_baseline2` currently sits on the second commit (`4b02c5d60ae620fb23cbea58516e3ea7388c2f75`, 2026-05-14), proving the mechanism works; actually moving the needle on "almost every case concentrated on one commit" needs future sessions to keep spreading new cases across multiple verified-clean pinned commits instead of defaulting back to the earliest one.

## 📄 License (授權)
MIT License. See LICENSE for more information.
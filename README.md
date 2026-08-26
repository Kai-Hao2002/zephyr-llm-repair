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

**[中]** `verified_zephyr_bugs.json` 裡的每一筆案例都是用 `tools/mutate_inject.py` 對一個已知乾淨的 `broken_commit`（所有案例目前共用同一個：`bc460feabe7038dc876782557e39be791d6c24e9`）做**純 working tree 層級的文字編輯**產生的——它只用 `shutil.copyfile` 備份、直接改檔案內容，從頭到尾不曾呼叫任何 `git` 指令，也不曾建立新的 commit。這代表：如果 `evaluate.py`（或任何未來的跑分 runner）比照 `tools/fault_injector.py` 自己驗證時的做法（`git checkout {broken_commit}` 之後直接套用 mutation），把那個目錄**原封不動、含 `.git`**地交給待測 agent 當工作目錄，那麼 git 的 index/HEAD 其實從頭到尾都停在乾淨的 `broken_commit`——**任何 agent 只要對整個 repo 執行一次 `git checkout -- .` 或 `git restore .`，不需要讀懂、甚至不需要看任何一行程式碼或錯誤訊息，就能讓全部案例（目前 150 筆，以及未來新增的）同時「修復成功」**，讓整個 benchmark 的分數失去意義。

`evaluate.py` 動工時，硬性要求：
1. 準備好要交給 agent 的 broken workspace 之後、把控制權交還給 agent 之前，**必須先移除 `.git` 目錄**（例如把工作目錄用 `tar`/`rsync --exclude=.git` 複製一份乾淨的、不含版本控制歷史的副本，而不是直接掛載或複製那個做完 `git checkout` 的目錄本身）。
2. 若 agent 執行環境有對外網路存取，同樣要考慮 agent 直接 `git clone`/`git fetch` 真正的 upstream Zephyr repo（`broken_commit` 是公開可查的 SHA）來比對差異的殘餘風險——是否要限制 agent sandbox 的對外網路存取，需要在設計 `evaluate.py` 時一併決定，不要等實際跑分才發現漏洞。
3. 把「拿掉 `.git` 之後，agent 的工作目錄裡確實沒有可以拿來 diff 出原始碼的版本控制殘留」這件事，寫成 `evaluate.py` 自己的一個驗收測試（例如跑分前先斷言 workspace 底下沒有 `.git`／沒有任何指向 `broken_commit` 的物件），不要只靠人工檢查。

**[EN]** Every case in `verified_zephyr_bugs.json` is produced by `tools/mutate_inject.py` editing a known-good `broken_commit` (all cases currently share one: `bc460feabe7038dc876782557e39be791d6c24e9`) **purely at the working-tree level** — it backs up via `shutil.copyfile` and edits file contents directly, never invoking `git` or creating a commit. This means: if `evaluate.py` (or any future runner) mirrors how `tools/fault_injector.py` verifies cases internally (`git checkout {broken_commit}`, then apply the mutation) and hands that directory to the agent under evaluation **as-is, with `.git` intact**, the git index/HEAD remain at the pristine `broken_commit` the whole time — **any agent can trivially "resolve" all cases (150 currently, and any future ones) by running `git checkout -- .` or `git restore .` on the whole repo, without reading a single line of code or error output**, making the benchmark's scores meaningless.

When building `evaluate.py`, this is a hard requirement:
1. **Strip `.git` before handing control to the agent** — e.g. copy the broken workspace out via `tar`/`rsync --exclude=.git` into a version-control-free directory, rather than mounting or copying the post-`git checkout` directory itself.
2. If the agent's sandbox has outbound network access, also consider the residual risk of it `git clone`/`git fetch`-ing the real upstream Zephyr repo directly (the `broken_commit` SHA is a public, fetchable commit) to diff against — decide whether to sandbox network access as part of `evaluate.py`'s design, not after a real evaluation run reveals the gap.
3. Turn "the agent's workspace genuinely has no version-control residue to diff the original source from" into one of `evaluate.py`'s own pre-flight assertions (e.g. assert no `.git` directory exists before releasing the workspace to the agent), not something only checked by hand.

### ⚠️ `evaluate.py` 設計要求：`injection`/`injections` 欄位絕不能交給待測 agent / Design requirement: never expose the `injection`/`injections` field to the agent under evaluation

**[中]** `verified_zephyr_bugs.json` 每一筆案例的 `injection`（或 compound 案例的 `injections`）欄位，其 `operator` 字串**直接、逐字寫死了這個案例的完整 mutation 手法**，包括被改動的確切原始文字（例如
`"runtime_double_free:test_malloc:k_free(actual_message_data->reference);"`
或
`"dts_swap_phandle_pair:test_reg_1:test_reg_chained"`）——這不是像 `.git` 風險那樣「runner 實作方式不當才會暴露」的間接風險，而是**只要這個欄位以任何形式讓待測 agent 看到（完整 case dict 被直接餵進 prompt、debug log 印出案例內容、跑分過程的中介檔案沒有先過濾這個欄位等），就等於直接把答案寫在考卷上**——agent 完全不需要讀懂錯誤訊息或原始碼，只要抓到 `operator` 字串裡的 `target_file`/舊文字/新文字就能機械式地做出「正確」的反向修改。

`evaluate.py` 動工時，硬性要求：
1. 交給 agent 的任何內容（prompt、workspace、任何形式的中介輸出）**絕對不能包含 `injection`/`injections` 欄位**，也不能包含由它反推得出的資訊（例如把 `operator` 字串印進除錯 log、或把整個 case dict 序列化後存進 agent 看得到的檔案）。agent 唯一該看到的錯誤相關資訊是 `initial_error_log`（已經是壓縮過的建置/執行期錯誤訊息，不含 mutation 手法本身）。
2. `target_test`/`category`/`board`/`target_app` 這些欄位本身是安全的（不洩漏具體改了什麼），可以視需要交給 agent；`broken_commit`/`fixed_commit` 則要搭配上面 `.git` 那條規則一起考慮（`fixed_commit` 若被 agent 看到，等同直接洩漏答案所在的那個上游修復 commit，同樣不能出現在 agent 可見的任何地方）。
3. 把「agent 可見的所有內容都經過白名單過濾、不含 `injection`/`injections`/`fixed_commit`」寫成 `evaluate.py` 自己的一個驗收測試，不要只靠人工檢查——跟上面 `.git` 那條的第 3 點是同一種紀律。

**[EN]** Every case's `injection` (or `injections` for compound cases) field in `verified_zephyr_bugs.json` has an `operator` string that **literally, verbatim encodes the case's complete mutation recipe**, including the exact original text that was changed (e.g.
`"runtime_double_free:test_malloc:k_free(actual_message_data->reference);"`
or
`"dts_swap_phandle_pair:test_reg_1:test_reg_chained"`). Unlike the `.git` risk above (which only leaks if the runner implements things a specific wrong way), this one leaks **the moment the field reaches the agent under evaluation in any form** — the full case dict fed into a prompt, case contents echoed into a debug log, an intermediate scoring file that wasn't filtered first — since the agent then needs zero understanding of the error message or source code to mechanically reverse-apply the `target_file`/old-text/new-text spelled out in `operator`.

When building `evaluate.py`, this is a hard requirement:
1. Anything handed to the agent (prompt, workspace, any intermediate output) **must never include the `injection`/`injections` field**, nor anything derived from it (e.g. printing the `operator` string to a debug log, or serializing the whole case dict into a file the agent can read). The only error-related information the agent should ever see is `initial_error_log` (already a compressed build/runtime error message, containing no mutation-recipe detail).
2. `target_test`/`category`/`board`/`target_app` are safe to expose (they don't reveal what was actually changed) if needed; `broken_commit`/`fixed_commit` need to be considered alongside the `.git` rule above — if the agent ever sees `fixed_commit`, that directly leaks the upstream commit containing the answer, so it must never appear anywhere agent-visible either.
3. Turn "everything agent-visible is allowlist-filtered, with no `injection`/`injections`/`fixed_commit`" into one of `evaluate.py`'s own pre-flight assertions, not something only checked by hand — the same discipline as point 3 of the `.git` rule above.

### 🕒 Baseline commit 多樣性 / Baseline commit diversity

**[中]** `verified_zephyr_bugs.json`（150 筆，截至 2026-08-24）的案例目前分散在 3 個已驗證乾淨的 pinned commit 上：主要的 `bc460feabe7038dc876782557e39be791d6c24e9`（2026-07-24，139 筆／92.7%）、`SECOND_BASELINE_COMMIT` `4b02c5d60ae620fb23cbea58516e3ea7388c2f75`（2026-05-14，5 筆）、`THIRD_BASELINE_COMMIT` `5286027c85945d043a814d2d1783b3e935e5256e`（2026-03-20，5 筆），另有 1 筆挖礦案例帶著自己的歷史 PR base commit。這仍然是一種多樣性風險：任何在 2026-07-24 之後訓練、且訓練資料涵蓋公開 GitHub 歷史的通用 LLM，都可能對主要那個特定原始碼快照本身有先驗記憶，跟它是否真的理解注入的 bug 機制無關——92.7% 集中在單一 commit，離真正緩解這個風險還有一段距離。`dataset/scripts/mine_commits.py` 的 `INJECTION_CATALOG` 支援任意 entry 帶 `"baseline_commit"` 覆蓋預設共用的 commit（見該檔案裡 `SECOND_BASELINE_COMMIT`/`THIRD_BASELINE_COMMIT` 常數上方的註解，包含挑選歷史 commit 時要注意的 Zephyr SDK 版本相容性陷阱）——`tools/fault_injector.py`/`dataset/scripts/verify_cases.py` 本來就是逐案例讀取 `broken_commit`，從來就沒有「整批必須共用同一個 commit」的架構限制。未來要真正緩解「絕大多數案例集中在單一 commit」這個統計，需要之後的 session 持續把新案例分散注入到多個已驗證乾淨的 pinned commit（或新增第四個 pin），而不是預設一律沿用最早那一個。

**[EN]** Cases in `verified_zephyr_bugs.json` (150 total, as of 2026-08-24) are currently spread across 3 verified-clean pinned commits: the primary `bc460feabe7038dc876782557e39be791d6c24e9` (2026-07-24, 139 cases / 92.7%), `SECOND_BASELINE_COMMIT` `4b02c5d60ae620fb23cbea58516e3ea7388c2f75` (2026-05-14, 5 cases), `THIRD_BASELINE_COMMIT` `5286027c85945d043a814d2d1783b3e935e5256e` (2026-03-20, 5 cases), plus 1 mined case carrying its own historical PR-base commit. This remains a real diversity risk: any general-purpose LLM trained after 2026-07-24 on public GitHub history could have prior exposure to that primary snapshot, independent of whether it understands the injected bug's mechanism — 92.7% concentration on one commit is still far from resolving this. `dataset/scripts/mine_commits.py`'s `INJECTION_CATALOG` supports any entry carrying a `"baseline_commit"` override (see the comments above the `SECOND_BASELINE_COMMIT`/`THIRD_BASELINE_COMMIT` constants in that file, including a Zephyr-SDK-version-compatibility trap worth knowing about when picking a historical commit) — `tools/fault_injector.py`/`dataset/scripts/verify_cases.py` already read `broken_commit` per-case; there was never an architectural requirement for a whole batch to share one commit. Actually moving the needle on "the large majority of cases concentrated on one commit" needs future sessions to keep spreading new cases across multiple verified-clean pinned commits (or add a fourth pin), instead of defaulting back to the earliest one.

## 📄 License (授權)
MIT License. See LICENSE for more information.
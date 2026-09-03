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

### ⚠️ `evaluate.py` 設計要求：禁止把 `mutate_inject.py` 留下的 `.orig` 備份暴露給待測 agent / Design requirement: never expose `mutate_inject.py`'s `.orig` backup files to the agent under evaluation

**[中]** `tools/mutate_inject.py` 每次執行 mutation 之前，都會先用 `shutil.copyfile` 把原始檔案備份成 `<檔案>.orig`（供它自己的 `--revert` 模式使用）——**這個備份檔案的內容，就是這個案例被注入 bug 之前的正確版本**。這個備份從來不會被自動清除，`evaluate.py` 的 `prepare_broken_workspace()` 過去只清除 `.git`，完全沒意識到這第二種殘留管道：`agents/patch_expert.py`/`core/baseline_pipelines.py` 的 `collect_relevant_context_paths()` 跟 `graph_rag/hybrid_retriever.py` 的 `_is_indexable_file()`，都用 `filename.startswith("Kconfig.")` 判斷「看起來像 Kconfig 相關的檔案」，`Kconfig.orig` 剛好符合這個條件——2026-09-03 實測（`inject_kconfig_fcb_depends` pilot）證實 `Kconfig.orig` 真的被 Hybrid RAG 檢索到、內容真的被讀進 `project_files_content`、餵給了 Patch Expert 的 LLM 呼叫，不是純理論風險。跟上面 `.git` 那條同樣性質：**agent 完全不需要理解問題，直接照抄 `.orig` 裡的內容就能通過修復**，讓分數失去意義。

`evaluate.py` 動工時，硬性要求：
1. 準備好要交給 agent 的 broken workspace 之後、把控制權交還給 agent 之前，**必須先移除所有 `*.orig` 檔案**（`mutate_inject.py` 每次呼叫都會產生一個，compound 案例的每個 injection 各自對應一個）。
2. 把「移除 `.orig` 之後，agent 的工作目錄裡確實沒有任何 `*.orig` 殘留」這件事，寫成 `evaluate.py` 自己的一個驗收測試，不要只靠人工檢查——跟 `.git` 那條的第 3 點是同一種紀律。
3. 任何未來新增的、用檔名前綴/副檔名判斷「這個檔案看起來相關」的邏輯（例如 Knowledge Expert 的檢索範圍、Patch Expert 的 context 蒐集範圍），都要意識到這類寬鬆比對可能意外撈進非原始碼的殘留檔案，不是只有 `.orig` 這一種——加新的檔案類型判斷時，一併確認 workspace 準備階段有沒有可能留下同類殘留。

**[EN]** Every time `tools/mutate_inject.py` runs a mutation, it first backs up the original file to `<file>.orig` via `shutil.copyfile` (for its own `--revert` mode) — **that backup's content is the case's correct, pre-injection version**. This backup was never cleaned up automatically; `evaluate.py`'s `prepare_broken_workspace()` previously only stripped `.git`, unaware of this second leak channel: both `agents/patch_expert.py`/`core/baseline_pipelines.py`'s `collect_relevant_context_paths()` and `graph_rag/hybrid_retriever.py`'s `_is_indexable_file()` use `filename.startswith("Kconfig.")` to recognize "looks like a Kconfig-related file", which `Kconfig.orig` happens to match — confirmed empirically (2026-09-03, `inject_kconfig_fcb_depends` pilot) that `Kconfig.orig` was actually retrieved by Hybrid RAG, its content actually read into `project_files_content`, and actually fed to the Patch Expert's LLM call — not a hypothetical risk. Same category as the `.git` issue above: **the agent needs zero understanding of the problem, just copy `.orig`'s content, to "resolve" the case**, making the score meaningless.

When building `evaluate.py`, this is a hard requirement:
1. **Strip every `*.orig` file before handing control to the agent** — `mutate_inject.py` produces one per invocation, so a compound case's multiple injections each leave their own.
2. Turn "the agent's workspace genuinely has no `*.orig` residue after stripping it" into one of `evaluate.py`'s own pre-flight assertions, not something only checked by hand — the same discipline as point 3 of the `.git` rule.
3. Any future filename-prefix/extension-based "this file looks relevant" logic (e.g. Knowledge Expert's retrieval scope, Patch Expert's context-collection scope) should account for this class of loose matching potentially sweeping in non-source residue files, not just `.orig` specifically — when adding a new file-type check, also confirm workspace prep can't leave behind a similar residue matching it.

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

## 📦 最終資料集 `final_dataset.json` / The curated final dataset

**[中]** `verified_zephyr_bugs.json`（158 筆，截至 2026-08-27）是這個專案從一開始累積至今、從未刪減過的**完整驗證池**——任何通過雙向驗證閘（合成注入）或單向重現閘（真實挖礦）的案例都會留在這裡，做為未來擴充、重新篩選的原始素材。`final_dataset.json`（143 筆，全數為合成注入）則是從這個池子裡**策展**出來、實際支撐 thesis proposal 的最終資料集。兩者的差異分兩類：（a）移除幾乎完全重複的內容，不涉及刪除任何真正獨立的 bug；（b）排除全部 9 筆真實挖礦案例——這是方法論範圍決定，不是這些案例品質有問題（見下方「挖礦擴充」一節）：

1. **`thread_priority_swap` 的 4 板驗證，砍到 2 板**：`inject_thread_priority_swap_semaphore` 這組 mutation 曾在 native_sim/qemu_riscv32/qemu_cortex_a53/qemu_xtensa 上驗證過完全相同的字面文字置換，用來證明 operator 本身能跨架構運作。保留 native_sim + qemu_riscv32 兩個代表（仍然涵蓋兩種不同指令集），移除 cortex_a53/xtensa 兩筆——4 份逐字相同的「答案」對評估沒有額外資訊量，只是同一題重複問 4 次。
2. **kconfig `*_EMUL` 模板集中度砍半**：這次 session 為了稀釋 `runtime_crash` 的 operator 集中度，密集挖了 10 個「反轉 `depends on DT_HAS_ZEPHYR_..._ENABLED`」的 `*_EMUL` kconfig 案例（ADC/DAC/DMA/ESPI/I2C/RTC/BBRAM/BIOMETRICS/GPIO/GNSS），結果讓 `kconfig` 分類本身變成另一種模板集中（62.5% 用同一招）。保留 `DAC_EMUL`（同時涵蓋兩個 baseline commit，具備多樣性價值）與這次新挖的 3 個最新穎的驅動家族（GPIO/GNSS/BIOMETRICS），移除 4 筆（RTC/ESPI/DMA/ADC_EMUL 的**獨立 kconfig 版本**——它們在 `compound` 分類裡跟 DTS mutation 搭配的版本完全不受影響，那是不同的案例）。

**特別強調：所有跨 `SECOND_BASELINE_COMMIT`/`THIRD_BASELINE_COMMIT` 的「同一個 mutation、不同 baseline commit」配對（10 組）全部保留在兩邊**——這些不是重複，是這個專案花了好幾個 session 才建立起來的 baseline commit 多樣性證據，砍掉任何一邊都會直接侵蝕這個已經很辛苦才往前推進的統計。

**[EN]** `verified_zephyr_bugs.json` (158 cases, as of 2026-08-27) is this project's complete, never-pruned **verification pool** — every case that passes either the two-sided gate (synthetic injection) or the one-sided reproduction gate (real mining) stays here as raw material for future expansion or re-curation. `final_dataset.json` (143 cases, 100% synthetic injection) is the **curated** final release that actually backs the thesis proposal. The two files differ in two ways: (a) removing near-total content duplication, not deleting any genuinely independent bug; (b) excluding all 9 real mined cases — a methodology scope decision, not a quality judgment on those cases (see "Mining expansion" below):

1. **`thread_priority_swap`'s 4-board proof capped to 2**: the `inject_thread_priority_swap_semaphore` mutation was verified with the identical literal text swap on native_sim/qemu_riscv32/qemu_cortex_a53/qemu_xtensa, proving the operator works cross-architecture. Kept native_sim + qemu_riscv32 (still 2 distinct instruction sets) and dropped the cortex_a53/xtensa pair — 4 byte-identical "answers" add no extra information to an evaluation, they're the same question asked 4 times.
2. **kconfig's `*_EMUL` template concentration halved**: this session mined 10 "invert `depends on DT_HAS_ZEPHYR_..._ENABLED`" `*_EMUL` kconfig cases (ADC/DAC/DMA/ESPI/I2C/RTC/BBRAM/BIOMETRICS/GPIO/GNSS) to dilute `runtime_crash`'s operator concentration, which incidentally made `kconfig` itself a template monoculture (62.5% one trick). Kept `DAC_EMUL` (spans both baseline commits, real diversity value) plus the 3 newest, most distinct driver families from this session (GPIO/GNSS/BIOMETRICS); dropped 4 (RTC/ESPI/DMA/ADC_EMUL's **standalone kconfig** versions — their `compound`-category siblings paired with a DTS mutation are untouched, different cases entirely).

**Explicitly preserved**: all 10 "same mutation, different pinned baseline commit" pairs across `SECOND_BASELINE_COMMIT`/`THIRD_BASELINE_COMMIT` are kept on *both* sides — these aren't duplication, they're the hard-won baseline-commit-diversity evidence this project took several sessions to build; dropping either side would directly erode a statistic that's already difficult to move.

### 挖礦擴充 / Mining expansion

**[中]** 稽核一開始發現最嚴重的失衡不是任何注入分類太小，而是**整個資料集幾乎 100% 是合成注入**——真實挖礦案例從 session 開始時的 1 筆（0.7%），到這次用 `.env` 裡本來就設好、但過去 session 一直沒用上的 `GITHUB_TOKEN`（解除了 GitHub API 60 次/小時的速率限制），重新對 `label:bug` 搜尋範圍外的 PR（標題含 `fix:`、或記憶體安全關鍵字如 NULL pointer/use-after-free/double-free/buffer-overflow/out-of-bounds）挖礦，手動篩掉「猜到 `samples/hello_world`」與「明顯需要特定廠商硬體」的候選後，對 22 個候選跑真實 Docker 驗證閘，其中 8 個通過、**2 個雖然一開始通過但複查完整 log 後發現是環境假陽性**（一個是 backport 到舊分支、需要 SDK 0.16 的歷史 commit；一個是 mining pipeline 猜出的板子名稱在 Zephyr 新版板子命名規則下其實不合法）而移除，淨增 6 筆真實案例，把挖礦比例從 0.7% 拉到 5.7%。

**後續決定：thesis proposal 明確主張「Zephyr-Eval 以 controlled fault injection 作為唯一建構方式」，用來對照 SWE-bench 之類挖礦式 benchmark 的記憶風險——即使只混入一小部分（6%）真實案例，也會跟這個已經寫死、當作賣點的方法論主張產生內部矛盾，且 9 筆案例對任何一個研究問題的統計檢定力都可忽略不計。因此最終決定：`final_dataset.json` 不納入這 9 筆挖礦案例，維持 100% 合成注入、143 筆，完整落在提案的 100–150 目標區間內。這 9 筆已驗證的真實 bug 保留在 `verified_zephyr_bugs.json` 完整驗證池中，供未來若決定額外做 real-world generalization check 時使用，不會被刪除。**

**[EN]** The audit's initial finding wasn't any injected category being too small — it was that the dataset was **almost 100% synthetic injection**. Real mined cases went from 1 (0.7%) at session start to 8 accepted (after removing 2 that initially passed but turned out to be environment false positives on full-log review — one a backport to an old branch requiring Zephyr SDK 0.16, one a board name the mining pipeline guessed that's actually invalid under Zephyr's current board-naming scheme) out of 22 real candidates verified via Docker, once this session discovered `GITHUB_TOKEN` was already configured in `.env` (removing the 60/hr unauthenticated GitHub API rate limit that constrained prior mining sessions) and searched beyond the near-exhausted `label:bug` query (titles containing `fix:`, or memory-safety keywords like NULL pointer/use-after-free/double-free/buffer-overflow/out-of-bounds), manually filtering out `samples/hello_world` fallback guesses and vendor-hardware-specific drivers before spending Docker time. Net 6 new genuine cases, lifting the mined fraction from 0.7% to 5.7%.

**Follow-up decision**: the thesis proposal explicitly claims Zephyr-Eval uses controlled fault injection as its *sole* construction method, contrasted against SWE-bench-style mined benchmarks' memorization risk — even a small (6%) mined slice would create an internal contradiction with that stated, load-bearing methodological claim, and 9 cases carry negligible statistical power for any of the research questions anyway. Final decision: `final_dataset.json` excludes all 9 mined cases, staying 100% synthetic injection at 143 cases, comfortably inside the proposal's 100–150 target. These 9 verified real bugs remain intact in the `verified_zephyr_bugs.json` full pool, not deleted, in case a future real-world generalization check is ever wanted.

## 📄 License (授權)
MIT License. See LICENSE for more information.
# Zephyr-LLM-Repair: Autonomous RTOS Bug Repair Framework 
# 基於多代理人閉環驗證之 Zephyr RTOS 錯誤自主修復框架

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)
![Framework](https://img.shields.io/badge/framework-LangGraph-orange)
![RTOS](https://img.shields.io/badge/RTOS-Zephyr-brightgreen)
![Simulation](https://img.shields.io/badge/simulation-QEMU-yellow)

## 📖 Introduction / 專案簡介

**[EN]** This project is an LLM-driven multi-agent closed-loop debugging framework designed specifically for the open-source RTOS, Zephyr. Utilizing LangGraph for a directed graph state machine, this system retrieves Zephyr source, Kconfig and DTS context with Hybrid RAG (BM25 + semantic embedding); the domain-specific Graph RAG for Kconfig/DTS is kept in the code but off by default (future work). It performs deterministic builds and QEMU runtime verification within fully isolated Docker containers to achieve highly autonomous and precise bug repair.

**[中]** 本專案是一個基於大型語言模型 (LLM) 的多代理人閉環除錯框架，專為開源即時作業系統 (RTOS) Zephyr 設計。本系統利用 LangGraph 構建有向圖狀態機，以 Hybrid RAG (BM25 + 語意 embedding) 檢索 Zephyr 原始碼、Kconfig 與 DTS 上下文 (針對 Kconfig/DTS 的領域圖譜檢索 Graph RAG 仍保留在程式中，但預設關閉，列為未來工作)，並在完全隔離的 Docker 容器中進行決定性的建置與 QEMU 執行期驗證，實現高度自主且精確的錯誤修復。

---

## 🏗️ System Architecture / 系統架構

To ensure high scalability and maintainability, the codebase is deeply decoupled based on **"State Management"**, **"Agent Nodes"**, **"Graph Retrieval"**, and **"Toolchains"**. 

為了讓專案具備高擴充性與可維護性，程式碼架構依照「狀態管理」、「代理人節點」、「圖譜檢索」與「工具鏈」進行深度解耦。

---

## 📂 Repository Structure / 專案結構

```text
zephyr-llm-repair/
│
├── evaluate.py                 # 評測進入點：逐筆準備 workspace、執行 pipeline、判定結果
├── analyze_results.py          # results.json → RQ 指標 (TTR、RQ2 檢索命中等)
├── requirements.txt / Dockerfile / .env_example
│
├── core/                       # 各 pipeline 的執行迴圈與共用基礎
│   ├── workflow.py             # Proposed：LangGraph 多代理人閉環
│   ├── proposed_opt.py         # Proposed 優化設定 (ZEPHYR_PROPOSED_OPT)
│   ├── baseline_pipelines.py   # B1 / B2 / B3 執行迴圈
│   ├── agentless_pipeline.py   # Agentless (定位 → 多候選 patch → 驗證)
│   ├── openhands_pipeline.py   # OpenHands (容器內 agent，見 docs/openhands_changes.md)
│   ├── llm_provider.py         # 模型供應商 (gemini / anthropic / openai / qwen)
│   ├── llm_retry.py            # 重試、暫時性錯誤與額度用完 (daily_quota) 判定
│   ├── llm_usage.py            # token 用量紀錄
│   ├── protected_files.py      # 受保護檔案 (測試/規格) 在判定前還原
│   ├── state.py / trajectory.py
│
├── agents/                     # LLM 呼叫
│   ├── supervisor.py           # 重試上限與跨迭代記憶壓縮
│   ├── analyzer.py             # 錯誤分析 (結構化輸出)，決定檢索策略
│   ├── knowledge_expert.py     # 檢索：Hybrid RAG (預設) / Graph RAG (預設關閉)
│   ├── patch_expert.py         # 產生 SEARCH/REPLACE patch
│   ├── baselines.py            # B1/B2/B3 的 LLM 呼叫
│   ├── agentless.py            # Agentless 的 LLM 呼叫
│   └── spec_rule.py            # 所有 patch prompt 共用的測試規格說明 (受保護檔案會被還原)
│
├── graph_rag/
│   ├── hybrid_retriever.py     # Hybrid RAG：BM25 + Gemini embedding (FAISS)
│   ├── case_graph.py / build_case_graph.py   # 逐案例 Kconfig/DTS 圖 (ZEPHYR_GRAPH_RAG，預設 0)
│   ├── build_graph.py / retriever.py / parsers/   # 早期全域圖譜 (未用於正式評測)
│
├── tools/                      # 評測工具鏈與批次腳本 (被 .gitignore，追蹤中的檔案要 git add -f)
│   ├── patch_applier.py / log_filter.py / qemu_oracle.py / static_checker.py / devops_analyzer.py
│   ├── mutate_inject.py / fault_injector.py / probe_mutants.py   # 注入 bug
│   ├── validate_v3_cases.py / gold_patch_check.py / flaky_check.py   # 資料集驗證
│   ├── analyze_v3.py / failure_taxonomy.py / failure_by_category.py / replay_patches.py   # 離線分析
│   ├── run_batch.sh / run_with_key_rotation.sh   # 批次執行與金鑰輪替
│   ├── openhands_runner.py / egress_proxy.py / openhands_image/   # OpenHands 容器與白名單 proxy
│   ├── v4/                     # Gemini 4 主實驗的批次腳本
│   └── xmodel/                 # 跨模型實驗 (Claude / Qwen)，偏差說明見 tools/xmodel/README.md
│
├── dataset/
│   ├── cases/                  # final_dataset_v3.json (139)、real_bugs_v3.json (13)；final_dataset.json 為 v2
│   ├── v3/                     # v3 的產生過程檔，說明見 dataset/v3/README.md
│   ├── scripts/                # 組裝 v3 資料集的腳本與注入素材
│   ├── dev_v2/                 # 開發集 (調參用，不與正式資料集重疊)
│   └── legacy/                 # v2 的挖礦與驗證腳本、完整驗證池
│
├── docs/openhands_changes.md   # OpenHands 整合的改動紀錄 (不改 SDK)
├── tests/                      # pytest 單元測試
└── eval_runs/                  # 評測結果 (被 .gitignore，commit 時用 git add -f)
```

### Pipelines (`evaluate.py --pipeline`)

| pipeline | 說明 / Description |
|---|---|
| `b1` | 單一 agent、單發、無檢索 (結構化輸出) / single agent, single shot, no retrieval |
| `b2` | B1 + Hybrid RAG (單發) / B1 + Hybrid RAG |
| `b3` | B2 + 閉環重試，每輪重新檢索 (`ZEPHYR_B3_GATE=1`：不合格的 patch 還原後在本輪重試) / B2 + closed-loop retries |
| `proposed` | B3 + 多代理人分工 (Analyzer / Knowledge / Patch / DevOps / QA / Supervisor) |
| `agentless` | Agentless 式：定位、多候選 patch、以建置/執行結果挑選 |
| `openhands` | OpenHands agent 在容器內自行操作，只能經白名單 proxy 連到 LLM API |

B1 → B2 → B3 → Proposed 每一步只多一個變因 (2026-11-hybrid 定義，見 `core/baseline_pipelines.py`)。
2.5 Flash 的 v3 結果用的是舊定義 (B2 只有 BM25、B3 無檢索)。

### 主要環境變數 / Main environment variables

| 變數 | 預設 | 用途 |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini 生成；未設 `GEMINI_EMBEDDING_API_KEY` 時也用於 embedding |
| `GEMINI_API_KEY2` | — | `run_with_key_rotation.sh` 在第一把碰到每日額度後換用 |
| `GEMINI_EMBEDDING_API_KEY` | — | Hybrid RAG embedding 專用金鑰 |
| `ANTHROPIC_API_KEY` 或 `Claude_API_KEY` | — | `--model-provider anthropic` |
| `QWEN_API_KEY` 或 `Bedrock_API_KEY` | — | `--model-provider qwen` (TUM proxy，需 TUM 網路/VPN) |
| `ZEPHYR_GEMINI_MODEL` | — | 覆寫 Gemini 模型名稱 |
| `ZEPHYR_PROPOSED_OPT` | `0` | Proposed 架構優化：不合格 patch 不耗輪次、上下文排序、記住無效方向 (正式實驗為 1) |
| `ZEPHYR_B3_GATE` | `0` | B3 套用閘門 (正式實驗為 1) |
| `ZEPHYR_HYBRID_RAG` | `1` | Hybrid RAG 開關 |
| `ZEPHYR_GRAPH_RAG` | `0` | Graph RAG 開關 (預設關閉) |
| `ZEPHYR_AGENTLESS_N` | `4` | Agentless 候選 patch 數 |
| `ZEPHYR_OH_IMAGE` / `ZEPHYR_OH_MAX_STEPS` / `ZEPHYR_OH_TIMEOUT_MIN` | 見 `core/openhands_pipeline.py` | OpenHands 設定 |
| `ZEPHYR_ANTHROPIC_MAX_TOKENS` / `ZEPHYR_QWEN_MAX_TOKENS` / `ZEPHYR_QWEN_BASE_URL` | 見 `core/llm_provider.py` | 跨模型設定 |
| `DATASET` | — | `tools/run_batch.sh` 使用的資料集路徑 |
| `GITHUB_TOKEN` | — | 只有 legacy 挖礦腳本用到 |

## 🛠️ Environment Setup
The execution of this system is divided into the "Host" (running LLM agent logic) and the "Container" (running Zephyr compilation and QEMU simulation).

本系統的執行分為「主機端 (Host)」(執行 LLM 代理人邏輯) 與「容器端 (Container)」(執行 Zephyr 編譯與 QEMU 模擬)。

1. 先決條件 (Prerequisites)
* Python 3.10+
* Docker Desktop / Docker Engine (用於啟動 Zephyr 決定性沙盒)
* 至少一個 LLM 供應商的金鑰；Hybrid RAG 的 embedding 一律用 Gemini

2. 主機端安裝 (Host Setup)
```bash
git clone https://github.com/yourusername/zephyr-llm-repair.git
cd zephyr-llm-repair

python3.10 -m venv venv
# Windows: .\venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 從範本建立 .env，再填入金鑰 (.env 不會進 git)
# Create .env from the template and fill in your keys (.env is gitignored)
cp .env_example .env
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
Run a pipeline on dataset cases through `evaluate.py`; results go to `--runs-dir` (default `eval_runs/<timestamp>`).
透過 `evaluate.py` 對資料集案例執行指定 pipeline，結果寫到 `--runs-dir` (預設 `eval_runs/<時間>`)。
```bash
# Run a single case (執行單一案例)
python evaluate.py --dataset dataset/cases/final_dataset_v3.json --pipeline proposed --case-id <case_id>

# Run the whole dataset (跑整個資料集)
python evaluate.py --dataset dataset/cases/final_dataset_v3.json --pipeline proposed --all

# Another model provider (換模型供應商；只用 pro 模型)
python evaluate.py --dataset dataset/cases/final_dataset_v3.json --pipeline b1 --model-provider anthropic --single-model --all

# Batch with resume + key rotation (批次執行，已完成的案例會跳過)
tools/run_with_key_rotation.sh <pipeline> <out_dir> <work_dir> --single-model
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

## 📦 資料集 / Datasets

| 檔案 | 筆數 | 內容 |
|---|---|---|
| `dataset/cases/final_dataset_v3.json` | 139 | 正式資料集 (v3)：runtime_crash 63、c_syntax / kconfig / dts / compound 各 19；共 26 個 base commit |
| `dataset/cases/real_bugs_v3.json` | 13 | 真實 bug 對照集，每筆各自的 commit |
| `dataset/v3/eval/repeat_subset_40.json` | 40 | 依類別分層抽樣的重複實驗子集 (`tools/sample_repeat_subset.py`) |
| `dataset/cases/final_dataset.json` | 143 | v2 資料集 (已凍結，保留給 v2 結果) |

**[中]** v3 的產生流程、驗證 (重現預期失敗、gold patch 119/119 resolved、穩定性檢查) 與 2026-10 的稽核修正，都記在 `dataset/v3/README.md`。
v3 把 base commit 從 v2 的 3 個 (其中一個佔 92.7%) 擴成以 24 個每週 commit (2026-03 至 08) 為主、共 26 個，以降低模型對單一原始碼快照的記憶風險。
統計時 runtime_crash 佔 63/139，所以除了整體，也報五類等權與 runtime 抽成 19 筆的敏感度分析。

**[EN]** How v3 was built and validated (reproduces the expected failure, gold patch 119/119 resolved, stability checks) and the 2026-10 audit fixes are in `dataset/v3/README.md`.
v3 spreads cases over 26 base commits, mostly 24 weekly ones (2026-03..08), instead of v2's 3 (one holding 92.7%), reducing the risk of a model memorizing one source snapshot.
Because runtime_crash is 63/139, results are reported overall, category-balanced, and with runtime subsampled to 19.

### v2 歷史 / v2 history

v2 的 `final_dataset.json` (143 筆，100% 合成注入) 是從 `dataset/legacy/cases/verified_zephyr_bugs.json` 完整驗證池策展而來：
移除幾乎重複的案例 (`thread_priority_swap` 的 4 板驗證只留 2 板、kconfig `*_EMUL` 模板減半)，並排除 9 筆真實挖礦案例，
以符合提案「只用 controlled fault injection 建構」的方法論。挖礦腳本在 `dataset/legacy/scripts/` (需要 `GITHUB_TOKEN`)。
v3 之後真實 bug 改成獨立的對照集 (`real_bugs_v3.json`)，不混入主資料集。

## 📄 License (授權)
MIT License. See LICENSE for more information.
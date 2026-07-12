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

## 📄 License (授權)
MIT License. See LICENSE for more information.
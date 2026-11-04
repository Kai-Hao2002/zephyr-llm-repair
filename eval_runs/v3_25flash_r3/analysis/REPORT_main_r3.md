# v3 r3 主實驗結果（gemini-2.5-flash，2026-10-24）

資料：`eval_runs/v3_25flash_r3/{proposed,b3,agentless,openhands}/`，主資料集 139 筆（runtime 63；c_syntax、kconfig、dts、compound 各 19）。
B1、B2 沿用 r2 的結果（`eval_runs/v3_25flash_r2/`，commit dbb8c72，程式路徑之後沒有變動）。
數字由 `r3_main_summary.py` 產生，完整輸出在 `main_summary_stdout.txt`、`main_summary.json`。

**注意**
- 這裡的「修好」是評測時的判定（通過 target_test，加上受保護檔案和測試完整性檢查），**還沒有人工判讀**。
  r2 經人工判讀後，「只算還原或等價修法」的修復率比評測判定低（Proposed 78 → 75 筆）。r3 的判讀在第 5 步。
- 每條 pipeline 只跑一次。r1 和 r2 之間，每條 pipeline 有 6～17 筆案例的結果改變，所以類別層級的小差異不要過度解讀，
  要等第 4 步的重複實驗。
- 和 r2 相比，Proposed 有兩處修改：Analyzer 規則恢復成原始設計（bfa17ce）；Hybrid RAG 不再受 Analyzer 的關鍵字控制（5a44080）。

## 1. 修復率

| Pipeline | 修好 | 修復率 | 95% CI（Wilson） |
|---|---|---|---|
| B1 | 0 | 0.0% | 0.0–2.7 |
| B2（r2） | 44 | 31.7% | 24.5–39.8 |
| B3 | 50 | 36.0% | 28.5–44.2 |
| Agentless | 59 | 42.4% | 34.5–50.8 |
| OpenHands | 75 | 54.0% | 45.7–62.0 |
| **Proposed** | **80** | **57.6%** | 49.2–65.5 |

## 2. 成對比較

精確 McNemar 檢定，對下表 9 組做 Holm 校正；差異的信賴區間用成對 bootstrap（10,000 次）。

| 比較 | 差異 | 95% CI | 只有前者修好 | 只有後者修好 | Holm p |
|---|---|---|---|---|---|
| Proposed vs B3 | +21.6 | +12.9～+30.2 | 38 | 8 | 6.5e-5 |
| Proposed vs Agentless | +15.1 | +6.5～+24.5 | 33 | 12 | 0.012 |
| **Proposed vs OpenHands** | **+3.6** | **−5.8～+12.9** | 24 | 19 | **0.63（不顯著）** |
| Proposed vs B2 | +25.9 | +18.0～+33.8 | 39 | 3 | 4.5e-8 |
| Proposed vs B1 | +57.6 | +48.9～+65.5 | 80 | 0 | 1.5e-23 |
| OpenHands vs Agentless | +11.5 | +2.2～+20.9 | 30 | 14 | 0.091 |
| OpenHands vs B3 | +18.0 | +8.6～+27.3 | 38 | 13 | 0.0037 |
| Agentless vs B3 | +6.5 | −2.9～+15.1 | 25 | 16 | 0.63 |
| B3 vs B2 | +4.3 | −1.4～+10.1 | 11 | 5 | 0.63 |

**重點**
- Proposed 顯著勝過 B3、Agentless、B2、B1。
- **Proposed 和 OpenHands 沒有顯著差異。** 兩者各有 20 筆左右只有自己修好，互補性高。
- 2×2 的兩個方向：
  - 「領域專用 vs 通用」：Proposed 對 Agentless +15.1（顯著），對 OpenHands +3.6（不顯著）。
  - 「固定流程 vs 自主 agent」：OpenHands 對 Agentless +11.5，未校正 p=0.023，Holm 校正後 0.091。

## 3. 各類別（修好筆數）

| 類別 | n | B2 | B3 | Agentless | OpenHands | Proposed |
|---|---|---|---|---|---|---|
| runtime | 63 | 17 | 19 | 30 | **39** | 34 |
| c_syntax | 19 | 8 | **13** | 10 | 9 | 9 |
| kconfig | 19 | 8 | 7 | 4 | 8 | **14** |
| dts | 19 | 11 | 11 | 13 | 14 | **15** |
| compound | 19 | 0 | 0 | 2 | 5 | **8** |

- Proposed 的優勢集中在設定類：kconfig、dts、compound 合計修好 37 筆，OpenHands 27 筆，Agentless 19 筆。
- runtime 是 OpenHands 最好（39 筆，Proposed 34 筆）：它可以自己讀程式碼、反覆執行測試。
- c_syntax 是 B3 最好（13 筆）。Proposed 只修好 9 筆，跟 r2 一樣偏低；主要原因是錯誤出現在標頭檔時找不到注入檔（r2 失敗分析的 E 類）。

**依注入位置**

| | n | B2 | B3 | Agentless | OpenHands | Proposed |
|---|---|---|---|---|---|---|
| 注入在核心程式（kernel/lib/drivers…） | 53 | 12 | 10 | 19 | 30 | 31 |
| 注入在 target_app 底下 | 86 | 32 | 40 | 40 | 45 | 49 |
| 初始 log 沒有寫出注入檔路徑 | 91 | 20 | 22 | 29 | 45 | 47 |
| 初始 log 有寫出注入檔路徑 | 48 | 24 | 28 | 30 | 30 | 33 |

- log 沒有給出路徑、需要自己定位的案例，Proposed 和 OpenHands 明顯勝過其他 pipeline。log 有寫出路徑時，各 pipeline 的差距就小很多。
- 板子：native_sim 97 筆中 Proposed 修好 55 筆、OpenHands 50 筆；QEMU 42 筆兩者都是 25 筆。

## 4. 和 r2 比較（同一條 pipeline）

| | r2 | r3 | 兩輪都修好 | 只有 r3 修好 | 只有 r2 修好 |
|---|---|---|---|---|---|
| B3 | 49 | 50 | 42 | 8 | 7 |
| Proposed | 78 | 80 | 63 | 17 | 15 |

- 總數幾乎一樣，但個別案例變動不小，Proposed 有 32 筆結果翻轉。
- 類別層級上，Proposed 的 compound 從 3 筆變 8 筆、kconfig 從 11 筆變 14 筆；runtime 從 37 筆變 34 筆、c_syntax 從 11 筆變 9 筆。
  - 這可能和 Graph RAG 加回來有關，也可能只是單次執行的變動。第 3 步的 ablation 顯示關閉 Graph RAG 後 compound 6 筆、kconfig 12 筆（見第 10 節），差距在單次執行的變動範圍內；重複實驗（第 4 步）目前先跳過。

## 5. 成本與時間

金額用 2.5-flash 的牌價估算（input $0.30/M、output $2.50/M），不含 embedding。

| | input/筆 | output/筆 | LLM 呼叫/筆 | 金額/筆 | **金額/修好一筆** | 時間/筆 | 時間/修好一筆 |
|---|---|---|---|---|---|---|---|
| B2 | 36k | 12k | 1.0 | $0.040 | $0.13 | 2.7 分 | 8.4 分 |
| B3 | 50k | 39k | 3.7 | $0.112 | $0.31 | 6.6 分 | 18.3 分 |
| Agentless | 33k | 33k | 5.9 | $0.091 | $0.21 | 6.9 分 | 16.4 分 |
| OpenHands | **697k** | 10k | 22.4 | $0.234 | $0.43 | 7.2 分 | 13.4 分 |
| Proposed | 107k | 40k | 7.6 | $0.132 | $0.23 | 10.9 分 | 19.0 分 |

- OpenHands 的 input token 是 Proposed 的 6.5 倍：它每一步都會把整段對話歷史重新送出。
  - 實際帳單比較低：litellm 回報的是 $8.80，按牌價算是 $32.56，差距來自 Gemini 的隱式快取。
  - 論文比較成本時，要說明採用哪一種算法。
- 以牌價計算，Proposed 每修好一筆 $0.23，約是 OpenHands 的一半，和 Agentless 相近。
- Proposed 的時間最長，因為每筆都要在容器裡建 Graph RAG 的圖（約 40 秒），而且每輪都要編譯。
- B1 的 output token 很多（每筆 14k），因為它要輸出整個檔案。

## 6. 其他觀察

- **修好的 patch 有沒有改到注入檔**：B2、B3、Agentless、OpenHands 修好的案例全部都改到了注入檔。Proposed 有 2 筆沒改到注入檔，人工判讀時優先看，多半是繞過：
  - compound comparator_shell：只改了 `comparator_fake_comp.c`，Kconfig 和 overlay 都沒動；
  - runtime can_loopback：改了 `include/zephyr/drivers/can.h`，沒有改 `can_loopback.c`。
- **違反規格（改了測試語意）**：

  | | 輪次 | 案例數 |
  |---|---|---|
  | Proposed | 37 | 14 |
  | B3 | 34 | 15 |
  | B2 | 10 | 10 |
  | OpenHands | 10 | 10 |
  | Agentless | 7 | 5 |

  閉環重試的 pipeline 比較容易反覆去改測試。
- **Proposed 的檢索**（注入檔是否出現在檢索結果）：
  - 第 1 輪的 hit@1 是 41/139，hit@8 是 61/139，MRR 0.347；任何一輪檢索到的有 75 筆。
  - 有命中的 61 筆中修好 42 筆（69%），沒命中的 78 筆中修好 38 筆（49%）。
  - 和 r2 比，hit@8 從 69 筆降到 61 筆：Analyzer 規則恢復後，沒有關鍵字的輪次從 78/424 輪增加到 157/436 輪，這些輪次改用 log 當查詢，效果比較差。
- **OpenHands**：
  - 結束方式：自己判斷完成 104 筆、跑滿 50 步 28 筆、被判定卡住 7 筆。跑滿 50 步的只修好 2 筆，以 compound（9 筆）和 c_syntax（8 筆）最多。
  - 平均每筆 22.7 步。
  - 139 筆的對外連線紀錄都只放行了 Gemini API。被擋下的只有 agent-server 啟動時要連的 `raw.githubusercontent.com`，沒有其他對外連線。
- **暫時性錯誤**：B3 1 筆、Agentless 2 筆、OpenHands 11 筆碰到 503（Gemini 需求過高）。都移到 `archive/main_transient_*` 後單筆重跑，現在的結果全部是正式結果。

## 7. 待辦（第 5 步）

- 用 `tools/replay_patches.py` 重建 patch，人工判讀「內容不同」的修好案例，算出「只算還原或等價修法」的修復率。上面那 2 筆沒改到注入檔的優先。
- 跑 `failure_by_category.py`，做 Agentless、OpenHands 的失敗分類；`analyze_v3.py` 的 PIPELINES 要加入這兩條。
- 等第 3、4 步跑完後，再分析 Graph RAG 和 Hybrid RAG 各自的貢獻，以及重複實驗的變異。（第 3 步見第 10 節；第 4 步見第 14 節。）

## 8. 人工判讀後的修復率（2026-10-26 補）

判讀檔是 `manual_labels_main.json`，對象是 `replay_main.json` 裡 overall=differs/partial 的修好案例。

- r3 共 60 筆：B3 11、Agentless 13、OpenHands 13、Proposed 23。
  - 其中 15 筆的 diff 和 r1/r2 判讀過的案例完全相同，直接沿用當時的判讀；其餘 45 筆逐筆判讀。
- B2 沿用 r2 的結果和判讀。
- 離線重建後，沒有任何修好的案例違反規格，所以沒有 test_changed。
- 判讀者只有一人，還沒複核。

| | 修好 | 完全還原 | 等價修法 | 替代修法 | **只算還原或等價** | 95% CI |
|---|---|---|---|---|---|---|
| B2（r2） | 44 | 30 | 12 | 2 | 42（30.2%） | 23.2–38.3 |
| B3 | 50 | 39 | 7 | 4 | 46（33.1%） | 25.8–41.3 |
| Agentless | 59 | 46 | 12 | 1 | 58（41.7%） | 33.9–50.0 |
| OpenHands | 75 | 62 | 10 | 3 | 72（51.8%） | 43.6–59.9 |
| Proposed | 80 | 57 | 19 | 4 | **76（54.7%）** | 46.4–62.7 |

**替代修法（14 筆）**
- 過度放寬或拿掉 Kconfig 相依：
  - zbus：B3、Agentless，沿用 r1 的判讀；
  - dma_emul compound：OpenHands；
  - flash_map：Proposed，改成 select 一個拼錯的符號；
  - adt7420：OpenHands，拿掉 7410/7422 和 select I2C；
  - nvs：B3、OpenHands，改成別的符號或板子專用的條件；
  - lc709203f：B3，重複定義 config。
- fwcfg 的 `reg` 大小改錯：B3。
- 繞過問題：
  - comparator_shell compound：Proposed。兩個注入檔都沒改，改 `.c` 用 DT_NODELABEL 定義裝置；
  - can_loopback：Proposed，沿用 r1 的判讀，改的是 `can.h`。
- 語意錯誤但測試通過：msgq，Proposed。改寫 `k_msgq_put` 後，佇列有空間時不再把訊息交給等待中的讀取者。

**只算還原或等價時的成對比較**（Holm 校正 9 組）

| 比較 | 差異 | 95% CI | Holm p |
|---|---|---|---|
| Proposed vs B3 | +21.6 | +12.9～+30.2 | 6.5e-5 |
| Proposed vs Agentless | +12.9 | +4.3～+21.6 | 0.040 |
| Proposed vs OpenHands | +2.9 | −6.5～+12.2 | 0.96 |
| Proposed vs B2 | +24.5 | +16.5～+32.4 | 1.6e-7 |
| OpenHands vs Agentless | +10.1 | +1.4～+18.7 | 0.15 |
| OpenHands vs B3 | +18.7 | +9.4～+28.1 | 0.0013 |
| Agentless vs B3 | +8.6 | +0.0～+17.3 | 0.22 |
| B3 vs B2 | +2.9 | −2.9～+8.6 | 0.96 |

結論和第 2 節相同：Proposed 顯著勝過 B3、Agentless、B2，和 OpenHands 沒有顯著差異。Proposed 對 Agentless 的 p 值從 0.012 變成 0.040，仍然顯著。

各類別（只算還原或等價）：

| 類別 | B2 | B3 | Agentless | OpenHands | Proposed |
|---|---|---|---|---|---|
| runtime | 16 | 18 | 30 | 39 | 32 |
| c_syntax | 8 | 13 | 10 | 9 | 9 |
| kconfig | 7 | 4 | 3 | 6 | 13 |
| dts | 11 | 11 | 13 | 14 | 15 |
| compound | 0 | 0 | 2 | 4 | 7 |

真實 bug 的重建在 `replay_real.json`：修好的 pipeline×案例共 15 個（B3 1、Agentless 3、OpenHands 6、Proposed 5），重建後全部和上游修正內容不同（真實 bug 很少會逐字寫出和上游相同的修正）。這部分還沒有人工判讀。

## 9. 真實 bug：結果與人工判讀（2026-10-26 補）

- 判讀檔：`manual_labels_real.json`。
- 比較對象是上游的修正 commit F。15 個修好的 pipeline×案例全部重建後和 F 不同，所以全部逐筆判讀。
- 分類：
  - equivalent：和上游修正等價；
  - alternative：用不同方式讓回歸測試通過，但語意和上游不同或可能不完整；
  - incomplete：修好了回歸測試涵蓋的部分，但上游同一個 commit 的其他修正沒有做（例如別的驅動、文件）。
- 只有一位判讀者，還沒複核。

| | 修好（評測判定） | 等價 | 不完整 | 替代 | **等價才算** | 等價或不完整 |
|---|---|---|---|---|---|---|
| B3 | 1 | 1 | 0 | 0 | 1 | 1 |
| Agentless | 3 | 2 | 0 | 1 | 2 | 2 |
| OpenHands | 6 | 4 | 1 | 1 | **4** | 5 |
| Proposed | 5 | 3 | 1 | 1 | **3** | 4 |

（r2：Proposed 4、B2 1、B3 0、B1 0，都是評測判定、沒有人工判讀。B1、B2 沒有在 r3 重跑。）

**逐筆說明**
- **video_buffer_null**：4 條 pipeline 都修好，而且都和上游等價。這是最簡單的一筆，只要補一個 NULL 檢查。
- **stack_alloc_overflow**：Agentless、OpenHands、Proposed 都用了和上游相同的 `size_mul_overflow`，等價。
- **stream_flash_size_overflow**：OpenHands、Proposed 都等價。
- **fuel_gauge_cycle_count**：OpenHands、Proposed 只修了回歸測試涵蓋的 bq27z746。上游同時修正的 adp5360 驅動和 API 說明都沒改，判為不完整。
- **http_chunked_frag_len**：
  - OpenHands 和上游等價。
  - Agentless 改成把長度累加。同一個 recv_buf 裡有多個 chunk 時，長度會把 chunk 標頭也算進去，判為替代修法。
- **dynamic_stack_overflow**：OpenHands 只擋 `size == SIZE_MAX`，剛好是測試用的值，沒有加上游的溢位檢查，判為替代修法。
- **zms_find_ate_offset**：只有 Proposed 修好，但它是在其中一個呼叫端改起點，沒有處理跨 sector 的情況，其他呼叫端也沒修，判為替代修法。

**結論**
- 真實 bug 的數量太少，只能看趨勢：OpenHands（4 筆等價）略多於 Proposed（3 筆），和主資料集「兩者沒有顯著差異」一致。
- 修好但不等價的有 5 筆：2 筆只修了回歸測試涵蓋的驅動（不完整），1 筆只擋下測試用的特定輸入值，2 筆用了不同的寫法而且可能不正確。這說明通過回歸測試和真正修好之間的落差，在真實 bug 上比在合成注入上更明顯。
- 主資料集的替代修法只占修好的 2～8%；真實 bug 上，OpenHands 有 2/6、Proposed 有 2/5 不是等價修法。

## 10. 檢索消融（第 3 步，2026-10-27 補）

- 三種設定都是 Proposed、gemini-2.5-flash、同一份評測程式（core、agents、graph_rag、evaluate.py 在 5a44080 之後沒有改動）：
  - 完整版：`eval_runs/v3_25flash_r3/`
  - 關閉 Graph RAG（`ZEPHYR_GRAPH_RAG=0`）：`eval_runs/v3_25flash_r3_nograph/`
  - 只保留 Graph RAG（`ZEPHYR_HYBRID_RAG=0`）：`eval_runs/v3_25flash_r3_graphonly/`
- 每組的 run_meta 都核對過開關和工作區乾淨；暫時性錯誤都移到 archive 後單筆重跑，沒有算進結果。
- 數字由 `r3_ablation_summary.py` 產生（`ablation_summary.json`）。

**修好筆數（評測判定）**

| | 完整版 | 關閉 Graph RAG | 只保留 Graph RAG |
|---|---|---|---|
| 主資料集（139） | **80** | **80** | 53 |
| 　runtime（63） | 34 | 35 | 18 |
| 　kconfig（19） | 14 | 12 | 7 |
| 　compound（19） | 8 | 6 | 0 |
| 　dts（19） | 15 | 16 | 15 |
| 　c_syntax（19） | 9 | 11 | 13 |
| 真實 bug（13） | 5 | 5 | 0 |

**配對檢定（exact McNemar，只有 A 修好 / 只有 B 修好）**

| | 主資料集 | 真實 bug |
|---|---|---|
| 完整版 vs 關閉 Graph RAG | 15 / 15，p=1 | 0 / 0（修好的 5 筆完全相同） |
| 完整版 vs 只保留 Graph RAG | 33 / 6，p=1.4e-5 | 5 / 0，p=0.063 |

**檢索命中（被注入檔或上游修改檔是否出現在檢索結果）**

| | 第一輪 hit@1 | 第一輪 hit@8 | 任一輪命中 | 命中時修好 | 沒命中時修好 |
|---|---|---|---|---|---|
| 主資料集 完整版 | 29.5% | 43.9% | 54.0% | 54/75 | 26/64 |
| 主資料集 關閉 Graph RAG | 28.8% | 45.3% | 55.4% | 52/77 | 28/62 |
| 主資料集 只保留 Graph RAG | — | — | 0% | — | 53/139 |
| 真實 bug 完整版 | 0% | 30.8% | 61.5% | 5/8 | 0/5 |
| 真實 bug 關閉 Graph RAG | 0% | 38.5% | 61.5% | 5/8 | 0/5 |

- 只保留 Graph RAG 時沒有任何檔案層級的檢索結果：Graph RAG 只回傳 Kconfig/DTS 符號的 YAML 說明，不回傳原始檔。
- Graph RAG 的使用率：完整版有 111/139 個案例至少一輪拿到 Graph RAG 內容（Analyzer 有給關鍵字）；kconfig、dts、compound 是 19/19，runtime 48/63，c_syntax 6/19。

**解讀**
- Proposed 的檢索效果幾乎都來自 Hybrid RAG。拿掉它，主資料集從 80 筆掉到 53 筆，真實 bug 從 5 筆掉到 0 筆。
- 在 Hybrid RAG 之上，Graph RAG 在 r3 這次執行沒有可量測的額外貢獻：主資料集 15/15 翻轉、真實 bug 完全相同。
- 單靠 Graph RAG，連它應該最擅長的設定類也不夠：compound 0 筆、kconfig 7 筆，因為模型還是要找到實際的 Kconfig 或原始檔才能改。dts 三種設定都是 15～16 筆，dts 的錯誤訊息本身就指出檔案，不太需要檢索。
- c_syntax 在沒有 Hybrid RAG 時反而最好（13 筆 vs 9 筆）。逐筆讀過 trajectory（2026-10-30 補）：
  - 三組是包含關係：完整版修好的 9 筆，關 Graph 也修好；關 Graph 修好的 11 筆，只留 Graph 也修好。沒有任何一筆 c_syntax 是「有檢索才修好」。
  - 多出來的 4 筆不是 Graph RAG 的貢獻。19 筆裡 15 筆的圖譜是空的（`No keywords provided for graph retrieval.`），其餘 4 筆只有無關的 Kconfig 符號，所以「只留 Graph RAG」在 c_syntax 上等於沒有檢索。B3 沒有檢索，c_syntax 也是 13 筆。
  - 差異來自 Hybrid RAG 的干擾：
    - adc_api：檢索回來的大檔（`Kconfig.zephyr` 等）讓 Patch 的上下文到 30 萬字上限，真正要改的 `tests/drivers/adc/adc_api/src/test_adc.c` 被截掉，完整版、關 Graph 各 5 輪都一樣。上下文是依路徑字母排序後才截斷，`tests/` 排在 `boards/`、`drivers/`、`include/`、`kernel/` 之後，最容易被截掉。
    - uuid：檢索回來的是 `ztest.h` 和其他測試檔，模型 5 輪都去改 `ztest_test.h`；完整版在 r3、rep1、rep2 三次都失敗。
    - timer_error_case：檢索結果和 uuid 相同，完整版 5 輪都改 `ztest_test.h`；關 Graph、只留 Graph 第 1 輪也改它，第 2 輪才改到 `main.c`。
    - kernel_queue：關 Graph 用同樣的檢索結果第 1 輪就修好，看起來是執行間的變動。
  - 對論文：錯誤訊息已經指到檔案時，Hybrid RAG 會擠掉或分散注意力。上下文排序（log 指到的檔案、target_app 優先）是可以改的設計問題，但依「不根據資料集案例調整」的原則，這次不改。
- 檢索命中和修好有關（命中時約 70% 修好，沒命中約 40%），但這是相關性，命中本身也和案例難度有關。

**限制**
- 都是單次執行；第 4 步的重複實驗先跳過，所以 15/15 這種翻轉量無法和執行間的變動分開。
- 消融組沒有做 patch 重建和人工判讀，表中都是評測判定的數字。


## 11. Agentless、OpenHands 的失敗分析（第 5 步，2026-10-27 補）

- 數字由 `r3_failure_modes.py` 產生（`failure_modes.json`），只看主資料集，B3、Proposed 一起列出來對照。分類依據是評測記錄的 `applied_files`、`restored_files`，OpenHands 另外用 `changed_files`。
- 每個失敗案例只歸一類：
  - **改到注入檔**：有改到被注入的檔案（compound 至少一個），但最後還是沒通過；
  - **只改受保護檔**：沒改注入檔，只改了測試、`prj.conf` 這類受保護檔，評測每輪都會還原，所以等於沒改；
  - **改其他檔**：沒改注入檔，改了其他非受保護的檔案；
  - **沒有改動**。

| | 失敗 | 改到注入檔 | 只改受保護檔 | 改其他檔 | 沒有改動 |
|---|---|---|---|---|---|
| B3 | 89 | 35 | 39 | 13 | 2 |
| Agentless | 80 | 27 | 23 | 26 | 4 |
| OpenHands | 64 | 32 | 14 | 17 | 1 |
| Proposed | 59 | 28 | 10 | 21 | 0 |

**Agentless**
- 失敗主要卡在定位。每個案例最多 5 個候選檔：
  - 修好的 59 筆，注入檔全部在定位結果裡；
  - 失敗的 80 筆，注入檔全部在裡面的只有 31 筆，部分在裡面 12 筆，完全不在 37 筆。
- 定位失敗的 49 筆裡，有 12 筆是 kconfig、12 筆是 runtime。kconfig 的錯誤訊息指向測試的 `prj.conf`，定位就停在那裡。它是受保護檔，改了會被還原，所以 kconfig 失敗的 15 筆有 8 筆只改了受保護檔。
- 定位成功、還是失敗的 31 筆，是 4 個候選都沒改對；有 18 筆出現重複候選被略過（`duplicates_skipped`），實際試過的候選不到 4 個。
- Agentless 每個候選都從頭開始，失敗的改動會被撤銷，第二個候選看不到第一個候選的測試結果。修好的 59 筆有 47 筆是第一個候選，第 2～4 個候選只多修好 12 筆。

**OpenHands**
- 結束狀態：
  - finished 31 筆，代理自己認為修好了；
  - max_steps 26 筆，用完 50 步；
  - stuck 7 筆，是 SDK 偵測到代理重複同樣動作而中止，不是 API 錯誤（7 筆的事件記錄都沒有 429/503，評測程式本來就把 stuck 當成正常結束）。
- 失敗案例中位數 37.5 步、7.5 分鐘；修好的是 8 步、2.7 分鐘。
- OpenHands 的實際花費（litellm）有 73% 花在失敗案例上：失敗 $6.40，修好 $2.39。
- 失敗案例有一半（32/64）改到了注入檔但沒改對，主要在 runtime（14）和 compound（11）。
  - compound 的典型情況是只改了 DTS 或只改了 Kconfig 其中一邊；
  - runtime 是改了注入檔，但改的位置或邏輯不對。
- kconfig 是 OpenHands 最弱的類別（8/19，Proposed 14/19）。失敗的 11 筆裡只有 1 筆改到注入的 Kconfig 檔：
  - 6 筆只改了測試的 `prj.conf`、`CMakeLists.txt`，評測會還原；
  - 4 筆改了其他檔，例如 board defconfig、別的驅動。
  - OpenHands 能自己跑 build，但它把「讓這個測試 build 得過」當成目標，往測試設定的方向修，沒有去找被改壞的依賴定義。
- 事件記錄裡沒看到 build 指令的有 26 筆（失敗 17、修好 9），這些案例它沒有自己 build 驗證就結束了。

**四條 pipeline 共同的失敗**
- 30 筆案例四條都沒修好：runtime 13、compound 6、kconfig 5、c_syntax 5、dts 1。
- c_syntax 有一個四條共同的失敗模式（2026-10-30 更正：原本寫成錯誤訊息本身指到錯的檔案，不準確）。測試檔少了右大括號或分號時，gcc 的第一個錯誤確實在 `ztest_test.h` / `ztest_assert.h`，但 gcc 也輸出了 `FAILED: ...main.c.obj` 和 `main.c:<行號>: note: in expansion of macro`。原始 log 19/19 筆都有被注入的檔案，其中 18 筆的 `FAILED:` 行直接指出它。`tools/log_filter.py` 把這些行濾掉了，過濾後有 8 筆完全看不到被注入的檔案，只剩 ztest 標頭。四條 pipeline 都去改這兩個 ztest 標頭，而不是改測試檔：
  - 失敗的 c_syntax 案例裡，改過 ztest 標頭的有 B3 6/6、Agentless 8/9、OpenHands 9/10、Proposed 9/10；
  - 四條都失敗的 5 筆 c_syntax 都是這種（刪右大括號 3 筆、刪分號 2 筆），其中 4 筆是過濾後看不到被注入檔案的那 8 筆。
  - 這 8 筆的結果：Agentless、OpenHands、Proposed 都是 0/8，B3 是 4/8。其餘 11 筆：B3 9、Agentless 10、OpenHands 9、Proposed 9。
  - 所以這個失敗模式主要來自我們的 LogFilter，不是編譯器輸出的問題，也不是某條 pipeline 特有的問題。第 10 節的逐筆檢查已確認 Hybrid RAG 會把模型帶去 ztest 標頭，但那是在 log 已經被濾掉線索之後發生的。
  - 數字由 `r3_csyntax_log_check.py` 產生（`r3_csyntax_log_check_stdout.txt`）。

**小結**
- Agentless 的主要瓶頸是一次性的定位：注入檔沒進候選清單，後面就不可能修好。
- OpenHands 的主要瓶頸是步數和方向：一半的失敗把 50 步用完或卡住；kconfig 類會往測試設定的方向修。
- Proposed 只改受保護檔的失敗最少（10 筆，B3 39 筆）。B3 和 Proposed 每輪都會收到受保護檔被還原的提醒（`restore_reminder`），所以差別不是來自這個提醒，比較可能來自 Proposed 的檢索把注入檔找了回來（第 10 節：任一輪命中 54%）。Agentless 的候選之間沒有這種回饋。

## 12. 2×2 比較（第 5 步，2026-10-27 補）

**設計**

| | 固定流程 | 自主 agent |
|---|---|---|
| 通用 | Agentless | OpenHands |
| 領域專用 | Proposed | （沒有這一格） |

- 「領域專用」指 Proposed 的 Zephyr 專用元件：Analyzer 規則、Graph RAG / Hybrid RAG、log 過濾、DevOps/QA 分工。
- 三條都用 gemini-2.5-flash、同一份資料集和評測程式。
- 只有三格，所以兩個方向各用一組乾淨的比較：
  - **領域知識**（流程都固定）：Proposed vs Agentless。
  - **自主性**（都是通用）：OpenHands vs Agentless。
  - Proposed vs OpenHands 兩個因素同時不同，只能當參考，不能歸因到單一因素。
- 數字來自 `analyze_v3.py --pipelines agentless openhands proposed --pairs proposed:agentless openhands:agentless proposed:openhands`（輸出在 `twobytwo/`），Holm 只在這 3 組之間校正，所以 p 值和第 2 節（9 組校正）不同。

**主資料集（139 筆）**

| 比較 | 評測判定：差異（95% CI） | 只有前者 / 只有後者 | Holm p | 只算還原或等價：差異（95% CI） | Holm p |
|---|---|---|---|---|---|
| 領域知識：Proposed vs Agentless | +15.1（+6.5～+24.5） | 33 / 12 | **0.0074** | +12.9（+4.3～+21.6） | **0.024** |
| 自主性：OpenHands vs Agentless | +11.5（+2.2～+20.9） | 30 / 14 | **0.045** | +10.1（+0.7～+18.7） | 0.077 |
| 參考：Proposed vs OpenHands | +3.6（−5.8～+12.9） | 24 / 19 | 0.54 | +2.9（−6.5～+12.2） | 0.64 |

- 修好筆數：Agentless 59、OpenHands 75、Proposed 80；只算還原或等價是 58、72、76。

**依錯誤類型拆開（評測判定，未校正 exact McNemar，探索性）**

| | n | Agentless | OpenHands | Proposed | 領域知識（P vs A） | 自主性（OH vs A） | 參考（P vs OH） |
|---|---|---|---|---|---|---|---|
| 設定類（kconfig + dts + compound） | 57 | 19 | 27 | 37 | 19 / 1，p=4e-5 | 15 / 7，p=0.13 | 17 / 7，p=0.064 |
| 程式碼類（runtime + c_syntax） | 82 | 40 | 48 | 43 | 14 / 11，p=0.69 | 15 / 7，p=0.13 | 7 / 12，p=0.36 |

- 各類別：kconfig A 4 / OH 8 / P 14、compound 2 / 5 / 8、dts 13 / 14 / 15、runtime 30 / 39 / 34、c_syntax 10 / 9 / 9。

**真實 bug（13 筆，見第 9 節）**
- 評測判定 Agentless 3、OpenHands 6、Proposed 5；只算等價 2、4、3。
- n 太小，三組都不顯著，方向和主資料集的「自主性有幫助」一致，但領域知識在真實 bug 上看不到優勢。

**成本（每修好一筆，見第 5 節）**
- Agentless $0.21、Proposed $0.23、OpenHands $0.43（定價計）。
- OpenHands 的實際花費（litellm）有 73% 花在失敗案例（第 11 節）。
- 每筆平均輸入 token：Agentless 3.3 萬、Proposed 10.7 萬、OpenHands 69.7 萬。

**解讀**
- **兩個因素在這次執行裡都有效，但作用在不同類型的錯誤上。**
  - 領域知識的效果幾乎都集中在設定類：19 比 1。在程式碼類，Proposed 和 Agentless 打平（14 比 11）。這和第 10 節一致：Hybrid RAG 找回 Kconfig/DTS 的定義檔，是 Proposed 在 kconfig、compound 領先的主因。
  - 自主性的效果比較平均：設定類和程式碼類都是 15 比 7。OpenHands 能自己 build、跑測試、反覆修改，runtime 是三條裡最好的（39 筆）。
- 兩個因素的總效果接近（+15.1 vs +11.5），所以 Proposed 和 OpenHands 打平：Proposed 在設定類多 10 筆，OpenHands 在程式碼類多 5 筆，互相抵銷。
- 「只算還原或等價」時，自主性的優勢掉到 Holm p=0.077，不再顯著。OpenHands 有 3 筆替代修法，集中在 Kconfig 相依被放寬。
- 成本面：拿到同樣的修復率，領域知識（Proposed）的成本和 Agentless 差不多，自主性（OpenHands）的成本約 2 倍。

**限制**
- 缺「領域專用 × 自主 agent」這一格（例如 OpenHands 加上 Zephyr 檢索工具），所以無法檢驗兩個因素是否可以疊加，也無法估計交互作用。
- 「領域專用」包含好幾個元件，第 10 節只拆開了檢索這一項。
- 都是單次執行（第 4 步先跳過）；分類別的檢定沒有校正，只能當探索性結果。
- Agentless 是本研究依照論文自行改寫到 C/Zephyr 的版本（N=4），不是原作者的實作；OpenHands 用官方 SDK 1.55.1，但工作描述、映像檔和 50 步 / 40 分鐘的預算是本研究設定的。

## 13. Proposed：還原 Analyzer 規則後和 r2 的差異（第 5 步，2026-10-27 補）

**r2（e86a76e）到 r3（93bff1b / ac9ccbc）之間 Proposed 的改動**
1. **Analyzer 規則**從 3 條還原成原始設計的 2 條（bfa17ce）。拿掉的是 r2 的規則 2：「undeclared / undefined reference 通常是 CONFIG 被關掉或 DTS 節點被移除，也要查圖譜」，附 fcb 範例。
2. **Hybrid RAG 每輪都跑**（5a44080）：
   - r2 沒有關鍵字時整個 Knowledge 節點會被跳過，連 Hybrid RAG 也不跑；
   - r3 沒有關鍵字時改用過濾後的 log 當查詢。
3. **Graph RAG 真的有圖**（014b4a9）：每筆案例從注入後的 workspace 現場建圖。r2 的全域圖譜快取不存在，圖譜檢索實際上是空的。
4. 其他：
   - 受保護檔被還原時的提醒（419820d）；
   - SEARCH 要對照目前的檔案內容（004f863）；
   - 違反規格時的判定訊息寫得更具體。
   - 這些 B3 也一樣有。
   - 失敗編譯單元提示（81fc115）在 r3 是關閉的（`proposed_failed_unit_hint=false`）。

- 數字由 `r3_vs_r2_proposed.py` 產生（`r3_vs_r2_proposed.json`）。「第一輪有關鍵字」看的是 Analyzer 第一輪的 `search_keywords`。「命中」指任一輪 Hybrid RAG 的結果包含注入檔。

**逐類別比較（主資料集）**

| 類別 | n | 修好 r2 / r3 / r3 關 Graph | 第一輪有關鍵字 r2 / r3 | 有關鍵字的輪數比例 r2 / r3 | 有檢索 r2 / r3 | 命中 r2 / r3 |
|---|---|---|---|---|---|---|
| runtime | 63 | 37 / 34 / 35 | 60 / 40 | 77% / 49% | 61 / 63 | 30 / 26 |
| c_syntax | 19 | 11 / 9 / 11 | 7 / 5 | 39% / 25% | 10 / 19 | 8 / 5 |
| kconfig | 19 | 11 / 14 / 12 | 19 / 19 | 98% / 100% | 19 / 19 | 14 / 15 |
| dts | 19 | 16 / 15 / 16 | 19 / 19 | 100% / 89% | 18 / 19 | 14 / 13 |
| compound | 19 | 3 / 8 / 6 | 19 / 19 | 95% / 96% | 19 / 19 | 19 / 16 |
| 合計 | 139 | **78 / 80 / 80** | 124 / 102 | 82% / 64% | 127 / 139 | 85 / 75 |

**配對比較（exact McNemar）**

| | 只有前者修好 / 只有後者修好 | p |
|---|---|---|
| r3 vs r2 | 17 / 15 | 0.86 |
| r3 關 Graph vs r2 | 20 / 18 | 0.87 |
| r3 vs r3 關 Graph | 15 / 15 | 1 |
| 參考：B3 r3 vs r2（只有第 4 項的小改動） | 8 / 7 | — |

**真實 bug**
- r2 修好 4 筆，r3 修好 5 筆。
- 都修好的 3 筆：fuel_gauge、stream_flash、video_buffer。
- 只有 r2 修好：usage_double_count。只有 r3 修好：stack_alloc、zms。

**解讀**
- **還原規則的直接效果，是 runtime 的關鍵字變少**：第一輪有關鍵字從 60/63 掉到 40/63，有關鍵字的輪數從 77% 掉到 49%。設定類（kconfig、dts、compound）幾乎每輪都有關鍵字，不受影響。被拿掉的規則 2 正是讓 Analyzer 對 runtime 和 undeclared 錯誤也去查圖譜的那一條。
- **Hybrid RAG 每輪都跑，補回了「有檢索」**（127 → 139），但檢索品質沒有補回來：
  - 命中從 85 筆掉到 75 筆，runtime 30 → 26、c_syntax 8 → 5。
  - 改用 log 當查詢時，log 裡大量的 START/PASS、測試名稱會蓋過真正的模組名稱（例如試跑的 json 案例，BM25 找回的是 bsim 的測試檔，不是 json 函式庫）。Analyzer 給的關鍵字是比較好的查詢。
  - r3 才修好的 17 筆裡，沒有一筆是 r2 完全沒檢索的案例，所以「保證每輪都檢索」這件事本身沒有直接救回案例。
- **總數幾乎不變**（78 → 80），但有 32 筆翻轉，p=0.86：
  - runtime 少 3 筆、c_syntax 少 2 筆，和關鍵字變少、命中下降的方向一致；
  - kconfig 多 3 筆、compound 多 5 筆。
- **compound、kconfig 的進步不是 Graph RAG 帶來的**：關閉 Graph RAG 的 r3 也是 compound 6、kconfig 12，都比 r2 高；第 10 節 full vs 關 Graph 是 15/15。這兩類的關鍵字和檢索在 r2、r3 幾乎相同，所以比較可能來自第 4 項的小改動（還原提醒、SEARCH 對照目前內容），或只是執行間的變動。
- **翻轉量和單純的執行間變動差不多**：
  - B3 在 r2、r3 之間只有第 4 項的小改動，就有 15 筆翻轉（B3 修好 50 筆，Proposed 修好約 80 筆）；
  - r3 和 r3 關 Graph 只差 Graph RAG，也有 30 筆翻轉。
  - 所以 r2 → r3 的 32 筆翻轉，大部分無法歸因到任何一項改動。

**對論文的意義**
- 還原 Analyzer 規則是為了不根據資料集案例調整 Proposed。這樣做的代價是 runtime 的檢索品質略降（命中 30 → 26），但總修復率沒有下降（78 → 80，不顯著）。
- 所以 r2 的規則 2（列舉注入方式、附 fcb 範例）沒有讓 r2 的整體結果變好，也就沒有「靠資料集知識拉高分數」的疑慮；r3 的數字可以直接當作原始設計的結果。
- 如果之後要改進 Proposed，可以先做 runtime 的查詢：例如沒有關鍵字時，從 log 的路徑和函式名稱抽出模組名稱來查，而不是用整段 log。依「不根據資料集案例調整」的原則，這要在新的開發集上做，不在這 139 筆上調。

**限制**
- r2、r3 各只跑一次，四項改動同時發生，第 4 項沒有單獨的 ablation。
- 命中只看 Hybrid RAG 的檔案層級結果，不看 Graph RAG 的 YAML 內容是否有用。

## 14. 執行間變動（第 4 步，2026-10-30 補）

- 子集：`dataset/v3/eval/repeat_subset_40.json`，從主資料集依類別分層抽 40 筆（runtime 18、c_syntax 6、kconfig 6、compound 5、dts 5），種子 20261027。
- 三次：r3（主實驗中這 40 筆的結果）、rep1、rep2（`eval_runs/v3_25flash_r3_rep{1,2}/`）。四條 pipeline、模型、Docker 映像、評測程式都相同；r3 之後只改了分析工具和啟動腳本（加 `STAGE=repeat`）。
- 暫時性 API 錯誤都移到各自的 `archive/repeat_transient_*` 後單筆重跑；rep2 B3 isotp_depends 因筆電沒電睡眠而重跑（`archive/repeat_laptop_sleep_20261029/`，見 rep2 的 NOTES.md）。
- 數字由 `r3_repeat_variance.py` 產生，輸出在 `repeat_variance_stdout.txt`、`repeat_variance.json`。都是評測判定，沒有人工判讀。

| | r3 | rep1 | rep2 | 平均 | SD | 3 次都修好 | 2 次 | 1 次 | 都沒修好 | 會翻轉的案例 |
|---|---|---|---|---|---|---|---|---|---|---|
| Proposed | 26 | 21 | 22 | 23.0 | 2.6 | 16 | 6 | 9 | 9 | 15 |
| OpenHands | 23 | 26 | 25 | 24.7 | 1.5 | 19 | 7 | 3 | 11 | 10 |
| Agentless | 19 | 18 | 18 | 18.3 | 0.6 | 12 | 7 | 5 | 16 | 12 |
| B3 | 18 | 12 | 12 | 14.0 | 3.5 | 11 | 2 | 5 | 22 | 7 |

**成對比較**（x : y = 只有前者 / 只有後者修好；合併 = 三次的平均差，以案例為單位 bootstrap 95% CI；未校正）

| 比較 | r3 | rep1 | rep2 | 合併差異（百分點） |
|---|---|---|---|---|
| Proposed vs OpenHands | 7 : 4 | 8 : 13 | 4 : 7 | −4.2（−17.5～+10.0） |
| Proposed vs Agentless | 11 : 4 | 10 : 7 | 9 : 5 | +11.7（−2.5～+25.8） |
| Proposed vs B3 | 12 : 4 | 11 : 2 | 10 : 0 | +22.5（+10.0～+35.0） |
| OpenHands vs Agentless | 10 : 6 | 11 : 3 | 11 : 4 | +15.8（+1.7～+30.0） |
| Agentless vs B3 | 8 : 7 | 10 : 4 | 9 : 3 | +10.8（−3.3～+25.0） |

**各類別**（r3 / rep1 / rep2）

| 類別 | n | Proposed | OpenHands | Agentless | B3 |
|---|---|---|---|---|---|
| runtime | 18 | 12/7/11 | 14/15/15 | 10/8/9 | 7/4/5 |
| c_syntax | 6 | 3/3/2 | 2/3/2 | 3/3/3 | 4/2/2 |
| kconfig | 6 | 5/4/4 | 3/3/3 | 0/2/2 | 2/1/1 |
| dts | 5 | 4/5/4 | 3/4/4 | 5/4/4 | 5/5/4 |
| compound | 5 | 2/2/1 | 1/1/1 | 1/1/0 | 0/0/0 |

**解讀**
- **Proposed 和 OpenHands 打平的結論穩定，但誰領先會換。** r3 是 Proposed 多（7:4），rep1、rep2 都是 OpenHands 多（8:13、4:7）；合併差異 −4.2 個百分點，CI 跨過 0。所以主實驗的「Proposed 57.6% vs OpenHands 54.0%」不能解讀成 Proposed 領先。
- **Proposed 的 r3 在這 40 筆上偏高。** 子集上 r3 是 26/40（65%），rep1、rep2 是 21、22；三次平均 57.5%，剛好等於全集 r3 的 57.6%。OpenHands 正好相反，三次平均（61.7%）比 r3（57.5%）高。
- **Proposed 勝過 Agentless、B3 的方向三次都一致**（Agentless 11:4、10:7、9:5；B3 12:4、11:2、10:0）。40 筆的檢定力不夠，Proposed vs Agentless 的合併 CI 碰到 0，但方向和主實驗（33:12，Holm p=0.012）一致。
- **Proposed 的總數變動主要來自 runtime。** Proposed 的 runtime 是 12/7/11，15 筆會翻轉的案例裡 7 筆在 runtime（runtime 佔子集 18/40）。OpenHands 的 runtime 也有 5 筆翻轉，但方向互相抵銷，總數很穩定（14/15/15）。Agentless 最穩定（SD 0.6），可能因為它的定位和第一個候選都是 temperature 0。
- **類別層級的結論大致穩定**：Proposed 在 kconfig、compound 三次都最好或並列最好，OpenHands 在 runtime 三次都最好，和第 3、12 節的解讀一致。
- **單次執行的翻轉量**：每條 pipeline 有 7～15 筆（18～38%）的案例在三次之間結果不同。所以第 10 節消融的 15/15 翻轉、第 13 節 r2 → r3 的 32 筆翻轉，都在執行間變動的範圍內。

**還沒確認的觀察**
- Proposed 和 B3 都是 r3 高、rep1/rep2 低（B3 18 → 12、12），OpenHands 和 Agentless 沒有這個現象。r3 是 10 月 20 日前後跑的，rep1、rep2 是 10 月 28～30 日。Proposed 和 B3 都走 langchain-google-genai，OpenHands 走 LiteLLM，可能是 API 端模型行為隨時間改變，也可能只是巧合（n=40）。沒有檢查，論文裡只能當作限制提到。

**限制**
- 只有 40 筆、三次；r3 不是和 rep1、rep2 同時跑的。
- 都是評測判定；人工判讀只做了 r3。


## 15. 五類等權平均與 runtime 敏感度分析（2026-10-30 補）

- 回應助教的疑慮：主資料集 runtime 占 63/139（45%），整體修復率可能偏袒 runtime 較強的 pipeline。
- 數字由 `r3_macro_sensitivity.py` 產生（`macro_sensitivity_stdout.txt`、`macro_sensitivity.json`），種子 20261030。只重新分析既有結果，沒有新的執行。
- B1、B2 沿用 r2。「只算還原或等價」用第 8 節的人工判讀，判讀者一人，還沒複核。
- 檢定方式：
  - 整體：exact McNemar，Holm 校正 9 組（與第 2、8 節相同）。
  - 五類等權：類別內的配對差做 sign-flip 置換（20 萬次），Holm 校正 9 組；CI 是類別內案例 bootstrap（2 萬次）。
  - 敏感度：runtime 從 63 筆抽 19 筆，其他類全留，共 95 筆，抽 2 萬次；同一次抽樣用在所有 pipeline。另做一組依故障位置（test app 內 30 筆、外 33 筆）分層抽樣。

**修復率**

| | 整體（評測判定） | 五類等權（評測判定） | 整體（還原或等價） | 五類等權（還原或等價） |
|---|---|---|---|---|
| Proposed | 57.6% | **59.2%** | 54.7% | **56.5%** |
| OpenHands | 54.0% | 50.3% | 51.8% | 47.1% |
| Agentless | 42.4% | 40.1% | 41.7% | 39.0% |
| B3 | 36.0% | 38.7% | 33.1% | 35.2% |
| B2 | 31.7% | 33.8% | 30.2% | 32.4% |
| B1 | 0% | 0% | 0% | 0% |

**成對比較（評測判定）**

| 比較 | 整體差異 | Holm p | 五類等權差異（95% CI） | Holm p |
|---|---|---|---|---|
| Proposed vs OpenHands | +3.6 | 0.63 | +8.9（−1.6～+19.1） | 0.32 |
| Proposed vs Agentless | +15.1 | 0.012 | +19.2（+10.9～+27.4） | 0.00045 |
| Proposed vs B3 | +21.6 | 6.5e-5 | +20.6（+11.2～+30.1） | 0.0014 |
| Proposed vs B2 | +25.9 | 4.5e-8 | +25.4（+17.2～+33.8） | 4.5e-5 |
| Proposed vs B1 | +57.6 | 1.5e-23 | +59.2（+50.5～+67.8） | 4.5e-5 |
| OpenHands vs Agentless | +11.5 | 0.091 | +10.2（+0.2～+20.2） | 0.22 |
| OpenHands vs B3 | +18.0 | 0.0037 | +11.6（+1.9～+21.3） | 0.13 |
| Agentless vs B3 | +6.5 | 0.63 | +1.4（−7.3～+10.2） | 0.77 |
| B3 vs B2 | +4.3 | 0.63 | +4.8（−1.4～+11.3） | 0.38 |

- 五類等權的 Holm p 下限受置換次數影響，4.5e-5 是下限，不是精確值。
- 「只算還原或等價」的結論相同：Proposed vs OpenHands 五類等權 +9.4（−0.5～+19.2），Holm p=0.33；Proposed vs Agentless、B3、B2 都顯著。

**runtime 抽到 19 筆（n=95，評測判定）**

| 比較 | 中位數差異 | 95% 抽樣區間 | 前者領先的抽樣比例 | 未校正 p<.05 的比例 |
|---|---|---|---|---|
| Proposed vs B3 | +21.1 | +16.8～+24.2 | 100% | 100% |
| Proposed vs Agentless | +18.9 | +14.7～+24.2 | 100% | 99.9% |
| Proposed vs OpenHands | +9.5 | +5.3～+12.6 | 100% | 3.7% |
| OpenHands vs Agentless | +10.5 | +6.3～+14.7 | 100% | 16.6% |

- 依故障位置分層抽樣的結果幾乎相同（Proposed vs OpenHands 中位數 +8.4，區間和比例都一樣）。
- 「還原或等價」的版本中，Proposed vs OpenHands 中位數 +9.5，p<.05 的比例 6.9%。

**40 筆子集三次（五類等權，評測判定）**

| | r3 | rep1 | rep2 | 平均 | 整體平均 |
|---|---|---|---|---|---|
| Proposed | 64.0% | 59.1% | 52.2% | 58.4% | 57.5% |
| OpenHands | 48.2% | 56.7% | 53.3% | 52.7% | 61.7% |
| Agentless | 45.1% | 45.6% | 42.7% | 44.4% | 45.8% |
| B3 | 47.8% | 34.4% | 31.6% | 37.9% | 35.0% |

| 比較 | 三次平均差異（95% CI） | Holm p（5 組） | r3 / rep1 / rep2 差異 |
|---|---|---|---|
| Proposed vs OpenHands | +5.7（−8.8～+20.3） | 0.78 | +15.8 / +2.4 / −1.1 |
| Proposed vs Agentless | +14.0（+1.7～+26.7） | 0.29 | +18.9 / +13.6 / +9.6 |
| Proposed vs B3 | +20.5（+11.0～+30.8） | 0.0096 | +16.2 / +24.7 / +20.7 |
| OpenHands vs Agentless | +8.3（−4.1～+21.3） | 0.78 | +3.1 / +11.1 / +10.7 |
| Agentless vs B3 | +6.5（−5.0～+18.5） | 0.78 | −2.7 / +11.1 / +11.1 |

**解讀**
- **助教的疑慮有部分成立。** 權重確實會改變 Proposed 和 OpenHands 的差距：主實驗從 +3.6 變成 +8.9；40 筆三次平均從 −4.2 變成 +5.7，方向相反。OpenHands vs B3 在整體顯著（Holm p=0.0037），五類等權就不顯著（0.13）。
- **但打平的結論不變。** Proposed vs OpenHands 在整體、五類等權、兩種判定、三次平均下都不顯著；抽樣後只有 3.7～6.9% 的抽樣達到未校正 p<.05。所以不能因為換成五類等權就說 Proposed 領先。
- **五類等權下的領先也不穩定。** 40 筆子集上，差異依序是 +15.8、+2.4、−1.1。r3 正好是 Proposed 最高的一次（第 14 節）。
- **Proposed 勝過 Agentless、B3、B2 的結論不受權重影響**，主資料集在整體和五類等權都顯著。40 筆子集的 Proposed vs Agentless 校正後不顯著，但方向三次一致；檢定力較低。
- **差距來自哪裡：** Proposed 比 OpenHands 少修 5 筆 runtime（34 vs 39），其他四類多修 10 筆（kconfig +6、compound +3、dts +1、c_syntax 0）。

**限制**
- 抽樣分析不會得到新資訊：五類等權就是「抽到 19 筆」的期望值，抽樣只是顯示選到哪 19 筆的影響。「領先比例 100%」只表示 r3 這一次的結果換一組 runtime 案例仍然領先，不代表重跑也會領先；執行間的變動要看 40 筆三次。
- 五類等權時，runtime 每筆的權重是其他類的 1/3.3。其他類每類只有 19 筆，一筆的翻轉影響較大。
- 第 12 節的 2×2 使用另一組校正，兩邊的 Holm p 不能直接比。
- 這些結果都是 2.5 Flash，而且 LogFilter 有第 11 節的問題；Gemini 4 重跑後要用同一支腳本再算一次。

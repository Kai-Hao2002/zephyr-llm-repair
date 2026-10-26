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
  - 這可能和 Graph RAG 加回來有關，也可能只是單次執行的變動。要等檢索 ablation（第 3 步）和重複實驗（第 4 步）才能分辨。

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
- 等第 3、4 步跑完後，再分析 Graph RAG 和 Hybrid RAG 各自的貢獻，以及重複實驗的變異。

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

真實 bug 的重建在 `replay_real.json`：13 筆修好的案例中，有 15 筆 pipeline×案例和上游修正不同。這部分還沒有人工判讀。

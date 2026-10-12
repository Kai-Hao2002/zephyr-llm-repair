# v3 評測分析：gemini-2.5-flash（2026-10-12）

主資料集 `dataset/cases/final_dataset_v3.json`（139 筆），真實 bug 對照集 `dataset/cases/real_bugs_v3.json`（13 筆）。
四條 pipeline 都用 `--single-model`，fast 與 pro 都是 gemini-2.5-flash；embedding 用 gemini-embedding-2。
結果在 `eval_runs/v3_25flash/{b1,b2,b3,proposed}/` 與 `real/`，分析輸出在本目錄。

重現方式：

```
python tools/replay_patches.py --runs eval_runs/v3_25flash --dataset dataset/cases/final_dataset_v3.json --out-dir eval_runs/v3_25flash/analysis --tag main
python tools/replay_patches.py --runs eval_runs/v3_25flash/real --dataset dataset/cases/real_bugs_v3.json --out-dir eval_runs/v3_25flash/analysis --tag real
python tools/analyze_v3.py --runs eval_runs/v3_25flash --real-runs eval_runs/v3_25flash/real --out-dir eval_runs/v3_25flash/analysis \
    --labels eval_runs/v3_25flash/analysis/manual_labels_main.json --replay eval_runs/v3_25flash/analysis/replay_main.json
```

## 1. 資料完整性

- 四條 pipeline 各 139 筆、真實 bug 各 13 筆都有正式結果，沒有 `error` 狀態。
  暫時性錯誤（503、逾時、斷網）一律移到 `archive/`、單筆重跑：主資料集 94 筆次、真實 bug 3 筆次、試跑 1 筆次；其中 80 筆是 10/8～10/9 約 24 小時斷網（DNS 查不到）造成的。
- 所有結果的 run_meta 都是 gemini-2.5-flash、`code_dirty=false`。
  - 跑的過程中，評測程式改過兩次：
    - Proposed 的前 15 筆在 5eddf13 跑，embedding 走免費金鑰；之後 124 筆在 a2b389e 跑，改走付費 embedding 金鑰。embedding 模型和輸入完全相同。
    - 真實 bug 這一輪在 3699943 跑。這個 commit 修正了重現檢查：遇到 `real_bug` 類別時要等測試跑完。這只影響「開跑前確認 bug 有重現」那一步，不影響評分。
  - 其他 commit 之間只差結果檔或分析工具。
- 評測期間沒有任何一筆觸發完整性檢查 `test_integrity_violation`（只比對 assertion 和 skip 的數量）。

## 2. 主要結果（139 筆）

修復率分三種判定，括號是 Wilson 95% 信賴區間：

| pipeline | 原始判定 | 排除改測試語意（人工） | 嚴格規格檢查（離線估計） |
|---|---|---|---|
| B1 | 0/139 = 0.0%（0–2.7） | 0.0% | 0.0% |
| B2 | 50/139 = 36.0%（28.5–44.2） | 46 = 33.1% | 45 = 32.4% |
| B3 | 61/139 = 43.9%（35.9–52.2） | 52 = 37.4% | 47 = 33.8% |
| Proposed | 88/139 = 63.3%（55.0–70.9） | 81 = 58.3% | 72 = 51.8%（43.6–59.9） |

三種判定的定義：

- **原始判定**：評測當時的結果。
- **排除改測試語意**：第 4 節的人工判讀中，標為 `test_changed` 的修好案例改判為未修好。
- **嚴格規格檢查**：用 2026-10-12 新增的規則離線重算。規則是：注入檔若位在 target_app 底下，只有被注入的那幾行可以改。
  - 這是下界。實際評分時，agent 會收到「規格被改」的訊息，B3 和 Proposed 還可以重試。
  - 3.8-flash 那一輪會從一開始就用這個規則評分。

成對比較用精確 McNemar 檢定，6 組比較以 Holm 校正；差異的 95% 信賴區間用以案例為單位的成對 bootstrap（10,000 次）：

| 比較 | 原始判定 | 排除改測試語意 | 嚴格規格（離線） |
|---|---|---|---|
| Proposed vs B3 | +19.4%（+10.8～+27.3），33:6，p=2.9e-5 | +20.9%，36:7，p=1.8e-5 | +18.0%（+10.1～+25.9），31:6，p=8.3e-5 |
| Proposed vs B2 | +27.3%，40:2，p=1.2e-9 | +25.2%，37:2，p=8.5e-9 | +19.4%，31:4，p=1.0e-5 |
| B3 vs B2 | +7.9%（+1.4～+14.4），17:6，p=0.035 | +4.3%，12:6，p=0.24 | +1.4%（−4.3～+7.2），10:8，p=0.81 |
| B2、B3、Proposed vs B1 | 全部 p < 1e-12 | 同左 | 同左 |

（「33:6」代表只有 Proposed 修好的有 33 筆、只有 B3 修好的有 6 筆。）

結論：

- **Proposed 勝過 B3、B2**：三種判定下都顯著，差距約 18～27 個百分點。
- **B3 勝過 B2**：只在原始判定下勉強顯著。B3 多修好的案例裡，有一部分是改了測試語意；扣掉之後就沒有差異。
  B3 有閉迴路重試，也比較常在重試中改寫測試的期望值（見第 4 節）。

## 3. 分組（只做描述，不做檢定）

**依類別（原始判定）**

| 類別 | n | B2 | B3 | Proposed |
|---|---|---|---|---|
| c_syntax | 19 | 9 | 15 | 12 |
| compound | 19 | 1 | 0 | 5 |
| dts | 19 | 11 | 13 | 15 |
| kconfig | 19 | 7 | 5 | 10 |
| runtime_crash | 63 | 22 | 28 | 46 |

- c_syntax 是唯一 B3 比 Proposed 多的類別：15 對 12，嚴格判定下是 14 對 10。
- compound（Kconfig 與 DTS 同時注入）最難，B2 和 B3 幾乎全滅，Proposed 修好 5 筆。

**依初始 log 是否寫出注入檔路徑**（嚴格規格判定；用子字串比對，共 48 筆含路徑）

| | n | B2 | B3 | Proposed |
|---|---|---|---|---|
| log 含注入檔路徑 | 48 | 25 | 27 | 28 |
| log 不含 | 91 | 20 | 20 | 44 |

log 已經指出檔案時，三者差不多。Proposed 的優勢幾乎都在 log 沒有指出檔案的案例，也就是需要自己定位的時候。

**runtime 案例依注入位置拆開**（左邊是原始判定，右邊是嚴格規格判定）

| | n | B2 | B3 | Proposed |
|---|---|---|---|---|
| 注入在測試程式（target_app 底下） | 30 | 18 → 14 | 25 → 13 | 27 → 13 |
| 注入在核心程式（kernel/lib/drivers…） | 33 | 4 → 4 | 3 → 3 | 19 → 19 |

- 注入在測試檔的那 30 筆，原始判定裡的領先大多來自改測試，套用嚴格規格後三者打平。
- 注入在核心程式的那 33 筆，B2 和 B3 幾乎修不好，Proposed 修好 19 筆。這是 Proposed 最主要的優勢來源。

**依板子**：native_sim 有 97 筆，Proposed 修好 54 筆（56%）；QEMU 有 42 筆，修好 34 筆（81%）。
不過 QEMU 案例集中在 runtime 類（42 筆中 28 筆），所以板子的效應和類別的效應分不開。

**依 operator**（n 很小，只列明顯的）
- `runtime_off_by_one`（23 筆）：B2 4、B3 3、Proposed 14。
- `runtime_remove_null_check`（10 筆）：B2 0、B3 0、Proposed 5。
- `dts_break_phandle`：三者都 9/9。
- `c_typo_identifier`：三者都 4/4。
- `dts_remove_compatible`：B2 1、B3 2、Proposed 4（共 8 筆）。

## 4. 修好的 patch 到底改了什麼（重建與人工判讀）

`tools/replay_patches.py` 對每一筆修好的案例，從上游原始檔開始依序重新套用注入和每一輪的 patch，再和原始檔比對。
- 每一輪都核對重建時套用的檔案和實際紀錄一致。
- 只有 1 筆無法重建：B3 的 modem_ubx。原因是 trajectory 存 patch 的上限是 20,000 字元，全部 998 個 patch 中有 13 個被截斷。

| | B2 | B3 | Proposed |
|---|---|---|---|
| 完全還原原始內容 | 35 | 39 | 52 |
| 內容不同 → 等價修法 | 10 | 11 | 22 |
| 內容不同 → 替代修法 | 1 | 2 | 7 |
| 內容不同 → 改測試語意 | 4 | 9 | 7 |

- **等價修法**：例如 `K_TICKS(ticks)` 取代 `K_MSEC(...)`、加大緩衝區、只多註解、select 順序不同。
- **替代修法**：例如整行刪掉 `depends on` 造成過度放寬、在 `can.h` 包裝層處理 NULL、把邊界檢查移到標頭檔的包裝函式。
- **改測試語意**：例如保留對調的優先權，同時改 assertion 的期望順序；保留換錯的 API，改變測試情境；把期望值從 `&testing_user_data` 改成 `NULL`。這類修改幾乎都出現在注入於測試檔的 `thread_priority_swap` 和 `c_api_substitute` 案例。
- 每一筆的判讀和理由都在 `manual_labels_main.json`，diff 在 `replay_main_diffs.md`。

**檢查設計的教訓**
- 原本的完整性檢查只數 assertion 和 skip 的數量，20 筆改測試語意的案例一筆都沒擋到。
- 新的嚴格規格檢查（commit 2650936）離線套用的結果：
  - 擋下其中 19 筆，漏掉的 1 筆就是無法重建的 modem_ubx。實際評分時會直接讀 workspace 的檔案，不會漏。
  - 另外會擋下 15 筆修法無害、但在注入行以外動了測試或規格的案例，例如改訊息拼字、多加一個 `k_yield`、調換屬性順序。這是刻意的嚴格設計。

**修補位置**
- Proposed 有 1 筆完全沒動到注入檔（can_loopback，改的是 `can.h`）。
- 其他修好的案例，修改都涵蓋全部注入檔。這裡的判斷方式是：以修好那一輪為止、所有生效修改的聯集來看。

**人工抽查 20 筆**（`manual_review_sample.md`，固定亂數種子）
- 其中 3 筆是改測試語意：Proposed 的 sys_mutex、B3 的 modem_ubx、B2 的 smp_client。
- 這次抽查就是發現上述問題的起點。

## 5. 檢索（RQ2）

以「第一次檢索結果的前 8 名」是否包含注入檔來計算：

| | n | hit@1 | hit@3 | hit@8 | MRR | 任一輪命中 |
|---|---|---|---|---|---|---|
| B2（只用 BM25，查詢是原始 log） | 139 | 6.5% | 12.9% | 19.4% | 0.10 | 19.4% |
| Proposed（Hybrid，查詢是 Analyzer 產生的關鍵字） | 124 | 28.2% | 46.0% | 58.1% | 0.38 | 63.7% |

- Proposed 有 15 筆沒有做檢索。這是設計上的行為：Analyzer 沒產生搜尋關鍵字時直接進入 Patch，這 15 筆幾乎都是 c_syntax。
- 命中與否和修好的關係：
  - Proposed：命中時修好 50/72（69%），未命中時修好 28/52（54%）。
  - B2：命中時修好 19/27（70%），未命中時修好 31/112（28%）。

## 6. 失敗分類（原始判定；未修好的案例）

各類別失敗原因的詳細分析在 `FAILURES.md`。

分類規則見 `tools/failure_taxonomy.py`：

| | B1 | B2 | B3 | Proposed |
|---|---|---|---|---|
| patch 完全沒套用成功（apply_failed） | 46 | 27 | 0 | 0 |
| 只改到受保護的規格檔，被評測端還原（spec_reverted） | 42 | 32 | 49 | 17 |
| 改錯檔案（wrong_file） | 15 | 10 | 7 | 10 |
| 改對檔案但編譯失敗（right_file_build） | 36 | 15 | 17 | 20 |
| 改對檔案、編譯成功但測試仍失敗（right_file_runtime） | 0 | 5 | 5 | 4 |

- B3 失敗的主因是一直去改受保護的測試或設定，被還原後再改，重複到用完 5 輪。
- **patch 格式錯誤**（依 10/8 決定，解析器維持原樣，這裡只做統計）：
  - 至少出現 1 次格式錯誤的案例數：B1 46、B2 30、B3 34、Proposed 38。
  - 其中「同一個檔名底下寫多個 SEARCH 區塊」的：B3 2、Proposed 1。
  - 用 `--- 路徑` 這種 diff 格式開頭的：B2 5、B3 15、Proposed 8。

## 7. 成本（每筆平均）

| | 生成呼叫 | 輸入 token | 輸出 token | 迭代數 | TTR（秒） | 修好案例的平均迭代數 |
|---|---|---|---|---|---|---|
| B1 | 1.0 | 438 | 10,351 | 1.0 | 172 | – |
| B2 | 1.0 | 36,138 | 7,066 | 1.0 | 162 | 1.0 |
| B3 | 3.3 | 39,454 | 23,864 | 3.3 | 339 | 1.2 |
| Proposed | 6.6 | 73,690 | 22,062 | 2.9 | 402 | 1.6 |

- 生成呼叫總數：B1 139、B2 139、B3 462、Proposed 919。
- Proposed 另外每次檢索要 embedding 31 筆內容（30 個候選檔加上查詢）。

## 8. 真實 bug 對照集（13 筆）

| | B1 | B2 | B3 | Proposed |
|---|---|---|---|---|
| 修好 | 0 | 0 | 0 | 6 |

- Proposed 修好的 6 筆：dynamic_stack、fuel_gauge、stack_alloc、stream_flash、video_buffer、zms。
- 和上游修正比對：
  - **等價（3 筆）**：stack_alloc、stream_flash、video_buffer。
  - **只修測試需要的部分（1 筆）**：fuel_gauge 的驅動程式完全還原，上游另外兩個檔案沒修。
  - **針對測試的窄修法（1 筆）**：dynamic_stack 只拒絕 `SIZE_MAX`，上游是一般性的溢位檢查。
  - **不同的修法（1 筆）**：zms 改了搜尋起點，正確性無法單憑測試判斷。
- 排名方向和主資料集一致：Proposed 第一，B1 墊底。
  - B2 和 B3 在這裡都是 0，所以主資料集的「B3 略高於 B2」在這裡無法比較。
  - B3 的 13 筆中有 12 筆失敗在「只改到受保護的規格檔」。
- 樣本只有 13 筆，只看趨勢，不做顯著性宣稱。
- 檢索命中率（hit@8）：B2 15%（2/13）、Proposed 38%（5/13）；Proposed 任一輪命中 62%。

## 9. 限制與注意事項

- **改測試語意的判讀**：73 筆「內容不同」的案例由一個人判讀，沒有第二位標註者，「等價」和「輕微改測試」之間有灰色地帶，例如 iterable_sections 的宣告順序。
- **嚴格規格的數字是下界**：那是離線估計，實際評分時 agent 有機會重試。
- **B2 的檢索**：只用 BM25、查詢是原始 log，所以 B2 和 Proposed 的檢索差異，同時包含「查詢來源」和「檢索方法」兩個因素。
- **單一模型、單次執行**：模型只有 2.5-flash，每筆只跑一次，重複實驗排在 3.8-flash 那一輪（第 4 步）。
- **prompt 提示**：`agents/analyzer.py:58` 的提示（「CONFIG 被關掉或 DTS 節點被移除」）和注入 operator 對應。拿掉這段提示的 ablation 排在第 4 步。
- **資料集本身的限制**：見 `dataset/v3/README.md`。其中 30 筆 runtime 注入在測試檔，正好是改測試語意集中的地方。

## 10. 對下一輪（3.8-flash）的影響

- 嚴格規格檢查已經生效（2650936），給 agent 的訊息也不再寫「去修受測程式而不是測試」。
- trajectory 不再截斷 patch（dec1bea），之後的 patch 重建不會再因為截斷而失敗。log 等其他欄位仍維持 20,000 字元的上限。

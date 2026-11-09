# Gemini 4 結果整理，以及和 Gemini 2.5 Flash 的比較（2026-11-08）

**資料來源（都已提交）：**

| 內容 | 檔案 |
|---|---|
| 139 筆主實驗 | `v4_compare_oh_stdout.txt`、`v4_baselines_stdout.txt` |
| 真實 bug | `v4_real_compare_stdout.txt` |
| 重複實驗 | `v4_repeat_variance_stdout.txt` |
| 2.5 Flash 優化版 Proposed | `eval_runs/v4_25flash_opt/analysis/opt_vs_r3_stdout.txt` |
| 2.5 Flash r3 | `eval_runs/v3_25flash_r3/analysis/main_summary_stdout.txt` |
| 評分決定 | `decisions.md` |

**實驗設定：**

- 模型 `gemini-4-argon`，所有 pipeline 都用 `--single-model`。修補呼叫逾時 900 秒。
- Proposed = 優化版（`ZEPHYR_PROPOSED_OPT=1`）。
- B1、B2、B3 採 `2026-11-hybrid` 定義：
  - B2：Hybrid RAG，單次產生 patch。
  - B3：每一輪都做 Hybrid RAG，再加重試迴圈，`ZEPHYR_B3_GATE=1`。
  - B1：定義不變。
- OpenHands 使用統一時間戳後的 workspace（94e5e22）。
- 費用欄位按牌價 $2/$10 per 1M token 計算，沒有扣掉快取折扣。

## 1. Gemini 4，139 筆主實驗

| pipeline | 整體 | 五類等權 | runtime (63) | c_syntax (19) | kconfig (19) | dts (19) | compound (19) | 輸入 Mtok | 輸出 Mtok | 牌價 $ |
|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 7.2% | 7.6 | 4 | 0 | 6 | 0 | 0 | 0.12 | 0.51 | 5.3 |
| B2 | 56.8% | 59.6 | 32 | 19 | 12 | 14 | 2 | 4.98 | 1.75 | 27.5 |
| B3 | 85.6% | 86.3 | 53 | 19 | 15 | 19 | 13 | 14.62 | 6.25 | 91.7 |
| Agentless | 78.4% | 72.8 | 57 | 19 | 15 | 15 | 3 | 4.75 | 1.99 | 29.3 |
| **Proposed** | **94.2%** | **94.5** | 59 | 19 | 18 | 17 | 18 | 8.67 | 6.11 | 78.4 |
| OpenHands | 97.8% | 98.3 | 61 | 19 | 19 | 18 | 19 | 66.52 | 0.68 | 139.8 |


類別人數已從 `final_dataset_v3.json` 確認：runtime_crash 63，其他四類各 19。

### 成對比較

方法：exact McNemar 加 Holm 校正；五類等權差異用 bootstrap 算 95% CI。

| 比較 | 只有 a 修好 : 只有 b 修好 | 整體差異 | Holm p | 五類等權差異 [95% CI] |
|---|---|---|---|---|
| B2 vs B1 | 73:4 | +49.6 | 5.7e-17 | +52.0 [+43.9, +59.9] |
| B3 vs B2 | 40:0 | +28.8 | 3.6e-12 | +26.7 [+19.6, +33.9] |
| **Proposed vs B3**（multi-agent 分工的效果） | 16:4 | +8.6 | **0.012** | +8.2 [+2.2, +14.4] |
| Proposed vs Agentless | 26:4 | +15.8 | 1.2e-4 | +21.7 [+15.2, +28.3] |
| Proposed vs OpenHands | 2:7 | −3.6 | 0.18（不顯著） | −3.8 [−7.9, −0.4] |
| OpenHands vs Agentless | 29:2 | +19.4 | 1.4e-6 | +25.5 [+18.8, +32.3] |

**runtime 抽成 19 筆後的敏感度分析（20,000 次抽樣）：**

- B3→Proposed：每次抽樣都是 Proposed 領先，但只有 46.2% 的抽樣原始 p<.05，是逐步比較裡最弱的一步。
- Proposed vs OpenHands：每次抽樣都是 OpenHands 領先，但只有 3.9% 的抽樣 p<.05。
- 其他比較：100% 的抽樣都顯著。

**patch 套用失敗**（依失敗的 iteration 計算）：

| pipeline | 失敗次數 | 涉及案例 |
|---|---|---|
| Proposed | 29 | 15 |
| B3 | 47 | 22 |
| Agentless | 14 | 11 |
| OpenHands | 0 | 0 |

OpenHands 直接編輯檔案，所以不會有套用失敗。另外，Proposed 的 8 筆失敗全部都撞到單次輸出 65,536 token 的上限。

## 2. 真實 bug（13 筆）

| pipeline | Gemini 4 | 2.5 Flash | 只有 G4 修好 : 只有 2.5 修好 | p（Holm 6 組） |
|---|---|---|---|---|
| Proposed | 9 | 3（優化版）／ 5（r3 舊版） | 6:0（對優化版） | 0.031（0.19） |
| Agentless | 9 | 3 | 6:0 | 0.031（0.19） |
| OpenHands | 12 | 6（舊 workspace） | 6:0 | 0.031（0.19） |

- **Gemini 4 內部比較：** Proposed vs Agentless 2:2；Proposed vs OpenHands 1:4；OpenHands vs Agentless 4:1。都不顯著。
- **怎麼寫：** 只有 13 筆，只能當描述性結果寫，不宣稱顯著。
- **失敗類型：** 多數是 patch 格式錯誤或對不上原始碼，常伴隨單次輸出撞到上限。

## 3. 重複實驗（40 筆分層子集 × 3 次，含主實驗那次）

| pipeline | 主實驗 / rep1 / rep2 | SD | 3/3 修好的案例 | 結果會變動的案例 |
|---|---|---|---|---|
| OpenHands | 40 / 40 / 40 | 0.00 | 40 | 0 |
| Proposed | 39 / 38 / 39 | 0.58 | 38 | 2 |
| Agentless | 35 / 36 / 36 | 0.58 | 35 | 1 |
| B3 | 34 / 35 / 33 | 1.00 | 32 | 4 |

**三次合併後的差異（bootstrap 95% CI）：**

| 比較 | 差異 [95% CI] |
|---|---|
| Proposed vs B3 | +11.7 [+2.5, +21.7] |
| OpenHands vs Agentless | +10.8 [+2.5, +20.8] |
| Proposed vs OpenHands | −3.3 [−8.3, 0] |
| Proposed vs Agentless | +7.5 [−2.5, +19.2] |
| Agentless vs B3 | +4.2 [−6.7, +15.8] |

每一組比較在三次執行中的方向都一致。

**解讀時要注意：**

- 這份子集比全體容易，三次平均：Agentless 89.2% vs 全體 78.4%，Proposed 96.7% vs 94.2%。
- 子集是 10/27 為 2.5 Flash 抽的，抽樣早於 Gemini 4 的結果，不是事後挑選。

## 4. 和 Gemini 2.5 Flash 的比較：哪些能直接比

| 項目 | 能否直接比 | 原因與數字 |
|---|---|---|
| **Proposed**（139） | ✅ 用 2.5 Flash **優化版** `v4_25flash_opt`（同為 OPT=1） | G4 131 vs 2.5 84（60.4%），48:1，Holm 8e-13，五類 +32.6 [+25.1, +40.1]。r3 舊版（OPT=0）是 80/139。 |
| **Agentless**（139） | ✅ 設定相同 | G4 109 vs 2.5 59（42.4%），52:2，Holm 8e-13，五類 +32.8 [+24.8, +41.1] |
| **B1**（139） | ✅ 定義沒變 | G4 10 vs 2.5 0，10:0，exact p≈0.002 |
| OpenHands（139） | ⚠️ 有限制 | G4 136 vs 2.5 75（54.0%），62:1。但 2.5 Flash 用的是**舊 workspace**（時間戳捷徑還在），兩邊差的不只是模型。可以報，要加註腳，或標成上限估計。 |
| B2、B3 | ❌ 不能比 | 2.5 Flash 的 B2/B3 是舊定義（31.7%、36.0%），Gemini 4 是 2026-11-hybrid |
| 真實 bug | ✅ Proposed（對優化版）、Agentless；⚠️ OpenHands 同上 | 各 6:0，Holm 0.19 |
| 重複實驗的穩定度 | ✅ Proposed、OpenHands、Agentless；❌ B3 | 2.5 Flash 的 SD 介於 0.58 和 3.46 之間，結果會變動的案例有 7–15 筆；Gemini 4 的 SD 不超過 1，會變動的不超過 4 筆。 |

### 兩個模型的整體排序

- **2.5 Flash（r3）：** Proposed 57.6% ≈ OpenHands 54.0% > Agentless 42.4% > B3 36.0% > B2 31.7% > B1 0%。Proposed vs OpenHands 不顯著，p=0.54。
- **Gemini 4：** OpenHands 97.8% ≈ Proposed 94.2% > B3 85.6% > Agentless 78.4% > B2 56.8% > B1 7.2%。

**可以寫進論文的結論：**

- 換成較強的模型後，所有 pipeline 都大幅進步：Proposed、Agentless +33~36 點，OpenHands +44 點。
- Proposed 和 OpenHands 在兩個模型下都沒有顯著差異。
- Proposed 相對 Agentless 的優勢在兩個模型下都存在，主要來自 compound 類：G4 15:0。
- 較弱的模型下，compound 幾乎修不好：2.5 Flash 的 Proposed 只修好 8/19。換成 Gemini 4 後，multi-agent 與迴圈式 pipeline 能修好 compound（Proposed 18、OpenHands 19、B3 13），但 Agentless 仍只有 3，B2 只有 2。

## 5. 費用（牌價，未扣快取）

| 批次 | 牌價 |
|---|---|
| 139 筆主實驗：Proposed | $78 |
| 139 筆主實驗：OpenHands | $140 |
| 139 筆主實驗：B3 | $92 |
| 139 筆主實驗：Agentless | $29 |
| 139 筆主實驗：B2 | $28 |
| 139 筆主實驗：B1 | $5 |
| 真實 bug（三條 pipeline 合計） | 約 $81 |
| 重複實驗 | 約 $125 |

watcher 估算的本月累計約 $640。OpenHands 的實際快取命中率約 88%，所以實付會比牌價低。

## 6. 寫論文時要註明的限制

1. **OpenHands 的跨模型比較：** 2.5 Flash 的 OpenHands 用舊 workspace，有時間戳捷徑。
2. **B2/B3 的定義在 2026-11 改過：** 2.5 Flash 的 B2/B3 不能拿來和 Gemini 4 比。
3. **B1 的 gpio_kbd_matrix：** 兩次都無法解析，依使用者決定算未修好（`decisions.md`）。
4. **天花板效應：** Gemini 4 下多數 pipeline 接近上限，Proposed vs OpenHands 的差距（2:7）不顯著，也不宜過度解讀。

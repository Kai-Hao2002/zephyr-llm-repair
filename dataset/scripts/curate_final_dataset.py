# dataset/scripts/curate_final_dataset.py
"""
從 verified_zephyr_bugs.json 這個從未刪減過的完整驗證池，策展出
final_dataset.json 這個供實際評估使用的最終資料集。差異純粹是移除
「幾乎完全重複的內容」，不刪除任何真正獨立的 bug——完整理由見
README.md「最終資料集 final_dataset.json」一節。

重新執行這支腳本前，請先確認 EXCLUDE_IDS 是否還符合最新的池子內容
（例如新一輪的 baseline-commit-diversity 或 board-portability 案例
會產生新的「同一 mutation 出現兩次」配對，可能需要決定新的排除規則）。

Curates final_dataset.json — the dataset actually used for evaluation — from
verified_zephyr_bugs.json, the never-pruned full verification pool. The only
difference is removing near-total content duplication, not deleting any
genuinely independent bug. Full reasoning in README.md's "final_dataset.json"
section.

Before re-running this script, re-check whether EXCLUDE_IDS still matches the
current pool (e.g. a future round of baseline-commit-diversity or
board-portability cases will create new "same mutation appears twice" pairs
that may need their own exclusion decision).
"""
import json
import os
from collections import Counter

DATASET_PATH = os.path.join(os.path.dirname(__file__), "..", "cases", "verified_zephyr_bugs.json")
FINAL_PATH = os.path.join(os.path.dirname(__file__), "..", "cases", "final_dataset.json")

# Session 46 part 45 (2026-08-27) 稽核決定的排除清單：
# Session 46 part 45 (2026-08-27) audit-driven exclusion list:
EXCLUDE_IDS = {
    # thread_priority_swap_semaphore 的 4 板驗證砍到 2 板 (native_sim + riscv32)，
    # 移除 cortex_a53/xtensa 這兩份逐字相同的 mutation。
    # thread_priority_swap_semaphore's 4-board proof capped to 2
    # (native_sim + riscv32); drop the cortex_a53/xtensa byte-identical pair.
    'inject_thread_priority_swap_semaphore_cortex_a53',
    'inject_thread_priority_swap_semaphore_xtensa',
    # kconfig 的 "*_EMUL" depends-invert 模板集中度從 10 砍到 6 個實例
    # (保留 DAC_EMUL x2 commits + 這次新挖的 GPIO/GNSS/BIOMETRICS)，
    # 移除獨立 kconfig 版本的 RTC/ESPI/DMA/ADC_EMUL——它們在 compound
    # 分類裡搭配 DTS mutation 的版本是不同案例，不受影響。
    # kconfig's "*_EMUL" depends-invert template cut from 10 to 6 instances
    # (keep DAC_EMUL x2 baseline commits + this session's newest GPIO/GNSS/
    # BIOMETRICS families); drop the standalone-kconfig RTC/ESPI/DMA/ADC_EMUL
    # entries -- their compound-category kconfig+dts siblings are untouched,
    # different cases.
    'inject_kconfig_rtc_emul_depends',
    'inject_kconfig_espi_emul_depends',
    'inject_kconfig_dma_emul_depends',
    'inject_kconfig_adc_emul_depends',
}


def main():
    with open(DATASET_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # 論文方法論決定：Zephyr-Eval 以「純注入」作為唯一建構方式（見 thesis
    # proposal「Mined versus Injected Faults」一節），挖礦案例（id 前綴
    # "bug_"）因此不列入最終資料集——這是方法論範圍決定，不是這些案例本身
    # 品質有問題；它們仍完整保留在 verified_zephyr_bugs.json 這個完整驗證池
    # 裡，供未來如果決定要做額外的 real-world generalization check 使用。
    # Thesis methodology decision: Zephyr-Eval uses injection as the sole
    # construction method (see the proposal's "Mined versus Injected Faults"
    # section), so mined cases (id prefix "bug_") are excluded from the final
    # dataset -- a scope decision, not a quality judgment on those cases.
    # They remain fully intact in verified_zephyr_bugs.json, the full
    # verification pool, in case a future real-world generalization check is
    # ever wanted.
    final = [c for c in data if c["id"] not in EXCLUDE_IDS and not c["id"].startswith("bug_")]

    print(f"Base pool: {len(data)}, excluded: {len(EXCLUDE_IDS)}, kept: {len(final)}")
    print("Final category distribution:", dict(Counter(c["category"] for c in final)))
    mined = [c for c in final if c["id"].startswith("bug_")]
    print(f"Mined: {len(mined)}/{len(final)} = {len(mined) / len(final) * 100:.1f}%")

    with open(FINAL_PATH, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=4, ensure_ascii=False)
        f.write("\n")
    print(f"Written to {FINAL_PATH}")


if __name__ == "__main__":
    main()

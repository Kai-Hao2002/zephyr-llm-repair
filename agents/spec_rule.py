# agents/spec_rule.py
"""
所有產生 patch 的 prompt (B1/B2/B3/Proposed 的 Patch Expert) 共用的「測試規格」說明 (2026-10-13)。
評測端會還原 target_app 底下的受保護檔案、拒絕在注入行以外修改測試/規格 (core/protected_files.py)，
之前 agent 完全不知道這條規則，常反覆修改會被還原的檔案 (2.5-flash：B3 186/225、Proposed 93/131 次)。
每個案例都是同一段文字，不含任何案例資訊，不會透露哪個檔案被注入。

The "test specification" note shared by every patch-generating prompt (B1/B2/B3 and Proposed's Patch
Expert), added 2026-10-13. The evaluator restores protected files under target_app and rejects edits to
tests/spec outside the injected lines (core/protected_files.py); agents were never told, and kept editing
files that get reverted (2.5-flash: B3 186/225, Proposed 93/131 times). The text is identical for every
case and carries no case information, so it does not reveal which file was injected.
"""

SPEC_RULE = (
    "[Test specification rule]\n"
    "The test application (its test sources, prj.conf, board overlays and CMakeLists.txt) defines the expected "
    "behaviour. Changing what the tests check, their expected values, their scenario or the test configuration "
    "in order to make them pass does not count as a fix: such edits are reverted or rejected by the evaluator. "
    "If the defect itself is located in one of those files, fix only that defect."
)

# rep2 notes

- b3 / v3_kconfig_isotp_depends_577e42a: the laptop ran out of battery and slept
  from 2026-10-29 16:26 to 20:13 during the first verification build. The run then
  resumed and resolved at iteration 1 (same outcome as r3 and rep1).
  Decision (user, 2026-10-29): keep final_status=resolved; exclude this case's
  ttr_seconds (13764 s, inflated by ~3.8 h of sleep) from any TTR analysis and note
  it in the report.

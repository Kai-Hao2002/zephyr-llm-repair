# rep2 notes

- b3 / v3_kconfig_isotp_depends_577e42a: the laptop ran out of battery and slept
  from 2026-10-29 16:26 to 20:13 during the first verification build. The run then
  resumed and resolved at iteration 1 (same outcome as r3 and rep1).
  Decision (user, 2026-10-29, revised): rerun the case. The slept run (resolved,
  ttr_seconds 13764, inflated by ~3.8 h of sleep) was moved to
  archive/repeat_laptop_sleep_20261029/b3/ and is not counted; the launcher reruns
  the missing case after the current b3 pass.

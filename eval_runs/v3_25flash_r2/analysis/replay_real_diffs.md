## [b2] v3real_zms_find_ate_offset_bf805bf (real_bug) -> differs
- subsys/kvss/zms/zms.c: differs
```diff
--- a/subsys/kvss/zms/zms.c (original)
+++ b/subsys/kvss/zms/zms.c (final)
@@ -1005,16 +1005,4 @@
 
 	wlk_addr = start_addr;
-	if (wlk_addr == fs->ate_wra) {
-		/* fs->ate_wra points to the next write slot, not to a valid ATE. */
-		rc = zms_compute_prev_addr(fs, &wlk_addr);
-		if (rc) {
-			return rc;
-		}
-
-		if (wlk_addr == end_addr) {
-			*ate_addr = end_addr;
-			return 0;
-		}
-	}
 
 	do {
@@ -1024,4 +1012,9 @@
 			return rc;
 		}
+		/* The ATE at fs->ate_wra is always considered empty/invalid. */
+		if (wlk_prev_addr == fs->ate_wra) {
+			continue;
+		}
+
 		if (wlk_ate.id == id) {
 			/* read the ate cycle only when we change the sector or if it is
```

## [proposed] v3real_fuel_gauge_cycle_count_8110e73 (real_bug) -> partial
- drivers/fuel_gauge/bq27z746/bq27z746.c: exact_restore
- drivers/fuel_gauge/adp5360/fuel_gauge_adp5360.c: untouched
- include/zephyr/drivers/fuel_gauge.h: untouched

## [proposed] v3real_stream_flash_size_overflow_07f1481 (real_bug) -> differs
- subsys/storage/stream/stream_flash.c: differs
```diff
--- a/subsys/storage/stream/stream_flash.c (original)
+++ b/subsys/storage/stream/stream_flash.c (final)
@@ -393,6 +393,7 @@
 	}
 
-	if ((offset + size) < offset) {
-		LOG_ERR("Requested range overflows SIZE_MAX");
+	/* Check for potential overflow of offset + size */
+	if (offset > SIZE_MAX - size) {
+		LOG_ERR("Requested range (offset + size) overflows size_t");
 		return -EFAULT;
 	}
```

## [proposed] v3real_usage_double_count_fc7a893 (real_bug) -> differs
- kernel/usage.c: differs
```diff
--- a/kernel/usage.c (original)
+++ b/kernel/usage.c (final)
@@ -79,9 +79,10 @@
 	key = k_spin_lock(&usage_lock);
 
-	_current_cpu->usage0 = usage_now();   /* Always update */
-
 	if (thread->base.usage.track_usage) {
+		_current_cpu->usage0 = usage_now();
 		thread->base.usage.num_windows++;
 		thread->base.usage.current = 0;
+	} else {
+		_current_cpu->usage0 = 0;
 	}
 
@@ -132,20 +133,24 @@
 	if (cpu == _current_cpu) {
 		uint32_t  now = usage_now();
-		uint32_t cycles = now - cpu->usage0;
-
-		/*
-		 * Getting stats for the current CPU. Update both its
-		 * current thread stats and the CPU stats as the CPU's
-		 * [usage0] field will also get updated. This keeps all
-		 * that information up-to-date.
-		 */
-
-		if (cpu->current->base.usage.track_usage) {
-			sched_thread_update_usage(cpu->current, cycles);
+		uint32_t u0 = cpu->usage0;
+
+		if (u0 != 0) {
+			uint32_t cycles = now - u0;
+
+			/*
+			 * Getting stats for the current CPU. Update both its
+			 * current thread stats and the CPU stats as the CPU's
+			 * [usage0] field will also get updated. This keeps all
+			 * that information up-to-date.
+			 */
+
+			if (cpu->current->base.usage.track_usage) {
+				sched_thread_update_usage(cpu->current, cycles);
+			}
+
+			sched_cpu_update_usage(cpu, cycles);
+
+			cpu->usage0 = now;
 		}
-
-		sched_cpu_update_usage(cpu, cycles);
-
-		cpu->usage0 = now;
 	} else if (cpu->usage->track_usage) {
 		/*
@@ -200,20 +205,24 @@
 	if (thread == cpu->current) {
 		uint32_t now = usage_now();
-		uint32_t cycles = now - cpu->usage0;
-
-		/*
-		 * Getting stats for the current thread. Update both the
-		 * current thread stats and its CPU stats as the CPU's
-		 * [usage0] field will also get updated. This keeps all
-		 * that information up-to-date.
-		 */
-
-		if (thread->base.usage.track_usage) {
-			sched_thread_update_usage(thread, cycles);
+		uint32_t u0 = cpu->usage0;
+
+		if (u0 != 0) {
+			uint32_t cycles = now - u0;
+
+			/*
+			 * Getting stats for the current thread. Update both the
+			 * current thread stats and its CPU stats as the CPU's
+			 * [usage0] field will also get updated. This keeps all
+			 * that information up-to-date.
+			 */
+
+			if (thread->base.usage.track_usage) {
+				sched_thread_update_usage(thread, cycles);
+			}
+
+			sched_cpu_update_usage(cpu, cycles);
+
+			cpu->usage0 = now;
 		}
-
-		sched_cpu_update_usage(cpu, cycles);
-
-		cpu->usage0 = now;
 	}
 
@@ -252,5 +261,4 @@
 
 	key = k_spin_lock(&usage_lock);
-	struct _cpu *cpu = _current_cpu;
 
 	if (!thread->base.usage.track_usage) {
@@ -258,13 +266,4 @@
 		thread->base.usage.num_windows++;
 		thread->base.usage.current = 0;
-
-		if (thread == cpu->current) {
-			uint32_t now = usage_now();
-			uint32_t cycles = now - cpu->usage0;
-
-			sched_cpu_update_usage(cpu, cycles);
-
-			cpu->usage0 = now;
-		}
 	}
 
@@ -289,11 +288,15 @@
 
 		if (thread == cpu->current) {
-			uint32_t now = usage_now();
-			uint32_t cycles = now - cpu->usage0;
+			uint32_t cycles = usage_now() - cpu->usage0;
 
 			sched_thread_update_usage(thread, cycles);
 			sched_cpu_update_usage(cpu, cycles);
-
-			cpu->usage0 = now;
+			/*
+			 * Update the CPU's usage0 to reflect that cycles up to
+			 * this point have been accounted for. This prevents
+			 * double-counting if z_sched_usage_stop() is called
+			 * later for the same execution window.
+			 */
+			cpu->usage0 = 0;
 		}
 	}
```

## [proposed] v3real_video_buffer_null_6d5bf31 (real_bug) -> differs
- subsys/video/buffer.c: differs
```diff
--- a/subsys/video/buffer.c (original)
+++ b/subsys/video/buffer.c (final)
@@ -92,5 +92,5 @@
 {
 	if (vbuf == NULL) {
-		LOG_ERR("NULL buffer");
+		LOG_ERR("Attempted to release a NULL buffer");
 		return -EINVAL;
 	}
```


## [proposed] v3real_dynamic_stack_overflow_6468c07 (real_bug) -> differs
- kernel/dynamic.c: differs
```diff
--- a/kernel/dynamic.c (original)
+++ b/kernel/dynamic.c (final)
@@ -59,6 +59,4 @@
 static k_thread_stack_t *z_thread_stack_alloc_dyn(size_t size, int flags)
 {
-	size_t stack_len;
-
 	if ((flags & K_USER) == K_USER) {
 #ifdef CONFIG_DYNAMIC_OBJECTS
@@ -72,10 +70,5 @@
 	}
 
-	stack_len = K_KERNEL_STACK_LEN(size);
-	if (stack_len < size) {
-		return NULL;
-	}
-
-	return z_thread_aligned_alloc(Z_KERNEL_STACK_OBJ_ALIGN, stack_len);
+	return z_thread_aligned_alloc(Z_KERNEL_STACK_OBJ_ALIGN, K_KERNEL_STACK_LEN(size));
 }
 
@@ -83,4 +76,9 @@
 {
 	k_thread_stack_t *stack = NULL;
+
+	/* Reject impossible stack sizes */
+	if (size == SIZE_MAX) {
+		return NULL;
+	}
 
 	if (IS_ENABLED(CONFIG_DYNAMIC_THREAD_PREFER_ALLOC)) {
```

## [proposed] v3real_fuel_gauge_cycle_count_8110e73 (real_bug) -> partial
- drivers/fuel_gauge/bq27z746/bq27z746.c: exact_restore
- drivers/fuel_gauge/adp5360/fuel_gauge_adp5360.c: untouched
- include/zephyr/drivers/fuel_gauge.h: untouched

## [proposed] v3real_stack_alloc_overflow_374352f (real_bug) -> differs
- kernel/stack.c: differs
```diff
--- a/kernel/stack.c (original)
+++ b/kernel/stack.c (final)
@@ -46,23 +46,24 @@
 {
 	void *buffer;
+	int32_t ret;
 	size_t total_size;
-	int32_t ret;
 
 	SYS_PORT_TRACING_OBJ_FUNC_ENTER(k_stack, alloc_init, stack);
 
-	/* Reject allocation sizes that cannot be represented. */
 	if (size_mul_overflow(num_entries, sizeof(stack_data_t), &total_size)) {
 		ret = -ENOMEM;
+		goto out;
+	}
+
+	buffer = z_thread_malloc(total_size);
+	if (buffer != NULL) {
+		k_stack_init(stack, buffer, num_entries);
+		stack->flags = K_STACK_FLAG_ALLOC;
+		ret = 0;
 	} else {
-		buffer = z_thread_malloc(total_size);
-		if (buffer != NULL) {
-			k_stack_init(stack, buffer, num_entries);
-			stack->flags = K_STACK_FLAG_ALLOC;
-			ret = 0;
-		} else {
-			ret = -ENOMEM;
-		}
-	}
-
+		ret = -ENOMEM;
+	}
+
+out:
 	SYS_PORT_TRACING_OBJ_FUNC_EXIT(k_stack, alloc_init, stack, ret);
 
```

## [proposed] v3real_stream_flash_size_overflow_07f1481 (real_bug) -> differs
- subsys/storage/stream/stream_flash.c: differs
```diff
--- a/subsys/storage/stream/stream_flash.c (original)
+++ b/subsys/storage/stream/stream_flash.c (final)
@@ -393,6 +393,7 @@
 	}
 
-	if ((offset + size) < offset) {
-		LOG_ERR("Requested range overflows SIZE_MAX");
+	/* Check for offset + size overflow */
+	if (offset > SIZE_MAX - size) {
+		LOG_ERR("Offset + size overflows size_t");
 		return -EFAULT;
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
+		LOG_ERR("Cannot release a NULL buffer");
 		return -EINVAL;
 	}
```

## [proposed] v3real_zms_find_ate_offset_bf805bf (real_bug) -> differs
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
@@ -1688,5 +1676,6 @@
 	}
 #else
-	wlk_addr = fs->ate_wra;
+	/* Start search from the first committed ATE. */
+	wlk_addr = fs->ate_wra + fs->ate_size;
 #endif /* CONFIG_ZMS_LOOKUP_CACHE */
 	uint64_t rd_addr = wlk_addr;
@@ -1832,5 +1821,6 @@
 	}
 #else
-	wlk_addr = fs->ate_wra;
+	/* Start search from the first committed ATE. */
+	wlk_addr = fs->ate_wra + fs->ate_size;
 #endif
 
```


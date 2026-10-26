## [b3] v3real_video_buffer_null_6d5bf31 (real_bug) -> differs
- subsys/video/buffer.c: differs
```diff
--- a/subsys/video/buffer.c (original)
+++ b/subsys/video/buffer.c (final)
@@ -92,9 +92,8 @@
 {
 	if (vbuf == NULL) {
-		LOG_ERR("NULL buffer");
+		LOG_ERR("Attempted to release a NULL video buffer");
 		return -EINVAL;
 	}
-
-	if (vbuf->index >= ARRAY_SIZE(video_buf)) {
+	if (vbuf == NULL || vbuf->index >= ARRAY_SIZE(video_buf)) {
 		LOG_ERR("Invalid buffer index: %u", vbuf->index);
 		return -EINVAL;
```

## [agentless] v3real_http_chunked_frag_len_1ea30bf (real_bug) -> differs
- subsys/net/lib/http/http_client.c: differs
```diff
--- a/subsys/net/lib/http/http_client.c (original)
+++ b/subsys/net/lib/http/http_client.c (final)
@@ -307,11 +307,11 @@
 	}
 
-	/* Reset the body_frag_start pointer for each fragment. */
+	/* Set the body_frag_start pointer to the first fragment in this recv_buf chunk. */
 	if (!req->internal.response.body_frag_start) {
 		req->internal.response.body_frag_start = (uint8_t *)at;
 	}
 
-	/* Use the parser-decoded length. */
-	req->internal.response.body_frag_len = length;
+	/* Accumulate the length of all body fragments in this recv_buf chunk. */
+	req->internal.response.body_frag_len += length;
 
 	return 0;
```

## [agentless] v3real_stack_alloc_overflow_374352f (real_bug) -> differs
- kernel/stack.c: differs
```diff
--- a/kernel/stack.c (original)
+++ b/kernel/stack.c (final)
@@ -46,10 +46,9 @@
 {
 	void *buffer;
+	int32_t ret;
 	size_t total_size;
-	int32_t ret;
 
 	SYS_PORT_TRACING_OBJ_FUNC_ENTER(k_stack, alloc_init, stack);
 
-	/* Reject allocation sizes that cannot be represented. */
 	if (size_mul_overflow(num_entries, sizeof(stack_data_t), &total_size)) {
 		ret = -ENOMEM;
```

## [agentless] v3real_video_buffer_null_6d5bf31 (real_bug) -> differs
- subsys/video/buffer.c: differs
```diff
--- a/subsys/video/buffer.c (original)
+++ b/subsys/video/buffer.c (final)
@@ -92,5 +92,4 @@
 {
 	if (vbuf == NULL) {
-		LOG_ERR("NULL buffer");
 		return -EINVAL;
 	}
```

## [openhands] v3real_dynamic_stack_overflow_6468c07 (real_bug) -> differs
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
@@ -72,14 +70,12 @@
 	}
 
-	stack_len = K_KERNEL_STACK_LEN(size);
-	if (stack_len < size) {
-		return NULL;
-	}
-
-	return z_thread_aligned_alloc(Z_KERNEL_STACK_OBJ_ALIGN, stack_len);
+	return z_thread_aligned_alloc(Z_KERNEL_STACK_OBJ_ALIGN, K_KERNEL_STACK_LEN(size));
 }
 
 k_thread_stack_t *z_impl_k_thread_stack_alloc(size_t size, int flags)
 {
+	if (size == SIZE_MAX) {
+		return NULL;
+	}
 	k_thread_stack_t *stack = NULL;
 
```

## [openhands] v3real_fuel_gauge_cycle_count_8110e73 (real_bug) -> partial
- drivers/fuel_gauge/bq27z746/bq27z746.c: whitespace_only
```diff
--- a/drivers/fuel_gauge/bq27z746/bq27z746.c (original)
+++ b/drivers/fuel_gauge/bq27z746/bq27z746.c (final)
@@ -132,5 +132,5 @@
 	case FUEL_GAUGE_CYCLE_COUNT:
 		rc = bq27z746_read16(dev, BQ27Z746_CYCLECOUNT, &tmp_val);
-		val->cycle_count = tmp_val;
+					val->cycle_count = tmp_val;
 		break;
 	case FUEL_GAUGE_CURRENT_UA:
```
- drivers/fuel_gauge/adp5360/fuel_gauge_adp5360.c: untouched
- include/zephyr/drivers/fuel_gauge.h: untouched

## [openhands] v3real_http_chunked_frag_len_1ea30bf (real_bug) -> differs
- subsys/net/lib/http/http_client.c: differs
```diff
--- a/subsys/net/lib/http/http_client.c (original)
+++ b/subsys/net/lib/http/http_client.c (final)
@@ -312,5 +312,5 @@
 	}
 
-	/* Use the parser-decoded length. */
+	/* Calculate the length of the body contained in the recv_buf */
 	req->internal.response.body_frag_len = length;
 
```

## [openhands] v3real_stack_alloc_overflow_374352f (real_bug) -> differs
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

## [openhands] v3real_stream_flash_size_overflow_07f1481 (real_bug) -> differs
- subsys/storage/stream/stream_flash.c: differs
```diff
--- a/subsys/storage/stream/stream_flash.c (original)
+++ b/subsys/storage/stream/stream_flash.c (final)
@@ -392,9 +392,8 @@
 		return -EFAULT;
 	}
-
-	if ((offset + size) < offset) {
-		LOG_ERR("Requested range overflows SIZE_MAX");
-		return -EFAULT;
-	}
+		if (offset > SIZE_MAX - size) {
+			LOG_ERR("Requested range overflows size_t");
+			return -EFAULT;
+		}
 
 	ctx->fdev = fdev;
```

## [openhands] v3real_video_buffer_null_6d5bf31 (real_bug) -> differs
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
+	if (offset + size < offset) {
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
@@ -1832,5 +1820,6 @@
 	}
 #else
-	wlk_addr = fs->ate_wra;
+	/* Start search from the last written ATE, not the next available write address. */
+	wlk_addr = fs->ate_wra + fs->ate_size;
 #endif
 
```


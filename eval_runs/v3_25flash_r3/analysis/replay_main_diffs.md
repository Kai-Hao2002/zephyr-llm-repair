## [b3] v3_api_substitute_early_sleep_161f758 (runtime_crash) -> differs
- tests/kernel/early_sleep/src/main.c: differs
```diff
--- a/tests/kernel/early_sleep/src/main.c (original)
+++ b/tests/kernel/early_sleep/src/main.c (final)
@@ -41,5 +41,5 @@
 
 	start_time = k_cycle_get_32();
-	k_sleep(K_MSEC(k_ticks_to_ms_floor64(ticks)));
+	k_sleep(K_TICKS(ticks));
 	stop_time = k_cycle_get_32();
 
```

## [b3] v3_api_substitute_sleep_yield_54ee8ec_r (runtime_crash) -> differs
- tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c: differs
```diff
--- a/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (original)
+++ b/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (final)
@@ -132,5 +132,10 @@
 	spawn_threads(0);
 	/* checkpoint: all ready threads get executed when k_sleep */
-	k_sleep(K_MSEC(100));
+	/* The test description states k_sleep() should be used to allow all threads to run.
+	 * k_yield() only yields to higher or equal priority threads, which causes lower
+	 * priority threads to not execute. Using k_sleep() will put the current thread
+	 * into a waiting state, allowing all other ready threads to run.
+	 */
+	k_sleep(K_MSEC(1));
 	for (int i = 0; i < THREADS_NUM; i++) {
 		zassert_true(tdata[i].executed == 1);
```

## [b3] v3_c_api_substitute_modem_pipe_6182bc0 (runtime_crash) -> differs
- tests/subsys/modem/modem_pipe/src/main.c: differs
```diff
--- a/tests/subsys/modem/modem_pipe/src/main.c (original)
+++ b/tests/subsys/modem/modem_pipe/src/main.c (final)
@@ -276,5 +276,6 @@
 	zassert_equal(atomic_get(&test_state), 0, "Unexpected state %u",
 		      (uint32_t)atomic_get(&test_state));
-	k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT);
+	/* Wait for the asynchronous transmit idle notification to be processed. */
+	k_msleep(10);
 	zassert_equal(atomic_get(&test_state), BIT(TEST_MODEM_PIPE_EVENT_TRANSMIT_IDLE_BIT),
 		      "Unexpected state %u", (uint32_t)atomic_get(&test_state));
```

## [b3] v3_dts_fwcfg_offbyone_x86_134e694 (runtime_crash) -> differs
- tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay: differs
```diff
--- a/tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay (original)
+++ b/tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay (final)
@@ -7,5 +7,5 @@
 	fwcfg0: fw-cfg@510 {
 		compatible = "qemu,fw-cfg-ioport";
-		reg = <0x510 0x18>;
+		reg = <0x510 0x4>;
 		status = "okay";
 	};
```

## [b3] v3_dts_gpio_latch_reg_cellcount_4ef3792 (dts) -> differs
- tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay: differs
```diff
--- a/tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay (original)
+++ b/tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay (final)
@@ -2,5 +2,5 @@
 	test_latch: gpio@2000ff00 {
 		compatible = "gpio-mmio-latch";
-		reg = <0x2000ff00 0x4>;
+		reg = <0x2000ff00 0x4>; /* Address and size for a single 32-bit register */
 		gpio-controller;
 		#gpio-cells = <2>;
```

## [b3] v3_kconfig_zbus_depends_b0ed2b5 (kconfig) -> differs
- subsys/zbus/Kconfig: differs
```diff
--- a/subsys/zbus/Kconfig (original)
+++ b/subsys/zbus/Kconfig (final)
@@ -4,5 +4,4 @@
 menuconfig ZBUS
 	bool "ZBus support"
-	depends on MULTITHREADING
 	help
 	  Enables support for Zephyr message bus.
```

## [b3] v3_runtime_cbprintf_package_compare_buf_shrink_9be6ee7 (runtime_crash) -> differs
- tests/lib/cbprintf_package/src/main.c: differs
```diff
--- a/tests/lib/cbprintf_package/src/main.c (original)
+++ b/tests/lib/cbprintf_package/src/main.c (final)
@@ -37,5 +37,5 @@
 static char static_buf[512];
 static char runtime_buf[512];
-static char compare_buf[128];
+static char compare_buf[256];
 
 static void dump(const char *desc, uint8_t *package, size_t len)
```

## [b3] v3_runtime_enum_mgmt_buffer_shrink_bf226c6 (runtime_crash) -> differs
- tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c: differs
```diff
--- a/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (original)
+++ b/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (final)
@@ -66,5 +66,5 @@
 
 struct details_entries {
-	char expected_name[32];
+	char expected_name[16]; /* Increased size to accommodate "shell mgmt" + null terminator */
 	uint8_t expected_handlers;
 	bool expected_test;
```

## [b3] v3new_kconfig_adt7420_5058917 (kconfig) -> differs
- drivers/sensor/adi/adt7420/Kconfig: differs
```diff
--- a/drivers/sensor/adi/adt7420/Kconfig (original)
+++ b/drivers/sensor/adi/adt7420/Kconfig (final)
@@ -7,6 +7,6 @@
 	bool "ADT7420 Temperature Sensor"
 	default y
-	depends on DT_HAS_ADI_ADT7420_ENABLED || DT_HAS_ADI_ADT7410_ENABLED || \
-			DT_HAS_ADI_ADT7422_ENABLED
+	depends on DT_HAS_ADI_ADT7240_ENABLED || DT_HAS_ADI_ADT7410_ENABLED || \
+			DT_HAS_ADI_ADT7422_ENABLED || DT_HAS_ADI_ADT7420_ENABLED
 	select I2C
 	help
```

## [b3] v3new_kconfig_lc709203f_577e42a_fx (kconfig) -> differs
- drivers/fuel_gauge/lc709203f/Kconfig: differs
```diff
--- a/drivers/fuel_gauge/lc709203f/Kconfig (original)
+++ b/drivers/fuel_gauge/lc709203f/Kconfig (final)
@@ -1,3 +1,9 @@
-# Copyright (c) 2025 Philipp Steiner <philipp.steiner1987@gmail.com>
+config FUEL_GAUGE_LC709203F
+	bool "LC709203F Fuel Gauge driver"
+	default y
+	depends on I2C && DT_HAS_ONNN_LC709203F_ENABLED
+	select CRC
+	help
+	  Enable the LC709203F Fuel Gauge driver.# Copyright (c) 2025 Philipp Steiner <philipp.steiner1987@gmail.com>
 #
 # SPDX-License-Identifier: Apache-2.0
@@ -7,5 +13,4 @@
 	default y
 	depends on DT_HAS_ONNN_LC709203F_ENABLED
-	select CRC
 	select I2C
 	help
```

## [b3] v3new_kconfig_nvs_qemu_161f758 (kconfig) -> differs
- subsys/kvss/nvs/Kconfig: differs
```diff
--- a/subsys/kvss/nvs/Kconfig (original)
+++ b/subsys/kvss/nvs/Kconfig (final)
@@ -6,6 +6,13 @@
 config NVS
 	bool "Non-volatile Storage"
-	depends on FLASH
-	depends on FLASH_PAGE_LAYOUT
+	# NVS requires FLSH to be enabled. For boards using flash simulator,
+	# FLASH_SIMULATOR selects FLSH. However, if NVS is enabled before
+	# FLASH_SIMULATOR, or if FLASH_SIMULATOR is not enabled for some reason,
+	# NVS will fail to enable. To ensure FLSH is always enabled when NVS is,
+	# NVS will directly select FLSH for relevant architectures.
+	# For boards using a flash simulator, NVS depends on FLASH_SIMULATOR,
+	# which in turn selects FLSH. For other boards, NVS depends directly on FLSH.
+	depends on ((ARCH_POSIX || BOARD_QEMU_X86 || BOARD_MPS2_AN385) && FLASH_SIMULATOR) || FLSH
+	select FLASH_PAGE_LAYOUT
 	select CRC
 	help
```

## [agentless] v3_api_substitute_early_sleep_161f758 (runtime_crash) -> differs
- tests/kernel/early_sleep/src/main.c: differs
```diff
--- a/tests/kernel/early_sleep/src/main.c (original)
+++ b/tests/kernel/early_sleep/src/main.c (final)
@@ -41,5 +41,9 @@
 
 	start_time = k_cycle_get_32();
-	k_sleep(K_MSEC(k_ticks_to_ms_floor64(ticks)));
+	/* The test is named "early_sleep" and the function "ticks_to_sleep",
+	 * but it was using k_yield(). To actually test sleep functionality
+	 * and measure the duration of a sleep, k_sleep() should be used.
+	 */
+	k_sleep(K_TICKS(ticks));
 	stop_time = k_cycle_get_32();
 
```

## [agentless] v3_api_substitute_mutex_recursive_b30a5a9 (runtime_crash) -> differs
- tests/kernel/mutex/mutex_api/src/test_mutex_apis.c: differs
```diff
--- a/tests/kernel/mutex/mutex_api/src/test_mutex_apis.c (original)
+++ b/tests/kernel/mutex/mutex_api/src/test_mutex_apis.c (final)
@@ -298,5 +298,5 @@
 
 	/* Give thread_waiter a chance to get the mutex */
-	k_sleep(K_MSEC(1));
+	k_sleep(K_MSEC(10)); /* Allow waiter thread to run and acquire mutex */
 
 	/**TESTPOINT: waiter thread got the mutex */
```

## [agentless] v3_api_substitute_sleep_yield_54ee8ec_r (runtime_crash) -> differs
- tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c: differs
```diff
--- a/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (original)
+++ b/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (final)
@@ -132,5 +132,5 @@
 	spawn_threads(0);
 	/* checkpoint: all ready threads get executed when k_sleep */
-	k_sleep(K_MSEC(100));
+	k_sleep(K_MSEC(100)); /* Give other threads time to execute */
 	for (int i = 0; i < THREADS_NUM; i++) {
 		zassert_true(tdata[i].executed == 1);
```

## [agentless] v3_c_api_substitute_smp_client_3628e91 (runtime_crash) -> differs
- tests/subsys/mgmt/mcumgr/smp_client/src/main.c: differs
```diff
--- a/tests/subsys/mgmt/mcumgr/smp_client/src/main.c (original)
+++ b/tests/subsys/mgmt/mcumgr/smp_client/src/main.c (final)
@@ -68,5 +68,5 @@
 	rc = smp_client_send_cmd(&smp_client, nb, smp_client_res_cb, &testing_user_data, 2);
 	zassert_equal(MGMT_ERR_EOK, rc, "Expected to receive %d response %d", MGMT_ERR_EOK, rc);
-	k_sleep(K_SECONDS(3));
+	k_sleep(K_SECONDS(3)); /* Wait for the command to timeout */
 	zassert_is_null(res_buf, "NULL pointer was not returned");
 	zassert_equal_ptr(response_ptr, &testing_user_data, "User data not returned correctly");
```

## [agentless] v3_kconfig_zbus_depends_b0ed2b5 (kconfig) -> differs
- subsys/zbus/Kconfig: differs
```diff
--- a/subsys/zbus/Kconfig (original)
+++ b/subsys/zbus/Kconfig (final)
@@ -4,5 +4,4 @@
 menuconfig ZBUS
 	bool "ZBus support"
-	depends on MULTITHREADING
 	help
 	  Enables support for Zephyr message bus.
```

## [agentless] v3_runtime_flash_sim_offbyone_907f069 (runtime_crash) -> differs
- drivers/flash/flash_simulator.c: differs
```diff
--- a/drivers/flash/flash_simulator.c (original)
+++ b/drivers/flash/flash_simulator.c (final)
@@ -157,7 +157,21 @@
 {
 	const struct flash_simulator_config *cfg = dev->config;
-
-	if ((offset < 0 || offset >= cfg->flash_size ||
-	     (cfg->flash_size - offset) < len)) {
+	size_t flash_size = cfg->flash_size;
+
+	if (offset < 0) {
+		return 0; /* Negative offset is always invalid */
+	}
+
+	/* At this point, offset is non-negative. Cast to size_t for further checks. */
+	size_t u_offset = (size_t)offset;
+
+	/* Check if the range [u_offset, u_offset + len - 1] is within [0, flash_size - 1].
+	 * This is equivalent to checking if u_offset + len <= flash_size.
+	 * The check `u_offset > flash_size || len > flash_size - u_offset` correctly
+	 * identifies if `u_offset + len` would exceed `flash_size`, while also
+	 * guarding against `flash_size - u_offset` underflow if `u_offset` itself
+	 * is already greater than `flash_size`.
+	 */
+	if (u_offset > flash_size || len > flash_size - u_offset) {
 		return 0;
 	}
```

## [agentless] v3_runtime_rtio_workq_submit_null_req_f914b6d (runtime_crash) -> differs
- subsys/rtio/rtio_workq.c: differs
```diff
--- a/subsys/rtio/rtio_workq.c (original)
+++ b/subsys/rtio/rtio_workq.c (final)
@@ -35,5 +35,5 @@
 			  rtio_work_submit_t handler)
 {
-	if (!req) {
+	if (req == NULL) {
 		return;
 	}
```

## [agentless] v3_runtime_video_emul_rx_offbyone_93db4e5 (runtime_crash) -> differs
- drivers/video/video_emul_rx.c: differs
```diff
--- a/drivers/video/video_emul_rx.c (original)
+++ b/drivers/video/video_emul_rx.c (final)
@@ -139,5 +139,6 @@
 
 	if (vbuf->size < fmt->pitch * fmt->height) {
-		LOG_ERR("Buffer too small for a full frame");
+		LOG_ERR("Buffer too small for a full frame (needed %zu, got %zu)",
+			fmt->pitch * fmt->height, vbuf->size);
 		return -ENOMEM;
 	}
```

## [agentless] v3new_runtime_json_bcf31c2 (runtime_crash) -> differs
- lib/utils/json.c: differs
```diff
--- a/lib/utils/json.c (original)
+++ b/lib/utils/json.c (final)
@@ -538,5 +538,7 @@
 	}
 
-	/* Check if we had to truncate due to the original escaped string being too long */
+	/* Check if we had to truncate due to the original escaped string being too long.
+	 * If escaped_len is equal to size, it means there's no room for the null terminator.
+	 */
 	if (escaped_len >= size) {
 		return -EINVAL;
```

## [agentless] v3new_runtime_kheap_f914b6d (runtime_crash) -> differs
- kernel/kheap.c: differs
```diff
--- a/kernel/kheap.c (original)
+++ b/kernel/kheap.c (final)
@@ -138,7 +138,7 @@
 	if (!size_mul_overflow(num, size, &bounds)) {
 		ret = k_heap_alloc(heap, bounds, timeout);
-	}
-	if (ret != NULL) {
-		(void)memset(ret, 0, bounds);
+		if (ret != NULL) {
+			(void)memset(ret, 0, bounds);
+		}
 	}
 
```

## [agentless] v3new_runtime_min_heap_134e694 (runtime_crash) -> differs
- lib/min_heap/min_heap.c: differs
```diff
--- a/lib/min_heap/min_heap.c (original)
+++ b/lib/min_heap/min_heap.c (final)
@@ -101,5 +101,5 @@
 int min_heap_push(struct min_heap *heap, const void *item)
 {
-	if (heap->size >= heap->capacity) {
+	if (heap->size == heap->capacity) { /* Heap is full */
 		return -ENOMEM;
 	}
```

## [agentless] v3new_runtime_onoff_44853cf (runtime_crash) -> differs
- lib/utils/onoff.c: differs
```diff
--- a/lib/utils/onoff.c (original)
+++ b/lib/utils/onoff.c (final)
@@ -513,24 +513,24 @@
 	int rv = validate_args(mgr, cli);
 
-	if ((rv >= 0)
-	    && (mgr->transitions->reset == NULL)) {
-		rv = -ENOTSUP;
-	}
-
-	if (rv < 0) {
+	if (rv < 0) { /* validation failure */
 		return rv;
+	}
+
+	if (mgr->transitions->reset == NULL) { /* reset not supported */
+		return -ENOTSUP;
 	}
 
 	k_spinlock_key_t key = k_spin_lock(&mgr->lock);
 	uint32_t state = mgr->flags & ONOFF_STATE_MASK;
-
-	rv = state;
+	int ret_val; /* new variable for the return value of the operation */
 
 	if ((state & ONOFF_FLAG_ERROR) == 0) {
-		rv = -EALREADY;
+		ret_val = -EALREADY; /* not in an error state, cannot reset */
 	} else {
 		reset = (state != ONOFF_STATE_RESETTING);
 		sys_slist_append(&mgr->clients, &cli->node);
-	}
+		ret_val = state; /* return current state if proceeding */
+	}
+	rv = ret_val; /* update rv for the return statement */
 
 	if (reset) {
```

## [agentless] v3new_runtime_spsc_pbuf_907f069 (runtime_crash) -> differs
- lib/os/spsc_pbuf.c: differs
```diff
--- a/lib/os/spsc_pbuf.c (original)
+++ b/lib/os/spsc_pbuf.c (final)
@@ -238,6 +238,9 @@
 
 	outlen = spsc_pbuf_alloc(pb, len, &pbuf);
-	if (outlen != len) {
-		return outlen < 0 ? outlen : -ENOMEM;
+	if (outlen < 0) { /* Propagate actual error codes from spsc_pbuf_alloc */
+		return outlen;
+	}
+	if (outlen != len) { /* Not enough space (0 or partial allocation) */
+		return -ENOMEM;
 	}
 
```

## [openhands] v3_api_substitute_early_sleep_161f758 (runtime_crash) -> differs
- tests/kernel/early_sleep/src/main.c: differs
```diff
--- a/tests/kernel/early_sleep/src/main.c (original)
+++ b/tests/kernel/early_sleep/src/main.c (final)
@@ -41,5 +41,5 @@
 
 	start_time = k_cycle_get_32();
-	k_sleep(K_MSEC(k_ticks_to_ms_floor64(ticks)));
+	    k_sleep(K_TICKS(ticks));
 	stop_time = k_cycle_get_32();
 
```

## [openhands] v3_api_substitute_modem_ubx_thread_yield_9be6ee7 (runtime_crash) -> differs
- tests/subsys/modem/modem_ubx/src/main.c: differs
```diff
--- a/tests/subsys/modem/modem_ubx/src/main.c (original)
+++ b/tests/subsys/modem/modem_ubx/src/main.c (final)
@@ -153,5 +153,5 @@
 	 * yielding.
 	 */
-	k_sleep(K_MSEC(1));
+			k_sleep(K_MSEC(100));
 }
 
```

## [openhands] v3_api_substitute_mutex_recursive_b30a5a9 (runtime_crash) -> differs
- tests/kernel/mutex/mutex_api/src/test_mutex_apis.c: differs
```diff
--- a/tests/kernel/mutex/mutex_api/src/test_mutex_apis.c (original)
+++ b/tests/kernel/mutex/mutex_api/src/test_mutex_apis.c (final)
@@ -298,5 +298,5 @@
 
 	/* Give thread_waiter a chance to get the mutex */
-	k_sleep(K_MSEC(1));
+	k_msleep(100);
 
 	/**TESTPOINT: waiter thread got the mutex */
```

## [openhands] v3_runtime_cbprintf_package_compare_buf_shrink_9be6ee7 (runtime_crash) -> differs
- tests/lib/cbprintf_package/src/main.c: differs
```diff
--- a/tests/lib/cbprintf_package/src/main.c (original)
+++ b/tests/lib/cbprintf_package/src/main.c (final)
@@ -37,5 +37,5 @@
 static char static_buf[512];
 static char runtime_buf[512];
-static char compare_buf[128];
+static char compare_buf[256];
 
 static void dump(const char *desc, uint8_t *package, size_t len)
```

## [openhands] v3_runtime_enum_mgmt_buffer_shrink_bf226c6 (runtime_crash) -> differs
- tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c: differs
```diff
--- a/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (original)
+++ b/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (final)
@@ -66,5 +66,5 @@
 
 struct details_entries {
-	char expected_name[32];
+	char expected_name[16];
 	uint8_t expected_handlers;
 	bool expected_test;
```

## [openhands] v3_runtime_rtio_workq_submit_null_req_f914b6d (runtime_crash) -> differs
- subsys/rtio/rtio_workq.c: differs
```diff
--- a/subsys/rtio/rtio_workq.c (original)
+++ b/subsys/rtio/rtio_workq.c (final)
@@ -35,5 +35,9 @@
 			  rtio_work_submit_t handler)
 {
-	if (!req) {
+	if (req == NULL) {
+		return;
+	}
+
+	if (0) {
 		return;
 	}
```

## [openhands] v3_runtime_stack_offbyone_bcf31c2 (runtime_crash) -> differs
- kernel/stack.c: differs
```diff
--- a/kernel/stack.c (original)
+++ b/kernel/stack.c (final)
@@ -107,5 +107,5 @@
 	SYS_PORT_TRACING_OBJ_FUNC_ENTER(k_stack, push, stack);
 
-	CHECKIF(stack->next == stack->top) {
+			CHECKIF(stack->next >= stack->top) {
 		ret = -ENOMEM;
 		goto out;
```

## [openhands] v3new_compound_dma_emul_kconfig_dts_b0ed2b5 (compound) -> differs
- drivers/dma/Kconfig.emul: differs
```diff
--- a/drivers/dma/Kconfig.emul (original)
+++ b/drivers/dma/Kconfig.emul (final)
@@ -4,5 +4,5 @@
 config DMA_EMUL
 	bool "Emulated DMA driver [EXPERIMENTAL]"
-	depends on DT_HAS_ZEPHYR_DMA_EMUL_ENABLED
+
 	select EXPERIMENTAL
 	help
```
- tests/drivers/dma/chan_blen_transfer/boards/native_sim.overlay: whitespace_only
```diff
--- a/tests/drivers/dma/chan_blen_transfer/boards/native_sim.overlay (original)
+++ b/tests/drivers/dma/chan_blen_transfer/boards/native_sim.overlay (final)
@@ -11,2 +11,3 @@
 
 tst_dma0: &dma {};
+
```

## [openhands] v3new_compound_gpio_kbd_matrix_kconfig_dts_577e42a (compound) -> differs
- drivers/input/Kconfig.gpio_kbd_matrix: exact_restore
- tests/drivers/input/gpio_kbd_matrix/boards/native_sim.overlay: differs
```diff
--- a/tests/drivers/input/gpio_kbd_matrix/boards/native_sim.overlay (original)
+++ b/tests/drivers/input/gpio_kbd_matrix/boards/native_sim.overlay (final)
@@ -11,5 +11,7 @@
 / {
 	kbd_matrix_interrupt: kbd-matrix-interrupt {
-		compatible = "gpio-kbd-matrix";
+			compatible = "gpio-kbd-matrix";
+			status = "okay";
+			label = "kbd_matrix_interrupt";
 		row-gpios = <&gpio0 0 (GPIO_PULL_UP | GPIO_ACTIVE_LOW)>,
 			    <&gpio0 1 (GPIO_PULL_UP | GPIO_ACTIVE_LOW)>;
```

## [openhands] v3new_dts_hc_sr04_compatible_91db82a (dts) -> differs
- tests/drivers/sensor/hc-sr04/boards/native_sim.overlay: differs
```diff
--- a/tests/drivers/sensor/hc-sr04/boards/native_sim.overlay (original)
+++ b/tests/drivers/sensor/hc-sr04/boards/native_sim.overlay (final)
@@ -6,4 +6,5 @@
 	hc_sr04: hc_sr04 {
 		compatible = "hc-sr04";
+		status = "okay";
 		trigger-gpios = <&gpio0 1 GPIO_ACTIVE_HIGH>;
 		echo-gpios = <&gpio0 2 GPIO_ACTIVE_HIGH>;
```

## [openhands] v3new_kconfig_adt7420_5058917 (kconfig) -> differs
- drivers/sensor/adi/adt7420/Kconfig: differs
```diff
--- a/drivers/sensor/adi/adt7420/Kconfig (original)
+++ b/drivers/sensor/adi/adt7420/Kconfig (final)
@@ -7,7 +7,5 @@
 	bool "ADT7420 Temperature Sensor"
 	default y
-	depends on DT_HAS_ADI_ADT7420_ENABLED || DT_HAS_ADI_ADT7410_ENABLED || \
-			DT_HAS_ADI_ADT7422_ENABLED
-	select I2C
+		depends on DT_HAS_ADI_ADT7420_ENABLED
 	help
 	  Enable the driver for Analog Devices ADT7420 High-Accuracy
```

## [openhands] v3new_kconfig_nvs_qemu_161f758 (kconfig) -> differs
- subsys/kvss/nvs/Kconfig: differs
```diff
--- a/subsys/kvss/nvs/Kconfig (original)
+++ b/subsys/kvss/nvs/Kconfig (final)
@@ -6,5 +6,5 @@
 config NVS
 	bool "Non-volatile Storage"
-	depends on FLASH
+	depends on FLASH_HAS_DRIVER_ENABLED
 	depends on FLASH_PAGE_LAYOUT
 	select CRC
```

## [openhands] v3new_runtime_p4wq_577e42a (runtime_crash) -> differs
- lib/os/p4wq.c: differs
```diff
--- a/lib/os/p4wq.c (original)
+++ b/lib/os/p4wq.c (final)
@@ -62,13 +62,23 @@
 static inline bool item_lessthan(struct k_p4wq_work *a, struct k_p4wq_work *b)
 {
+	/* a has lower priority than b if a->priority is numerically greater
+	 * than b->priority.
+	 */
 	if (a->priority > b->priority) {
 		return true;
-	} else if ((a->priority == b->priority) &&
-		   (a->deadline != b->deadline)) {
-		return a->deadline - b->deadline > 0;
-	} else {
-		;
-	}
-	return false;
+	}
+
+	/* a has higher priority than b if a->priority is numerically less
+	 * than b->priority.
+	 */
+	if (a->priority < b->priority) {
+		return false;
+	}
+
+	/* Priorities are equal, compare deadlines. a has lower priority than b
+	 * if a->deadline is numerically greater than b->deadline (meaning 'a'
+	 * has a later deadline, thus less urgent).
+	 */
+	return a->deadline > b->deadline;
 }
 
```

## [proposed] v3_api_substitute_early_sleep_161f758 (runtime_crash) -> differs
- tests/kernel/early_sleep/src/main.c: differs
```diff
--- a/tests/kernel/early_sleep/src/main.c (original)
+++ b/tests/kernel/early_sleep/src/main.c (final)
@@ -41,5 +41,5 @@
 
 	start_time = k_cycle_get_32();
-	k_sleep(K_MSEC(k_ticks_to_ms_floor64(ticks)));
+	k_sleep(K_TICKS(ticks));
 	stop_time = k_cycle_get_32();
 
```

## [proposed] v3_api_substitute_event_reset_on_wait_5058917 (runtime_crash) -> differs
- tests/kernel/events/event_api/src/main.c: differs
```diff
--- a/tests/kernel/events/event_api/src/main.c (original)
+++ b/tests/kernel/events/event_api/src/main.c (final)
@@ -305,5 +305,5 @@
 
 	k_sem_give(&sync_sem);
-	k_sleep(DELAY);           /* Give receiver thread time to run */
+	k_sleep(DELAY); /* Give receiver time to pend */
 	k_event_post(&test_event, 0x123);
 	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
@@ -318,5 +318,5 @@
 
 	k_sem_give(&sync_sem);
-	k_sleep(DELAY);
+	k_sleep(DELAY); /* Give receiver time to pend */
 	k_event_post(&test_event, 0x248);
 	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
@@ -331,5 +331,5 @@
 
 	k_sem_give(&sync_sem);
-	k_sleep(DELAY);
+	k_sleep(DELAY); /* Give receiver time to pend */
 	k_event_post(&test_event, 0x248021);
 	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
@@ -344,5 +344,5 @@
 
 	k_sem_give(&sync_sem);
-	k_sleep(DELAY);
+	k_sleep(DELAY); /* Give receiver time to pend */
 	k_event_post(&test_event, 0x123456);
 	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
```

## [proposed] v3_api_substitute_mutex_recursive_b30a5a9 (runtime_crash) -> differs
- tests/kernel/mutex/mutex_api/src/test_mutex_apis.c: differs
```diff
--- a/tests/kernel/mutex/mutex_api/src/test_mutex_apis.c (original)
+++ b/tests/kernel/mutex/mutex_api/src/test_mutex_apis.c (final)
@@ -298,5 +298,5 @@
 
 	/* Give thread_waiter a chance to get the mutex */
-	k_sleep(K_MSEC(1));
+	k_msleep(1); /* Ensure the waiter thread gets scheduled */
 
 	/**TESTPOINT: waiter thread got the mutex */
```

## [proposed] v3_api_substitute_sleep_yield_54ee8ec_r (runtime_crash) -> differs
- tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c: differs
```diff
--- a/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (original)
+++ b/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (final)
@@ -132,5 +132,5 @@
 	spawn_threads(0);
 	/* checkpoint: all ready threads get executed when k_sleep */
-	k_sleep(K_MSEC(100));
+	k_sleep(K_MSEC(1)); /* Allow all other threads to run */
 	for (int i = 0; i < THREADS_NUM; i++) {
 		zassert_true(tdata[i].executed == 1);
```

## [proposed] v3_c_api_substitute_modem_pipe_6182bc0 (runtime_crash) -> differs
- tests/subsys/modem/modem_pipe/src/main.c: differs
```diff
--- a/tests/subsys/modem/modem_pipe/src/main.c (original)
+++ b/tests/subsys/modem/modem_pipe/src/main.c (final)
@@ -276,4 +276,5 @@
 	zassert_equal(atomic_get(&test_state), 0, "Unexpected state %u",
 		      (uint32_t)atomic_get(&test_state));
+	/* Allow the system workqueue to process the delayed notification */
 	k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT);
 	zassert_equal(atomic_get(&test_state), BIT(TEST_MODEM_PIPE_EVENT_TRANSMIT_IDLE_BIT),
```

## [proposed] v3_compound_i2c_emul_kconfig_dts_daad7c5 (compound) -> differs
- drivers/i2c/Kconfig.i2c_emul: exact_restore
- tests/drivers/i2c/i2c_emul/boards/native_sim.overlay: differs
```diff
--- a/tests/drivers/i2c/i2c_emul/boards/native_sim.overlay (original)
+++ b/tests/drivers/i2c/i2c_emul/boards/native_sim.overlay (final)
@@ -12,5 +12,5 @@
 		#size-cells = <0>;
 		#forward-cells = <1>;
-		reg = <0x400 4>;
+		reg = <0x400 0x4>;
 	};
 
```

## [proposed] v3_kconfig_flash_map_depends_93db4e5_fx (kconfig) -> differs
- subsys/storage/flash_map/Kconfig: differs
```diff
--- a/subsys/storage/flash_map/Kconfig (original)
+++ b/subsys/storage/flash_map/Kconfig (final)
@@ -10,5 +10,5 @@
 menuconfig FLASH_MAP
 	bool "Flash map abstraction module"
-	depends on FLASH_HAS_DRIVER_ENABLED
+	select FLASH_HAS_DRIVERS_ENABLED
 	help
 	  Enable support of flash map abstraction.
```

## [proposed] v3_kconfig_modem_ppp_depends_acc5365_fx (kconfig) -> differs
- subsys/modem/Kconfig: differs
```diff
--- a/subsys/modem/Kconfig (original)
+++ b/subsys/modem/Kconfig (final)
@@ -131,7 +131,7 @@
 	bool "Modem PPP module"
 	depends on NET_L2_PPP
-	select MODEM_PIPE
 	select RING_BUFFER
 	select CRC
+	select MODEM_PIPE
 	select PM_DEVICE_RUNTIME_ASYNC if PM_DEVICE_RUNTIME
 
```

## [proposed] v3_kconfig_wireguard_depends_aa50e83_fx (kconfig) -> differs
- subsys/net/lib/wireguard/Kconfig: differs
```diff
--- a/subsys/net/lib/wireguard/Kconfig (original)
+++ b/subsys/net/lib/wireguard/Kconfig (final)
@@ -7,5 +7,4 @@
 	bool "Wireguard VPN service [EXPERIMENTAL]"
 	depends on NET_UDP
-	select NET_VPN
 	select NET_IPV4_MAPPING_TO_IPV6 if NET_IPV4 && NET_IPV6
 	select NET_MGMT
@@ -18,4 +17,5 @@
 	select BASE64
 	select EXPERIMENTAL
+	select NET_VPN
 	help
 	  Select 'y' if you want to enable Wireguard VPN service.
```

## [proposed] v3_runtime_c_lib_common_buffer_shrink_acc5365_r (runtime_crash) -> differs
- tests/lib/c_lib/common/src/main.c: differs
```diff
--- a/tests/lib/c_lib/common/src/main.c (original)
+++ b/tests/lib/c_lib/common/src/main.c (final)
@@ -166,5 +166,5 @@
 #define BUFSIZE 10
 
-char buffer[BUFSIZE];
+char buffer[BUFSIZE + 1]; /* +1 for null terminator */
 
 /**
```

## [proposed] v3_runtime_can_loopback_nullcheck_6182bc0 (runtime_crash) -> partial
- drivers/can/can_loopback.c: untouched

## [proposed] v3_runtime_cbprintf_package_compare_buf_shrink_9be6ee7 (runtime_crash) -> differs
- tests/lib/cbprintf_package/src/main.c: differs
```diff
--- a/tests/lib/cbprintf_package/src/main.c (original)
+++ b/tests/lib/cbprintf_package/src/main.c (final)
@@ -37,5 +37,5 @@
 static char static_buf[512];
 static char runtime_buf[512];
-static char compare_buf[128];
+static char compare_buf[512];
 
 static void dump(const char *desc, uint8_t *package, size_t len)
```

## [proposed] v3_runtime_enum_mgmt_buffer_shrink_bf226c6 (runtime_crash) -> differs
- tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c: differs
```diff
--- a/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (original)
+++ b/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (final)
@@ -66,5 +66,5 @@
 
 struct details_entries {
-	char expected_name[32];
+	char expected_name[11]; /* Increased size to accommodate "shell mgmt" + null terminator */
 	uint8_t expected_handlers;
 	bool expected_test;
```

## [proposed] v3_runtime_flash_sim_offbyone_907f069 (runtime_crash) -> differs
- drivers/flash/flash_simulator.c: differs
```diff
--- a/drivers/flash/flash_simulator.c (original)
+++ b/drivers/flash/flash_simulator.c (final)
@@ -158,6 +158,6 @@
 	const struct flash_simulator_config *cfg = dev->config;
 
-	if ((offset < 0 || offset >= cfg->flash_size ||
-	     (cfg->flash_size - offset) < len)) {
+	if ((offset < 0) || (offset >= cfg->flash_size) ||
+	    ((offset + len) > cfg->flash_size)) {
 		return 0;
 	}
```

## [proposed] v3_runtime_msgq_offbyone_bcf31c2 (runtime_crash) -> differs
- kernel/msg_q.c: differs
```diff
--- a/kernel/msg_q.c (original)
+++ b/kernel/msg_q.c (final)
@@ -147,68 +147,69 @@
 
 	if (msgq->used_msgs < msgq->max_msgs) {
-		/* message queue isn't full */
+		/* message queue has space */
+		if (put_at_back) {
+			__ASSERT_NO_MSG((msgq->write_ptr >= msgq->buffer_start) &&
+					(msgq->write_ptr <= (msgq->buffer_end - 1)) &&
+					((size_t)(uintptr_t)(msgq->buffer_end - msgq->write_ptr) >=
+						msgq->msg_size));
+			/*
+			 * to write a message to the back of the queue,
+			 * copy the message and increment write_ptr
+			 */
+			(void)memcpy(msgq->write_ptr, (char *)data, msgq->msg_size);
+			msgq->write_ptr += msgq->msg_size;
+			if (msgq->write_ptr == msgq->buffer_end) {
+				msgq->write_ptr = msgq->buffer_start;
+			}
+		} else {
+			/*
+			 * to write a message to the head of the queue,
+			 * first decrement the read pointer (to open
+			 * space at the front of the queue) then copy
+			 * the message to the newly created space.
+			 */
+			if (msgq->read_ptr == msgq->buffer_start) {
+				msgq->read_ptr = msgq->buffer_end;
+			}
+			msgq->read_ptr -= msgq->msg_size;
+			(void)memcpy(msgq->read_ptr, (char *)data, msgq->msg_size);
+		}
+		msgq->used_msgs++;
+		resched = handle_poll_events(msgq);
+		result = 0;
+	} else { /* msgq->used_msgs == msgq->max_msgs (queue is full) */
 		pending_thread = z_unpend_first_thread(&msgq->wait_q);
 		if (unlikely(pending_thread != NULL)) {
 			resched = true;
 
-			/* give message to waiting thread */
+			/* give message to waiting thread (assumed reader) */
 			(void)memcpy(pending_thread->base.swap_data, data, msgq->msg_size);
 			/* wake up waiting thread */
 			arch_thread_return_value_set(pending_thread, 0);
 			z_ready_thread(pending_thread);
+			result = 0;
+		} else if (K_TIMEOUT_EQ(timeout, K_NO_WAIT)) {
+			/* don't wait for message space to become available */
+			result = -ENOMSG;
 		} else {
-			__ASSERT_NO_MSG((msgq->write_ptr >= msgq->buffer_start) &&
-					(msgq->write_ptr <= (msgq->buffer_end - 1)) &&
-					((size_t)(uintptr_t)(msgq->buffer_end - msgq->write_ptr) >=
-						msgq->msg_size));
 			if (put_at_back) {
-				/*
-				 * to write a message to the back of the queue,
-				 * copy the message and increment write_ptr
-				 */
-				(void)memcpy(msgq->write_ptr, (char *)data, msgq->msg_size);
-				msgq->write_ptr += msgq->msg_size;
-				if (msgq->write_ptr == msgq->buffer_end) {
-					msgq->write_ptr = msgq->buffer_start;
-				}
+				SYS_PORT_TRACING_OBJ_FUNC_BLOCKING(k_msgq, put, msgq, timeout);
 			} else {
-				/*
-				 * to write a message to the head of the queue,
-				 * first decrement the read pointer (to open
-				 * space at the front of the queue) then copy
-				 * the message to the newly created space.
-				 */
-				if (msgq->read_ptr == msgq->buffer_start) {
-					msgq->read_ptr = msgq->buffer_end;
-				}
-				msgq->read_ptr -= msgq->msg_size;
-				(void)memcpy(msgq->read_ptr, (char *)data, msgq->msg_size);
+				SYS_PORT_TRACING_OBJ_FUNC_BLOCKING(k_msgq, put_front, msgq, timeout);
 			}
-			msgq->used_msgs++;
-			resched = handle_poll_events(msgq);
+
+			/* wait for put message success, failure, or timeout */
+			_current->base.swap_data = (void *) data;
+
+			result = z_pend_curr(&msgq->lock, key, &msgq->wait_q, timeout);
+
+			if (put_at_back) {
+				SYS_PORT_TRACING_OBJ_FUNC_EXIT(k_msgq, put, msgq, timeout, result);
+			} else {
+				SYS_PORT_TRACING_OBJ_FUNC_EXIT(k_msgq, put_front, msgq, timeout, result);
+			}
+
+			return result;
 		}
-		result = 0;
-	} else if (K_TIMEOUT_EQ(timeout, K_NO_WAIT)) {
-		/* don't wait for message space to become available */
-		result = -ENOMSG;
-	} else {
-		if (put_at_back) {
-			SYS_PORT_TRACING_OBJ_FUNC_BLOCKING(k_msgq, put, msgq, timeout);
-		} else {
-			SYS_PORT_TRACING_OBJ_FUNC_BLOCKING(k_msgq, put_front, msgq, timeout);
-		}
-
-		/* wait for put message success, failure, or timeout */
-		_current->base.swap_data = (void *) data;
-
-		result = z_pend_curr(&msgq->lock, key, &msgq->wait_q, timeout);
-
-		if (put_at_back) {
-			SYS_PORT_TRACING_OBJ_FUNC_EXIT(k_msgq, put, msgq, timeout, result);
-		} else {
-			SYS_PORT_TRACING_OBJ_FUNC_EXIT(k_msgq, put_front, msgq, timeout, result);
-		}
-
-		return result;
 	}
 
```

## [proposed] v3_runtime_nvmem_cell_bounds_offbyone_44853cf_fx (runtime_crash) -> differs
- subsys/nvmem/nvmem.c: differs
```diff
--- a/subsys/nvmem/nvmem.c (original)
+++ b/subsys/nvmem/nvmem.c (final)
@@ -15,7 +15,9 @@
 int nvmem_cell_read(const struct nvmem_cell *cell, void *buf, off_t off, size_t len)
 {
-	__ASSERT_NO_MSG(cell != NULL);
+	if (cell == NULL || buf == NULL) {
+		return -EINVAL;
+	}
 
-	if (off < 0 || cell->size < off + len) {
+	if (off < 0 || (size_t)off > cell->size || len > cell->size - (size_t)off) {
 		return -EINVAL;
 	}
@@ -46,7 +48,9 @@
 int nvmem_cell_write(const struct nvmem_cell *cell, const void *buf, off_t off, size_t len)
 {
-	__ASSERT_NO_MSG(cell != NULL);
+	if (cell == NULL || buf == NULL) {
+		return -EINVAL;
+	}
 
-	if (off < 0 || cell->size < off + len) {
+	if (off < 0 || (size_t)off > cell->size || len > cell->size - (size_t)off) {
 		return -EINVAL;
 	}
```

## [proposed] v3new_compound_akm09918c_kconfig_dts_134e694 (compound) -> differs
- drivers/sensor/asahi_kasei/akm09918c/Kconfig: differs
```diff
--- a/drivers/sensor/asahi_kasei/akm09918c/Kconfig (original)
+++ b/drivers/sensor/asahi_kasei/akm09918c/Kconfig (final)
@@ -6,5 +6,5 @@
 	bool "AKM09918C Magnetometer"
 	default y
-	depends on DT_HAS_ASAHI_KASEI_AKM09918C_ENABLED
+	depends on SENSOR && DT_HAS_ASAHI_KASEI_AKM09918C_ENABLED
 	select I2C
 	select I2C_RTIO if SENSOR_ASYNC_API
```
- tests/drivers/sensor/akm09918c/boards/native_sim.overlay: differs
```diff
--- a/tests/drivers/sensor/akm09918c/boards/native_sim.overlay (original)
+++ b/tests/drivers/sensor/akm09918c/boards/native_sim.overlay (final)
@@ -5,5 +5,5 @@
 &i2c0 {
 	akm09918c: akm09918c@c {
-		compatible = "asahi-kasei,akm09918c";
+		compatible = "asahi-kasei,akm09918c", "asahi-kasei,akm09918c-emul";
 		reg = <0xc>;
 	};
```

## [proposed] v3new_compound_comparator_shell_kconfig_dts_30bef2a (compound) -> partial
- drivers/comparator/Kconfig.fake_comp: untouched
- tests/drivers/comparator/shell/app.overlay: untouched

## [proposed] v3new_dts_hc_sr04_compatible_91db82a (dts) -> differs
- tests/drivers/sensor/hc-sr04/boards/native_sim.overlay: differs
```diff
--- a/tests/drivers/sensor/hc-sr04/boards/native_sim.overlay (original)
+++ b/tests/drivers/sensor/hc-sr04/boards/native_sim.overlay (final)
@@ -6,4 +6,5 @@
 	hc_sr04: hc_sr04 {
 		compatible = "hc-sr04";
+		status = "okay";
 		trigger-gpios = <&gpio0 1 GPIO_ACTIVE_HIGH>;
 		echo-gpios = <&gpio0 2 GPIO_ACTIVE_HIGH>;
```

## [proposed] v3new_kconfig_adt7420_5058917 (kconfig) -> differs
- drivers/sensor/adi/adt7420/Kconfig: differs
```diff
--- a/drivers/sensor/adi/adt7420/Kconfig (original)
+++ b/drivers/sensor/adi/adt7420/Kconfig (final)
@@ -7,6 +7,6 @@
 	bool "ADT7420 Temperature Sensor"
 	default y
-	depends on DT_HAS_ADI_ADT7420_ENABLED || DT_HAS_ADI_ADT7410_ENABLED || \
-			DT_HAS_ADI_ADT7422_ENABLED
+	depends on DT_HAS_ADI_ADT7240_ENABLED || DT_HAS_ADI_ADT7410_ENABLED || \
+			DT_HAS_ADI_ADT7420_ENABLED || DT_HAS_ADI_ADT7422_ENABLED
 	select I2C
 	help
```

## [proposed] v3new_kconfig_lc709203f_577e42a_fx (kconfig) -> differs
- drivers/fuel_gauge/lc709203f/Kconfig: differs
```diff
--- a/drivers/fuel_gauge/lc709203f/Kconfig (original)
+++ b/drivers/fuel_gauge/lc709203f/Kconfig (final)
@@ -7,6 +7,9 @@
 	default y
 	depends on DT_HAS_ONNN_LC709203F_ENABLED
+	select I2C
 	select CRC
-	select I2C
+	select CRC_DRIVER
+	select CRC8
+	select CRC_DRIVER_HAS_CRC8
 	help
 	  Enable I2C-based driver for LC709203F Fuel Gauge.
```

## [proposed] v3new_runtime_heap_34f8f2d (runtime_crash) -> differs
- lib/heap/heap.c: differs
```diff
--- a/lib/heap/heap.c (original)
+++ b/lib/heap/heap.c (final)
@@ -376,7 +376,14 @@
 
 	int bi = bucket_idx(h, sz);
+
+	/* If the requested size is too large for any bucket in this heap,
+	 * return NULL. This check also prevents out-of-bounds access to
+	 * h->buckets if bi exceeds the maximum valid bucket index for this heap.
+	 */
+	if (bi > bucket_idx(h, h->end_chunk)) {
+		return 0;
+	}
+
 	struct z_heap_bucket *b = &h->buckets[bi];
-
-	CHECK(bi <= bucket_idx(h, h->end_chunk));
 
 	/* First try a bounded count of items from the minimal bucket
```

## [proposed] v3new_runtime_min_heap_134e694 (runtime_crash) -> differs
- lib/min_heap/min_heap.c: differs
```diff
--- a/lib/min_heap/min_heap.c (original)
+++ b/lib/min_heap/min_heap.c (final)
@@ -101,5 +101,5 @@
 int min_heap_push(struct min_heap *heap, const void *item)
 {
-	if (heap->size >= heap->capacity) {
+	if (heap->size == heap->capacity) {
 		return -ENOMEM;
 	}
```


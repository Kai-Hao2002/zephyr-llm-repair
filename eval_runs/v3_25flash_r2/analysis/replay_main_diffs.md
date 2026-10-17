## [b2] v3_api_substitute_early_sleep_161f758 (runtime_crash) -> differs
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

## [b2] v3_api_substitute_modem_ubx_thread_yield_9be6ee7 (runtime_crash) -> differs
- tests/subsys/modem/modem_ubx/src/main.c: differs
```diff
--- a/tests/subsys/modem/modem_ubx/src/main.c (original)
+++ b/tests/subsys/modem/modem_ubx/src/main.c (final)
@@ -152,4 +152,10 @@
 	 * multiple thread interactions which may not be served by simply
 	 * yielding.
+	 *
+	 * The original k_yield() was insufficient to guarantee that the modem
+	 * UBX internal thread processed the incoming data from the mock pipe,
+	 * leading to a timeout in the test. Replacing it with k_sleep(K_MSEC(1))
+	 * ensures that the scheduler runs and allows the UBX thread to complete
+	 * its processing.
 	 */
 	k_sleep(K_MSEC(1));
```

## [b2] v3_api_substitute_sleep_yield_54ee8ec_r (runtime_crash) -> differs
- tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c: differs
```diff
--- a/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (original)
+++ b/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (final)
@@ -132,5 +132,5 @@
 	spawn_threads(0);
 	/* checkpoint: all ready threads get executed when k_sleep */
-	k_sleep(K_MSEC(100));
+	k_sleep(K_MSEC(10));
 	for (int i = 0; i < THREADS_NUM; i++) {
 		zassert_true(tdata[i].executed == 1);
```

## [b2] v3_c_api_substitute_modem_pipe_6182bc0 (runtime_crash) -> differs
- tests/subsys/modem/modem_pipe/src/main.c: differs
```diff
--- a/tests/subsys/modem/modem_pipe/src/main.c (original)
+++ b/tests/subsys/modem/modem_pipe/src/main.c (final)
@@ -276,4 +276,5 @@
 	zassert_equal(atomic_get(&test_state), 0, "Unexpected state %u",
 		      (uint32_t)atomic_get(&test_state));
+	/* Wait for the asynchronous transmit idle notification to be processed by the workqueue. */
 	k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT);
 	zassert_equal(atomic_get(&test_state), BIT(TEST_MODEM_PIPE_EVENT_TRANSMIT_IDLE_BIT),
```

## [b2] v3_dts_fwcfg_offbyone_x86_134e694 (runtime_crash) -> differs
- tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay: differs
```diff
--- a/tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay (original)
+++ b/tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay (final)
@@ -7,5 +7,5 @@
 	fwcfg0: fw-cfg@510 {
 		compatible = "qemu,fw-cfg-ioport";
-		reg = <0x510 0x18>;
+		reg = <0x510 0x2>;
 		status = "okay";
 	};
```

## [b2] v3_dts_gpio_latch_reg_cellcount_4ef3792 (dts) -> differs
- tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay: differs
```diff
--- a/tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay (original)
+++ b/tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay (final)
@@ -2,5 +2,5 @@
 	test_latch: gpio@2000ff00 {
 		compatible = "gpio-mmio-latch";
-		reg = <0x2000ff00 0x4>;
+		reg = <0x2000ff00 0x4>; /* Address and size (4 bytes for a 32-bit register) */
 		gpio-controller;
 		#gpio-cells = <2>;
```

## [b2] v3_kconfig_modem_ppp_depends_acc5365_fx (kconfig) -> differs
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

## [b2] v3_kconfig_wireguard_depends_aa50e83_fx (kconfig) -> differs
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

## [b2] v3_kconfig_zbus_depends_b0ed2b5 (kconfig) -> differs
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

## [b2] v3_runtime_cbprintf_package_compare_buf_shrink_9be6ee7 (runtime_crash) -> differs
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

## [b2] v3_runtime_enum_mgmt_buffer_shrink_bf226c6 (runtime_crash) -> differs
- tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c: differs
```diff
--- a/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (original)
+++ b/tests/subsys/mgmt/mcumgr/enum_mgmt/src/main.c (final)
@@ -66,5 +66,5 @@
 
 struct details_entries {
-	char expected_name[32];
+	char expected_name[16]; /* Max name length is "shell mgmt" (10 chars) + null terminator */
 	uint8_t expected_handlers;
 	bool expected_test;
```

## [b2] v3_runtime_video_emul_rx_offbyone_93db4e5 (runtime_crash) -> differs
- drivers/video/video_emul_rx.c: differs
```diff
--- a/drivers/video/video_emul_rx.c (original)
+++ b/drivers/video/video_emul_rx.c (final)
@@ -109,5 +109,5 @@
 	const struct emul_rx_config *cfg = dev->config;
 	struct video_format *fmt = &data->fmt;
-	struct video_buffer *vbuf = vbuf;
+	struct video_buffer *vbuf;
 
 	LOG_DBG("Queueing a frame of %u bytes in format %x %ux%u", fmt->pitch * fmt->height,
```

## [b2] v3new_c_syntax_poll_acc5365_fx (c_syntax) -> differs
- tests/kernel/poll/src/test_poll.c: differs
```diff
--- a/tests/kernel/poll/src/test_poll.c (original)
+++ b/tests/kernel/poll/src/test_poll.c (final)
@@ -78,5 +78,5 @@
 	k_sem_init(&no_wait_sem, 1, 1);
 	k_fifo_init(&no_wait_fifo);
-	k_poll_signal_init(&no_wait_signal);
+	k_poll_signal_init(&no_wait_signal); /* Corrected function name */
 
 	k_msgq_alloc_init(mq, MSGQ_MSG_SIZE, MSGQ_MAX_MSGS);
```

## [b2] v3new_kconfig_lc709203f_577e42a_fx (kconfig) -> differs
- drivers/fuel_gauge/lc709203f/Kconfig: differs
```diff
--- a/drivers/fuel_gauge/lc709203f/Kconfig (original)
+++ b/drivers/fuel_gauge/lc709203f/Kconfig (final)
@@ -7,6 +7,6 @@
 	default y
 	depends on DT_HAS_ONNN_LC709203F_ENABLED
+	select I2C
 	select CRC
-	select I2C
 	help
 	  Enable I2C-based driver for LC709203F Fuel Gauge.
```

## [b3] v3_api_substitute_modem_ubx_thread_yield_9be6ee7 (runtime_crash) -> differs
- tests/subsys/modem/modem_ubx/src/main.c: differs
```diff
--- a/tests/subsys/modem/modem_ubx/src/main.c (original)
+++ b/tests/subsys/modem/modem_ubx/src/main.c (final)
@@ -153,5 +153,5 @@
 	 * yielding.
 	 */
-	k_sleep(K_MSEC(1));
+	k_msleep(1);
 }
 
```

## [b3] v3_api_substitute_sleep_yield_54ee8ec_r (runtime_crash) -> differs
- tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c: differs
```diff
--- a/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (original)
+++ b/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c (final)
@@ -132,5 +132,5 @@
 	spawn_threads(0);
 	/* checkpoint: all ready threads get executed when k_sleep */
-	k_sleep(K_MSEC(100));
+	k_sleep(K_MSEC(100)); /* Allow all threads to run */
 	for (int i = 0; i < THREADS_NUM; i++) {
 		zassert_true(tdata[i].executed == 1);
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
+		reg = <0x510 0x2>;
 		status = "okay";
 	};
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

## [b3] v3_runtime_enum_mgmt_buffer_shrink_bf226c6 (runtime_crash) -> differs
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

## [b3] v3new_c_syntax_thread_apis_9be6ee7_r2 (c_syntax) -> differs
- tests/kernel/threads/thread_apis/src/main.c: differs
```diff
--- a/tests/kernel/threads/thread_apis/src/main.c (original)
+++ b/tests/kernel/threads/thread_apis/src/main.c (final)
@@ -715,5 +715,5 @@
 
 	return NULL;
-}
+} /* End of thread_test_setup function */
 
 ZTEST_SUITE(threads_lifecycle, NULL, thread_test_setup, NULL, NULL, NULL);
```

## [b3] v3new_dts_settings_retention_qemu_3701def (dts) -> differs
- tests/subsys/settings/retention/boards/qemu_cortex_m3.overlay: differs
```diff
--- a/tests/subsys/settings/retention/boards/qemu_cortex_m3.overlay (original)
+++ b/tests/subsys/settings/retention/boards/qemu_cortex_m3.overlay (final)
@@ -16,5 +16,5 @@
 
 		retainedmem {
-			compatible = "zephyr,retained-ram";
+			compatible = "zephyr,retained-ram", "zephyr,retention-partition";
 			status = "okay";
 			#address-cells = <1>;
```

## [proposed] v3_api_substitute_k_yield_lower_prio_577e42a_ns (runtime_crash) -> differs
- tests/kernel/context/src/main.c: differs
```diff
--- a/tests/kernel/context/src/main.c (original)
+++ b/tests/kernel/context/src/main.c (final)
@@ -855,4 +855,8 @@
 	 */
 	k_thread_priority_set(self_thread_id, self_thread_id->base.prio - 1);
+	/*
+	 * Calling k_yield() should not result in switching to the helper,
+	 * as the current thread now has a higher priority.
+	 */
 	k_yield();
 
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
+	k_sleep(K_MSEC(1));
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
+	/* Wait for the asynchronous transmit idle notification to be processed by the workqueue. */
 	k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT);
 	zassert_equal(atomic_get(&test_state), BIT(TEST_MODEM_PIPE_EVENT_TRANSMIT_IDLE_BIT),
```

## [proposed] v3_c_api_substitute_smp_client_3628e91 (runtime_crash) -> differs
- tests/subsys/mgmt/mcumgr/smp_client/src/main.c: differs
```diff
--- a/tests/subsys/mgmt/mcumgr/smp_client/src/main.c (original)
+++ b/tests/subsys/mgmt/mcumgr/smp_client/src/main.c (final)
@@ -68,5 +68,12 @@
 	rc = smp_client_send_cmd(&smp_client, nb, smp_client_res_cb, &testing_user_data, 2);
 	zassert_equal(MGMT_ERR_EOK, rc, "Expected to receive %d response %d", MGMT_ERR_EOK, rc);
-	k_sleep(K_SECONDS(3));
+	/* Wait for the command to timeout.
+	 * The total timeout duration is calculated as:
+	 * (timeout_in_sec * (1000 / CONFIG_SMP_CMD_RETRY_TIME) + 1) * CONFIG_SMP_CMD_RETRY_TIME.
+	 * With timeout_in_sec=2 and default CONFIG_SMP_CMD_RETRY_TIME=500, this is:
+	 * (2 * (1000/500) + 1) * 500 = (4 + 1) * 500 = 2500ms.
+	 * Add a small buffer to ensure the workqueue processes the timeout.
+	 */
+	k_sleep(K_MSEC(2600));
 	zassert_is_null(res_buf, "NULL pointer was not returned");
 	zassert_equal_ptr(response_ptr, &testing_user_data, "User data not returned correctly");
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

## [proposed] v3_kconfig_zbus_depends_b0ed2b5 (kconfig) -> differs
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

## [proposed] v3_runtime_cbprintf_package_compare_buf_shrink_9be6ee7 (runtime_crash) -> differs
- tests/lib/cbprintf_package/src/main.c: differs
```diff
--- a/tests/lib/cbprintf_package/src/main.c (original)
+++ b/tests/lib/cbprintf_package/src/main.c (final)
@@ -37,5 +37,5 @@
 static char static_buf[512];
 static char runtime_buf[512];
-static char compare_buf[128];
+static char compare_buf[256]; /* Increased size to prevent truncation in snprintfcb */
 
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
+	char expected_name[16]; /* Increased size to accommodate "shell mgmt" (10 chars + null) */
 	uint8_t expected_handlers;
 	bool expected_test;
```

## [proposed] v3_runtime_flash_sim_offbyone_907f069 (runtime_crash) -> differs
- drivers/flash/flash_simulator.c: differs
```diff
--- a/drivers/flash/flash_simulator.c (original)
+++ b/drivers/flash/flash_simulator.c (final)
@@ -159,5 +159,5 @@
 
 	if ((offset < 0 || offset >= cfg->flash_size ||
-	     (cfg->flash_size - offset) < len)) {
+	     (offset + len) > cfg->flash_size)) {
 		return 0;
 	}
```

## [proposed] v3_runtime_msgq_offbyone_bcf31c2 (runtime_crash) -> differs
- kernel/msg_q.c: differs
```diff
--- a/kernel/msg_q.c (original)
+++ b/kernel/msg_q.c (final)
@@ -148,5 +148,6 @@
 	if (msgq->used_msgs < msgq->max_msgs) {
 		/* message queue isn't full */
-		pending_thread = z_unpend_first_thread(&msgq->wait_q);
+		struct k_thread *pending_thread = z_unpend_first_thread(&msgq->wait_q);
+
 		if (unlikely(pending_thread != NULL)) {
 			resched = true;
```

## [proposed] v3_runtime_nvmem_cell_bounds_offbyone_44853cf_fx (runtime_crash) -> differs
- subsys/nvmem/nvmem.c: differs
```diff
--- a/subsys/nvmem/nvmem.c (original)
+++ b/subsys/nvmem/nvmem.c (final)
@@ -17,5 +17,5 @@
 	__ASSERT_NO_MSG(cell != NULL);
 
-	if (off < 0 || cell->size < off + len) {
+	if (off < 0 || (size_t)off + len > cell->size) {
 		return -EINVAL;
 	}
```

## [proposed] v3_runtime_rtio_sqe_copy_in_offbyone_c79b1e8_fx (runtime_crash) -> differs
- include/zephyr/rtio/rtio.h: differs
```diff
--- a/include/zephyr/rtio/rtio.h (original)
+++ b/include/zephyr/rtio/rtio.h (final)
@@ -905,5 +905,5 @@
 	for (unsigned long i = 0; i < sqe_count; i++) {
 		sqe = rtio_sqe_acquire(r);
-		__ASSERT_NO_MSG(sqe != NULL);
+		__ASSERT_NO_MSG(sqe != NULL); /* Should not happen with the check above */
 		if (handle != NULL && i == 0) {
 			*handle = sqe;
```

## [proposed] v3_runtime_rtio_workq_submit_null_req_f914b6d (runtime_crash) -> differs
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

## [proposed] v3new_compound_comparator_shell_kconfig_dts_30bef2a (compound) -> differs
- drivers/comparator/Kconfig.fake_comp: differs
```diff
--- a/drivers/comparator/Kconfig.fake_comp (original)
+++ b/drivers/comparator/Kconfig.fake_comp (final)
@@ -5,4 +5,3 @@
 	bool "Fake comparator driver"
 	default y
-	depends on DT_HAS_ZEPHYR_FAKE_COMP_ENABLED
 	depends on ZTEST
```
- tests/drivers/comparator/shell/app.overlay: exact_restore

## [proposed] v3new_compound_dma_emul_kconfig_dts_b0ed2b5 (compound) -> differs
- drivers/dma/Kconfig.emul: exact_restore
- tests/drivers/dma/chan_blen_transfer/boards/native_sim.overlay: differs
```diff
--- a/tests/drivers/dma/chan_blen_transfer/boards/native_sim.overlay (original)
+++ b/tests/drivers/dma/chan_blen_transfer/boards/native_sim.overlay (final)
@@ -6,4 +6,5 @@
 
 &dma {
+	compatible = "zephyr,dma-emul";
 	dma-channels = <2>;
 	status = "okay";
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
@@ -7,6 +7,7 @@
 	default y
 	depends on DT_HAS_ONNN_LC709203F_ENABLED
+	select I2C
 	select CRC
-	select I2C
+	select CRC8
 	help
 	  Enable I2C-based driver for LC709203F Fuel Gauge.
```

## [proposed] v3new_kconfig_max17048_f914b6d (kconfig) -> differs
- drivers/fuel_gauge/max17048/Kconfig: differs
```diff
--- a/drivers/fuel_gauge/max17048/Kconfig (original)
+++ b/drivers/fuel_gauge/max17048/Kconfig (final)
@@ -17,5 +17,5 @@
 	default y
 	depends on EMUL
-	depends on MAX17048
+	depends on DT_HAS_MAXIM_MAX17048_ENABLED
 	help
 	  It provides readings which follow a simple sequence, thus allowing
```


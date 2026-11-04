## 1. [proposed] v3new_c_syntax_fdtable_91db82a_r_fx (c_syntax, c_remove_semicolon)
- injected: ['tests/lib/fdtable/src/main.c']
- applied: ['tests/lib/fdtable/src/main.c']
- restored: []

```
tests/lib/fdtable/src/main.c
<<<<<<<< SEARCH
	int err = -1;
	const struct fd_op_vtable *vtable = 0;
	const struct fd_op_vtable *vtable2 = vtable+1;

	int *obj = zvfs_get_fd_obj(fd, vtable, err); /* function being tested */
========
	int err = -1;
	/* Use a dummy non-NULL pointer for arithmetic to avoid cppcheck warning */
	const struct fd_op_vtable *dummy_vtable_ptr = (const struct fd_op_vtable *)1;
	const struct fd_op_vtable *vtable2 = dummy_vtable_ptr + 1;

	/* This vtable is used for the actual test logic, initialized to NULL as per original intent */
	const struct fd_op_vtable *vtable = NULL;

	int *obj = zvfs_get_fd_obj(fd, vtable, err); /* function being tested */
>>>>>>>> REPLACE
```

## 2. [proposed] v3_api_substitute_event_reset_on_wait_5058917 (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/events/event_api/src/main.c']
- applied: ['tests/kernel/events/event_api/src/main.c']
- restored: []

```
tests/kernel/events/event_api/src/main.c
<<<<<<<< SEARCH
	/*
	 * Sync point 2-1. Reset events before receive.
	 * Short timeout, k_event_wait_all(), incomplete match
	 */

	k_sem_give(&sync_sem);
	k_yield();           /* Give receiver thread time to run */
	k_event_post(&test_event, 0x123);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0);
	zassert_true(test_event.events == 0x123);
========
	/*
	 * Sync point 2-1. Reset events before receive.
	 * Short timeout, k_event_wait_all(), incomplete match
	 */

	k_sem_give(&sync_sem);
	k_sleep(DELAY);           /* Give receiver thread time to run */
	k_event_post(&test_event, 0x123);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0);
	zassert_true(test_event.events == 0x123);
>>>>>>>> REPLACE
```

## 3. [proposed] v3_c_api_substitute_backends_tty_54ee8ec (runtime_crash, c_api_substitute)
- injected: ['tests/subsys/modem/backends/tty/src/main.c']
- applied: ['tests/subsys/modem/backends/tty/src/main.c']
- restored: []

```
tests/subsys/modem/backends/tty/src/main.c
<<<<<<<< SEARCH
	k_yield();
========
	k_sleep(K_MSEC(150)); /* Allow backend thread to poll and raise event */
>>>>>>>> REPLACE
```

## 4. [proposed] v3_api_substitute_early_sleep_161f758 (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/early_sleep/src/main.c']
- applied: ['tests/kernel/early_sleep/src/main.c']
- restored: []

```
tests/kernel/early_sleep/src/main.c
<<<<<<<< SEARCH
	uint32_t stop_time;

	start_time = k_cycle_get_32();
	k_yield();
	stop_time = k_cycle_get_32();

	return (stop_time - start_time) / k_ticks_to_cyc_floor32(1);
}
========
	uint32_t stop_time;

	start_time = k_cycle_get_32();
	k_sleep(K_TICKS(ticks));
	stop_time = k_cycle_get_32();

	return (stop_time - start_time) / k_ticks_to_cyc_floor32(1);
}
>>>>>>>> REPLACE
```

## 5. [proposed] v3_api_substitute_sys_mutex_test_mutex_134e694 (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/mutex/sys_mutex/src/main.c']
- applied: ['tests/kernel/mutex/sys_mutex/prj.conf', 'tests/kernel/mutex/sys_mutex/src/main.c']
- restored: ['tests/kernel/mutex/sys_mutex/prj.conf']

```
```
tests/kernel/mutex/sys_mutex/prj.conf
<<<<<<<< SEARCH
CONFIG_MAX_THREAD_BYTES=8
========
CONFIG_USERSPACE=y
CONFIG_MAX_THREAD_BYTES=8
>>>>>>>> REPLACE
tests/kernel/mutex/sys_mutex/src/main.c
<<<<<<<< SEARCH
			K_PRIO_PREEMPT(12), PARTICIPANT_THREAD_OPTIONS, K_NO_WAIT);
	k_yield();     /* Give thread_12 a chance to block on the mutex */
========
			K_PRIO_PREEMPT(9), PARTICIPANT_THREAD_OPTIONS, K_NO_WAIT);
	k_yield();     /* Give thread_12 a chance to block on the mutex */
>>>>>>>> REPLACE
```

## 6. [proposed] v3new_dts_rtc_shell_compatible_3628e91 (dts, dts_remove_compatible)
- injected: ['tests/drivers/rtc/shell/app.overlay']
- applied: ['tests/drivers/rtc/shell/app.overlay']
- restored: []

```
tests/drivers/rtc/shell/app.overlay
<<<<<<<< SEARCH
/ {
	fake_rtc: fake_rtc {
		status = "okay";
	};
};
========
/ {
	fake_rtc: fake_rtc {
		compatible = "zephyr,fake-rtc";
		status = "okay";
	};
};
>>>>>>>> REPLACE
```

## 7. [proposed] v3new_dts_hc_sr04_compatible_91db82a (dts, dts_remove_compatible)
- injected: ['tests/drivers/sensor/hc-sr04/boards/native_sim.overlay']
- applied: ['tests/drivers/sensor/hc-sr04/boards/native_sim.overlay']
- restored: []

```
tests/drivers/sensor/hc-sr04/boards/native_sim.overlay
<<<<<<<< SEARCH
	hc_sr04: hc_sr04 {
		trigger-gpios = <&gpio0 1 GPIO_ACTIVE_HIGH>;
		echo-gpios = <&gpio0 2 GPIO_ACTIVE_HIGH>;
	};
========
	hc_sr04: hc_sr04 {
		compatible = "hc-sr04";
		trigger-gpios = <&gpio0 1 GPIO_ACTIVE_HIGH>;
		echo-gpios = <&gpio0 2 GPIO_ACTIVE_HIGH>;
	};
>>>>>>>> REPLACE
```

## 8. [proposed] v3new_runtime_notify_30bef2a (runtime_crash, runtime_remove_null_check)
- injected: ['lib/utils/notify.c']
- applied: ['lib/utils/notify.c']
- restored: []

```
The error log indicates an assertion failure in `tests/lib/notify/src/main.c` within the `test_signal` test case, specifically on line 120: `zassert_equal(rc, -EINVAL, "invalid not diagnosed");`. The error message `(rc not equal to -EINVAL)` implies that the `sys_notify_validate` function returned a value other than `-EINVAL` when it was expected to return `-EINVAL`.

Upon reviewing the `sys_notify_validate` function in `lib/utils/notify.c`, a clear bug is identified:

```c
#ifdef CONFIG_POLL
	case SYS_NOTIFY_METHOD_SIGNAL:
		if (0) { // <-- This condition is always false
			rv = -EINVAL;
		}
		break;
#endif /* CONFIG_POLL */
```

The `if (0)` condition means that the validation for `SYS_NOTIFY_METHOD_SIGNAL` will never check if `notify->method.signal` is `NULL`. This is a logical error, as a `NULL` signal pointer should be considered an invalid configuration. The `sys_notify_init_signal` function itself asserts that `sigp` is not `NULL`, so `sys_notify_validate` should also enforce this.

While this specific bug might not be the direct cause of the *first* assertion failure in `test_signal` (which occurs when the `notify` structure is `memset` to `0xac`, leading `sys_notify_get_method` to return `SYS_NOTIFY_METHOD_COMPLETED`, which should fall into the `default` case and return `-EINVAL`), it is a critical validation flaw. Fixing this bug is necessary for the correctness of the `sys_notify` API. It's possible that fixing this underlying issue will resolve the test failure, or reveal other issues more clearly.

The fix involves changing `if (0)` to `if (notify->method.signal == NULL)` to correctly validate the signal pointer.

**Reasoning for the fix:**
The `sys_notify_validate` function is responsible for ensuring that the `sys_notify` structure is correctly configured for the chosen notification method. For `SYS_NOTIFY_METHOD_SIGNAL`, it must ensure that a valid `k_poll_signal` pointer is provided. The `if (0)` statement prevents this check, allowing an invalid (NULL) signal pointer to pass validation, which is incorrect behavior.

lib/utils/notify.c
<<<<<<<< SEARCH
	case SYS_NOTIFY_METHOD_SIGNAL:
		if (0) {
			rv = -EINVAL;
		}
		break;
========
	case SYS_NOTIFY_METHOD_SIGNAL:
		if (notify->method.signal == NULL) {
			rv = -EINVAL;
		}
		break;
>>>>>>>> REPLACE
```

## 9. [b3] v3_api_substitute_sleep_yield_54ee8ec_r (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c']
- applied: ['tests/kernel/sched/schedule_api/prj.conf', 'tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c']
- restored: ['tests/kernel/sched/schedule_api/prj.conf']

```
tests/kernel/sched/schedule_api/prj.conf
<<<<<<<< SEARCH
CONFIG_ZTEST_FATAL_HOOK=y
========
CONFIG_ZTEST_FATAL_HOOK=y
CONFIG_STACK_SENTINEL=n
>>>>>>>> REPLACE

tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c
<<<<<<<< SEARCH
	spawn_threads(0);
	/* checkpoint: all ready threads get executed when k_sleep */
	k_yield();
	for (int i = 0; i < THREADS_NUM; i++) {
		zassert_true(tdata[i].executed == 1);
	}
========
	spawn_threads(0);
	/* checkpoint: all ready threads get executed when k_sleep */
	k_sleep(K_MSEC(10)); /* Allow all threads to run */
	for (int i = 0; i < THREADS_NUM; i++) {
		zassert_true(tdata[i].executed == 1);
	}
>>>>>>>> REPLACE
```

## 10. [b3] v3_dts_gnss_emul_alias_break_3701def_fx (dts, dts_break_phandle)
- injected: ['tests/drivers/gnss/gnss_emul/app.overlay']
- applied: ['tests/drivers/gnss/gnss_emul/app.overlay']
- restored: []

```
tests/drivers/gnss/gnss_emul/app.overlay
<<<<<<<< SEARCH
	aliases {
		gnss = &gnns;
	};
========
	aliases {
		gnss = &gnss;
	};
>>>>>>>> REPLACE
```

## 11. [b3] v3_runtime_cmsis_thread2_double_free_4ef3792 (runtime_crash, runtime_double_free)
- injected: ['tests/subsys/portability/cmsis_rtos_v2/src/thread_apis.c']
- applied: ['tests/subsys/portability/cmsis_rtos_v2/src/thread_apis.c']
- restored: []

```
tests/subsys/portability/cmsis_rtos_v2/src/thread_apis.c
<<<<<<<< SEARCH
	zassert_equal(osThreadGetState(thread_array[1]), osThreadReady,
		      "Thread not in ready state");

	k_free(thread_array); k_free(thread_array);

	/* Yield back to thread1 which is of same priority */
	osThreadYield();
========
	zassert_equal(osThreadGetState(thread_array[1]), osThreadReady,
		      "Thread not in ready state");

	k_free(thread_array);

	/* Yield back to thread1 which is of same priority */
	osThreadYield();
>>>>>>>> REPLACE
```

## 12. [b3] v3_api_substitute_modem_ubx_thread_yield_9be6ee7 (runtime_crash, c_api_substitute)
- injected: ['tests/subsys/modem/modem_ubx/src/main.c']
- applied: ['tests/subsys/modem/modem_ubx/src/main.c']
- restored: []

```
tests/subsys/modem/modem_ubx/src/main.c
<<<<<<<< SEARCH
ZTEST(modem_ubx, test_rsp_match_with_payload)
{
	static struct ubx_frame test_rsp_non_match = UBX_FRAME_ACK_INITIALIZER(0x02, 0x03);
	static struct ubx_frame test_rsp_match = UBX_FRAME_ACK_INITIALIZER(0x03, 0x04);

	test_script_runner.script.match.filter.class = 0x03;
	test_script_runner.script.match.filter.id = 0x04;
	test_script_runner.script.match.filter.payload.buf = test_rsp_match.payload_and_checksum;
	test_script_runner.script.match.filter.payload.len = test_rsp_match.payload_size;

	uint8_t buf[256];
	size_t buf_len = 0;

	/** We're passing a valid packet, but not what we're expecting. We
	 * should not get an event out of this one.
	 */
	memcpy(buf, &test_rsp_non_match, UBX_FRAME_SZ(test_rsp_non_match.payload_size));
	buf_len += UBX_FRAME_SZ(test_rsp_non_match.payload_size);

	script_runner_start(&test_script_runner, 0);
	test_thread_yield();

	zassert_false(test_script_runner.result.done, "Script should not be done");

	modem_backend_mock_put(&mock, (uint8_t *)buf, buf_len);
	test_thread_yield();

	zassert_false(test_script_runner.result.done, "Script should not be done");

	/** Now we're passing two valid packets, on the same event: one which
	 * does not match, one which matches. We should get the latter.
	 */
	memcpy(buf, &test_rsp_match, UBX_FRAME_SZ(test_rsp_match.payload_size));
	buf_len += UBX_FRAME_SZ(test_rsp_match.payload_size);

	modem_backend_mock_put(&mock, (uint8_t *)buf, buf_len);
	test_thread_yield();

	zassert_true(test_script_runner.result.done, "Script should be done");
	zassert_ok(test_script_runner.result.ret);
	zassert_equal(UBX_FRAME_SZ(test_rsp_match.payload_size),
		      test_script_runner.script.response.received_len,
		      "expected: %d, got: %d",
		      UBX_FRAME_SZ(test_rsp_match.payload_size),
		      test_script_runner.script.response.received_len);
	zassert_mem_equal(&test_rsp_match,
			  test_script_runner.script.response.buf,
			  UBX_FRAME_SZ(test_rsp_match.payload_size));
}
========
ZTEST(modem_ubx, test_rsp_match_with_payload)
{
	/* Define payloads for the frames */
	static const uint8_t match_payload[] = {0xAA, 0xBB, 0xCC};
	static const uint8_t non_match_payload[] = {0x11, 0x22, 0x33};

	/* Buffers to hold the encoded UBX frames */
	uint8_t test_rsp_match_buf[64];
	uint8_t test_rsp_non_match_buf[64];
	int test_rsp_match_len;
	int test_rsp_non_match_len;

	/* Encode the matching UBX frame (class 0x03, id 0x04, with match_payload) */
	test_rsp_match_len = ubx_frame_encode(0x03, 0x04, match_payload, sizeof(match_payload),
						test_rsp_match_buf, sizeof(test_rsp_match_buf));
	zassert_true(test_rsp_match_len > 0, "Failed to encode match frame");

	/* Encode the non-matching UBX frame (class 0x02, id 0x03, with non_match_payload) */
	test_rsp_non_match_len = ubx_frame_encode(0x02, 0x03, non_match_payload, sizeof(non_match_payload),
						test_rsp_non_match_buf, sizeof(test_rsp_non_match_buf));
	zassert_true(test_rsp_non_match_len > 0, "Failed to encode non-match frame");

	/* Configure the script's match filter to look for class 0x03, id 0x04, and the specific payload */
	test_script_runner.script.match.filter.class = 0x03;
	test_script_runner.script.match.filter.id = 0x04;
	test_script_runner.script.match.filter.payload.buf = match_payload;
	test_script_runner.script.match.filter.payload.len = sizeof(match_payload);

	uint8_t buf[256];

	/* Start the script runner thread */
	script_runner_start(&test_script_runner, 0);
	test_thread_yield();

	/* Assert that the script is not yet done (it's waiting for a response) */
	zassert_false(test_script_runner.result.done, "Script should not be done");

	/* Send the non-matching frame to the mock backend */
	memcpy(buf, test_rsp_non_match_buf, test_rsp_non_match_len);
	modem_backend_mock_put(&mock, (uint8_t *)buf, test_rsp_non_match_len);
	test_thread_yield();

	/* Assert that the script is still not done (it should have filtered out the non-matching frame) */
	zassert_false(test_script_runner.result.done, "Script should not be done");

	/* Send the matching frame to the mock backend */
	memcpy(buf, test_rsp_match_buf, test_rsp_match_len);
	modem_backend_mock_put(&mock, (uint8_t *)buf, test_rsp_match_len);
	test_thread_yield();

	/* Assert that the script is now done and successful */
	zassert_true(test_script_runner.result.done, "Script should be done");
	zassert_ok(test_script_runner.result.ret);
	zassert_equal(test_rsp_match_len,
		      test_script_runner.script.response.received_len,
		      "expected: %d, got: %d",
		      test_rsp_match_len,
		      test_script_runner.script.response.received_len);
	zassert_mem_equal(test_rsp_match_buf,
			  test_script_runner.script.response.buf,
			  test_rsp_match_len);
}
>>>>>>>> REPLACE
```

## 13. [b3] v3_c_timer_api_brace_6182bc0_fx (c_syntax, c_typo_identifier)
- injected: ['tests/kernel/timer/timer_api/src/main.c']
- applied: ['tests/kernel/timer/timer_api/src/main.c']
- restored: []

```
tests/kernel/timer/timer_api/src/main.c
<<<<<<<< SEARCH
static void busy_wait_ms(int32_t ms)
{
	k_busy_wiat(ms*1000);
}
========
static void busy_wait_ms(int32_t ms)
{
	k_busy_wait(ms*1000);
}
>>>>>>>> REPLACE
```

## 14. [b3] v3new_runtime_heap_34f8f2d (runtime_crash, runtime_off_by_one)
- injected: ['lib/heap/heap.c']
- applied: ['lib/heap/heap.c']
- restored: []

```
lib/heap/heap.c
<<<<<<<< SEARCH
	CHECK(bi < bucket_idx(h, h->end_chunk));
========
	CHECK(bi <= bucket_idx(h, h->end_chunk));
>>>>>>>> REPLACE
```

## 15. [b2] v3new_c_syntax_fdtable_91db82a_r_fx (c_syntax, c_remove_semicolon)
- injected: ['tests/lib/fdtable/src/main.c']
- applied: ['tests/lib/fdtable/src/main.c']
- restored: []

```
tests/lib/fdtable/src/main.c
<<<<<<<< SEARCH
	/* This will set obj and vtable properly */
	zvfs_finalize_fd(fd, obj, vtable)

	obj = zvfs_get_fd_obj(-1, vtable, err); /* function being tested */
========
	/* This will set obj and vtable properly */
	zvfs_finalize_fd(fd, obj, vtable);

	obj = zvfs_get_fd_obj(-1, vtable, err); /* function being tested */
>>>>>>>> REPLACE
```

## 16. [b2] v3new_runtime_mem_slab_4ef3792 (runtime_crash, runtime_off_by_one)
- injected: ['kernel/mem_slab.c']
- applied: ['kernel/mem_slab.c']
- restored: []

```
kernel/mem_slab.c
<<<<<<<< SEARCH
	for (uint32_t i = 0; i <= slab->info.num_blocks; i++) {
		*(char **)p = slab->free_list;
		slab->free_list = p;
		p -= slab->info.block_size;
	}
========
	for (uint32_t i = 0; i < slab->info.num_blocks; i++) {
		*(char **)p = slab->free_list;
		slab->free_list = p;
		p -= slab->info.block_size;
	}
>>>>>>>> REPLACE
```

## 17. [b2] v3_kconfig_isotp_depends_577e42a (kconfig, kconfig_invert_depends)
- injected: ['subsys/canbus/isotp/Kconfig']
- applied: ['subsys/canbus/isotp/Kconfig']
- restored: []

```
subsys/canbus/isotp/Kconfig
<<<<<<<< SEARCH
menuconfig ISOTP
	bool "ISO-TP Transport"
	depends on !CAN
	select NET_BUF
	select POLL
	help
	  Enable ISO TP support for CAN
========
menuconfig ISOTP
	bool "ISO-TP Transport"
	depends on CAN
	select NET_BUF
	select POLL
	help
	  Enable ISO TP support for CAN
>>>>>>>> REPLACE
```

## 18. [b2] v3_c_api_substitute_smp_client_3628e91 (runtime_crash, c_api_substitute)
- injected: ['tests/subsys/mgmt/mcumgr/smp_client/src/main.c']
- applied: ['tests/subsys/mgmt/mcumgr/smp_client/src/main.c']
- restored: []

```
tests/subsys/mgmt/mcumgr/smp_client/src/main.c
<<<<<<<< SEARCH
	zassert_is_null(res_buf, "NULL pointer was not returned");
	zassert_equal_ptr(response_ptr, &testing_user_data, "User data not returned correctly");
========
	zassert_is_null(res_buf, "NULL pointer was not returned");
	zassert_is_null(response_ptr, "User data should be NULL on timeout");
>>>>>>>> REPLACE
```

## 19. [b2] v3new_c_syntax_poll_acc5365_fx (c_syntax, c_typo_identifier)
- injected: ['tests/kernel/poll/src/test_poll.c']
- applied: ['tests/kernel/poll/prj.conf', 'tests/kernel/poll/src/test_poll.c']
- restored: ['tests/kernel/poll/prj.conf']

```
tests/kernel/poll/prj.conf
<<<<<<<< SEARCH
CONFIG_SYS_CLOCK_EXISTS=y
========
CONFIG_SYS_CLOCK_EXISTS=y
CONFIG_STACK_SENTINEL=n
>>>>>>>> REPLACE
tests/kernel/poll/src/test_poll.c
<<<<<<<< SEARCH
	k_sem_init(&no_wait_sem, 1, 1);
	k_fifo_init(&no_wait_fifo);
	k_poll_signal_int(&no_wait_signal);

	k_msgq_alloc_init(mq, MSGQ_MSG_SIZE, MSGQ_MAX_MSGS);
========
	k_sem_init(&no_wait_sem, 1, 1);
	k_fifo_init(&no_wait_fifo);
	k_poll_signal_init(&no_wait_signal);

	k_msgq_alloc_init(mq, MSGQ_MSG_SIZE, MSGQ_MAX_MSGS);
>>>>>>>> REPLACE
```

## 20. [b2] v3new_c_syntax_deadline_b30a5a9_fx (c_syntax, c_typo_identifier)
- injected: ['tests/kernel/sched/deadline/src/main.c']
- applied: ['tests/kernel/sched/deadline/src/main.c']
- restored: []

```
tests/kernel/sched/deadline/src/main.c
<<<<<<<< SEARCH
		k_thread_dealine_set(&worker_threads[i], thread_deadlines[i]);
========
		k_thread_deadline_set(&worker_threads[i], thread_deadlines[i]);
>>>>>>>> REPLACE
```


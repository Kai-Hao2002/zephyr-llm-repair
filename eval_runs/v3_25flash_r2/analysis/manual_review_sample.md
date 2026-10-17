## 1. [proposed] v3new_compound_ltc2959_kconfig_dts_b30a5a9 (compound, kconfig_invert_depends+dts_remove_compatible)
- injected: ['drivers/fuel_gauge/ltc2959/Kconfig', 'tests/drivers/fuel_gauge/ltc2959/boards/native_sim.overlay']
- applied: ['drivers/fuel_gauge/ltc2959/Kconfig']
- restored: []

```
drivers/fuel_gauge/ltc2959/Kconfig
<<<<<<<< SEARCH
config EMUL_LTC2959
	bool "Emulate an LTC2959 fuel gauge"
	default y
	depends on !EMUL
	depends on FUEL_GAUGE_LTC2959
	help
	  It provides readings which follow a simple sequence, thus allowing
	  test code to check that things are working as expected.
========
config EMUL_LTC2959
	bool "Emulate an LTC2959 fuel gauge"
	default y
	depends on EMUL
	depends on FUEL_GAUGE_LTC2959
	help
	  It provides readings which follow a simple sequence, thus allowing
	  test code to check that things are working as expected.
>>>>>>>> REPLACE
```

## 2. [proposed] v3_api_substitute_sleep_yield_54ee8ec_r (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c']
- applied: ['tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c', 'tests/kernel/sched/schedule_api/prj.conf', 'tests/kernel/sched/schedule_api/prj_multiq.conf', 'tests/kernel/sched/schedule_api/prj_simple.conf']
- restored: ['tests/kernel/sched/schedule_api/prj.conf', 'tests/kernel/sched/schedule_api/prj_multiq.conf', 'tests/kernel/sched/schedule_api/prj_simple.conf']

```
The Kconfig warning `STACK_SENTINEL (defined at subsys/debug/Kconfig:118) was assigned the value 'y' but got the value 'n'. Check these unsatisfied dependencies: (!USERSPACE) (=n).` indicates that `CONFIG_STACK_SENTINEL` is implicitly disabled because `CONFIG_USERSPACE` is enabled (via `CONFIG_TEST_USERSPACE=y` in the test's `prj.conf`). To resolve this warning and make the configuration explicit, `CONFIG_STACK_SENTINEL=n` should be added to the test's configuration files.

The runtime error `Assertion failed at WEST_TOPDIR/zephyr/tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c:136: threads_scheduling_test_sleep_cooperative: tdata[i].executed == 1 is false` occurs in the `test_sleep_cooperative` test.
The test creates three threads with priorities -2, -1, and 0, while the main thread has priority -1.
- `tdata[0]` (priority -2) will preempt the main thread and execute immediately after creation.
- `tdata[1]` (priority -1) has the same priority as the main thread.
- `tdata[2]` (priority 0) has a lower priority than the main thread.

The original code calls `k_yield()`. When the main thread yields, `tdata[1]` (same priority) will get a chance to run. However, `tdata[2]` (lower priority) will *not* run because the main thread (priority -1) will become ready again after `tdata[1]` finishes, and it still has a higher priority than `tdata[2]`. This leads to `tdata[2].executed` remaining 0, causing the assertion to fail.

The comment `/* checkpoint: all ready threads get executed when k_sleep */` suggests that `k_sleep()` was intended. If `k_sleep(K_MSEC(1))` is used instead of `k_yield()`, the main thread will become unready and be placed on the timeout queue. This allows all other ready threads, including `tdata[2]`, to execute before the main thread wakes up, thus satisfying the assertion.

The fix involves:
1. Changing `k_yield()` to `k_sleep(K_MSEC(1))` in `test_sched_timeslice_and_lock.c` to correctly allow all threads to execute.
2. Explicitly disabling `CONFIG_STACK_SENTINEL` in the `prj.conf` files for this test to resolve the Kconfig warning.

tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c
<<<<<<<< SEARCH
	/* checkpoint: all ready threads get executed when k_sleep */
	k_yield();
	for (int i = 0; i < THREADS_NUM; i++) {
		zassert_true(tdata[i].executed == 1);
	}
========
	/* checkpoint: all ready threads get executed when k_sleep */
	k_sleep(K_MSEC(1));
	for (int i = 0; i < THREADS_NUM; i++) {
		zassert_true(tdata[i].executed == 1);
	}
>>>>>>>> REPLACE

tests/kernel/sched/schedule_api/prj.conf
<<<<<<<< SEARCH
CONFIG_ZTEST_FATAL_HOOK=y
========
CONFIG_ZTEST_FATAL_HOOK=y
CONFIG_STACK_SENTINEL=n
>>>>>>>> REPLACE

tests/kernel/sched/schedule_api/prj_multiq.conf
<<<<<<<< SEARCH
CONFIG_NUM_COOP_PRIORITIES=30
CONFIG_NUM_PREEMPT_PRIORITIES=40
========
CONFIG_NUM_COOP_PRIORITIES=30
CONFIG_NUM_PREEMPT_PRIORITIES=40
CONFIG_STACK_SENTINEL=n
>>>>>>>> REPLACE

tests/kernel/sched/schedule_api/prj_simple.conf
<<<<<<<< SEARCH
CONFIG_ZTEST_FATAL_HOOK=y
========
CONFIG_ZTEST_FATAL_HOOK=y
CONFIG_STACK_SENTINEL=n
>>>>>>>> REPLACE
```

## 3. [proposed] v3_c_timer_api_brace_6182bc0_fx (c_syntax, c_typo_identifier)
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

## 4. [proposed] v3_api_substitute_k_yield_lower_prio_577e42a_ns (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/context/src/main.c']
- applied: ['tests/kernel/context/src/main.c']
- restored: []

```
tests/kernel/context/src/main.c
<<<<<<<< SEARCH
	k_thread_priority_set(self_thread_id, self_thread_id->base.prio - 1);
	k_sleep(K_MSEC(50));

	zassert_equal(thread_evidence, 1,
		      "k_yield() yielded to a lower priority thread");
========
	k_thread_priority_set(self_thread_id, self_thread_id->base.prio - 1);
	/*
	 * Calling k_yield() should not result in switching to the helper,
	 * as the current thread now has a higher priority.
	 */
	k_yield();

	zassert_equal(thread_evidence, 1,
		      "k_yield() yielded to a lower priority thread");
>>>>>>>> REPLACE
```

## 5. [proposed] v3_c_net_buf_brace_34f8f2d_fx (c_syntax, c_typo_identifier)
- injected: ['tests/lib/net_buf/buf/src/main.c']
- applied: ['tests/lib/net_buf/buf/src/main.c']
- restored: []

```
tests/lib/net_buf/buf/src/main.c
<<<<<<<< SEARCH
	destroy_called++;
	zassert_equal(pool, &bufs_pool, "Invalid free pointer in buffer");
	net_buf_destory(buf);
}
========
	destroy_called++;
	zassert_equal(pool, &bufs_pool, "Invalid free pointer in buffer");
	net_buf_destroy(buf);
}
>>>>>>>> REPLACE
```

## 6. [proposed] v3new_runtime_linear_range_aa50e83 (runtime_crash, runtime_off_by_one)
- injected: ['include/zephyr/sys/linear_range.h']
- applied: ['include/zephyr/sys/linear_range.h']
- restored: []

```
include/zephyr/sys/linear_range.h
<<<<<<<< SEARCH
	if ((idx < r->min_idx) || (idx >= r->max_idx)) {
		return -EINVAL;
	}
========
	if ((idx < r->min_idx) || (idx > r->max_idx)) {
		return -EINVAL;
	}
>>>>>>>> REPLACE
```

## 7. [proposed] v3new_kconfig_nvs_qemu_161f758 (kconfig, kconfig_typo_depends)
- injected: ['subsys/kvss/nvs/Kconfig']
- applied: ['subsys/kvss/nvs/Kconfig']
- restored: []

```
subsys/kvss/nvs/Kconfig
<<<<<<<< SEARCH
	depends on FLSH
========
	depends on FLASH
>>>>>>>> REPLACE
```

## 8. [proposed] v3_dts_fwcfg_offbyone_x86_134e694 (runtime_crash, dts_reg_offbyone)
- injected: ['tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay']
- applied: ['tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay']
- restored: []

```
tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay
<<<<<<<< SEARCH
		reg = <0x512 0x18>;
========
		reg = <0x510 0x18>;
>>>>>>>> REPLACE
```

## 9. [b3] v3_kconfig_isotp_depends_577e42a (kconfig, kconfig_invert_depends)
- injected: ['subsys/canbus/isotp/Kconfig']
- applied: ['subsys/canbus/isotp/Kconfig']
- restored: []

```
subsys/canbus/isotp/Kconfig
<<<<<<<< SEARCH
	depends on !CAN
========
	depends on CAN
>>>>>>>> REPLACE
```

## 10. [b3] v3_thread_priority_swap_devicetree_init_order_30bef2a (runtime_crash, thread_priority_swap)
- injected: ['tests/lib/devicetree/devices/src/main.c']
- applied: ['tests/lib/devicetree/devices/src/main.c']
- restored: []

```
tests/lib/devicetree/devices/src/main.c
<<<<<<<< SEARCH
DEVICE_DT_DEFINE(TEST_I2C, dev_init, NULL,
		 NULL, NULL, POST_KERNEL, 20, NULL);
DEVICE_DT_DEFINE(TEST_DEVA, dev_init, NULL,
		 NULL, NULL, POST_KERNEL, 10, NULL);
========
DEVICE_DT_DEFINE(TEST_I2C, dev_init, NULL,
		 NULL, NULL, POST_KERNEL, 10, NULL);
DEVICE_DT_DEFINE(TEST_DEVA, dev_init, NULL,
		 NULL, NULL, POST_KERNEL, 20, NULL);
>>>>>>>> REPLACE
```

## 11. [b3] v3_c_kernel_queue_brace_bcf31c2 (c_syntax, c_remove_closing_brace)
- injected: ['tests/kernel/queue/src/main.c']
- applied: ['tests/kernel/queue/src/main.c', 'tests/kernel/queue/prj.conf']
- restored: ['tests/kernel/queue/prj.conf']

```
tests/kernel/queue/src/main.c
<<<<<<<< SEARCH
	return NULL;

ZTEST_SUITE(queue_api, NULL, queue_test_setup, NULL, NULL, NULL);
ZTEST_SUITE(queue_api_1cpu, NULL, queue_test_setup,
		ztest_simple_1cpu_before, ztest_simple_1cpu_after, NULL);
========
	return NULL;
}

ZTEST_SUITE(queue_api, NULL, queue_test_setup, NULL, NULL, NULL);
ZTEST_SUITE(queue_api_1cpu, NULL, queue_test_setup,
		ztest_simple_1cpu_before, ztest_simple_1cpu_after, NULL);
>>>>>>>> REPLACE

tests/kernel/queue/prj.conf
<<<<<<<< SEARCH
CONFIG_TEST_USERSPACE=y
========
CONFIG_TEST_USERSPACE=y
CONFIG_USERSPACE=y
>>>>>>>> REPLACE
```

## 12. [b3] v3_dts_longpress_phandle_bcf31c2_fx (dts, dts_break_phandle)
- injected: ['tests/subsys/input/longpress/boards/native_sim.overlay']
- applied: ['tests/subsys/input/longpress/boards/native_sim.overlay']
- restored: []

```
tests/subsys/input/longpress/boards/native_sim.overlay
<<<<<<<< SEARCH
	longpress: longpress {
		input = <&fake_input_devce>;
		compatible = "zephyr,input-longpress";
		input-codes = <INPUT_KEY_0>, <INPUT_KEY_1>;
		short-codes = <INPUT_KEY_A>, <INPUT_KEY_B>;
		long-codes = <INPUT_KEY_X>, <INPUT_KEY_Y>;
		long-delay-ms = <100>;
	};
========
	longpress: longpress {
		input = <&fake_input_device>;
		compatible = "zephyr,input-longpress";
		input-codes = <INPUT_KEY_0>, <INPUT_KEY_1>;
		short-codes = <INPUT_KEY_A>, <INPUT_KEY_B>;
		long-codes = <INPUT_KEY_X>, <INPUT_KEY_Y>;
		long-delay-ms = <100>;
	};
>>>>>>>> REPLACE
```

## 13. [b3] v3new_runtime_mem_slab_4ef3792 (runtime_crash, runtime_off_by_one)
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

## 14. [b3] v3new_c_syntax_sleep_134e694_fx (c_syntax, c_remove_semicolon)
- injected: ['tests/kernel/sleep/src/main.c']
- applied: ['tests/kernel/sleep/src/main.c']
- restored: []

```
tests/kernel/sleep/src/main.c
<<<<<<<< SEARCH
	k_sem_init(&test_thread_sem, 0, UINT_MAX);
	k_sem_init(&helper_thread_sem, 0, UINT_MAX);
	k_sem_init(&task_sem, 0, UINT_MAX)
}
========
	k_sem_init(&test_thread_sem, 0, UINT_MAX);
	k_sem_init(&helper_thread_sem, 0, UINT_MAX);
	k_sem_init(&task_sem, 0, UINT_MAX);
}
>>>>>>>> REPLACE
```

## 15. [b2] v3new_dts_gpio_hogs_label_3628e91 (dts, dts_break_phandle)
- injected: ['tests/drivers/gpio/gpio_hogs/boards/native_sim.overlay']
- applied: ['tests/drivers/gpio/gpio_hogs/boards/native_sim.overlay']
- restored: []

```
tests/drivers/gpio/gpio_hogs/boards/native_sim.overlay
<<<<<<<< SEARCH
		output-high-gpios = <&gpoi0 1 GPIO_ACTIVE_LOW>;
========
		output-high-gpios = <&gpio0 1 GPIO_ACTIVE_LOW>;
>>>>>>>> REPLACE
```

## 16. [b2] v3_kconfig_isotp_depends_577e42a (kconfig, kconfig_invert_depends)
- injected: ['subsys/canbus/isotp/Kconfig']
- applied: ['subsys/canbus/isotp/Kconfig']
- restored: []

```
The issue stems from a misconfigured dependency in the `subsys/canbus/isotp/Kconfig` file. The `ISOTP` option currently has a `depends on !CAN` clause, meaning it can only be enabled if `CAN` is *not* enabled. However, the test application `tests/subsys/canbus/isotp/implementation/prj.conf` explicitly enables both `CONFIG_CAN=y` and `CONFIG_ISOTP=y`.

This conflict causes `ISOTP` to be disabled, which in turn disables all `CONFIG_ISOTP_*` options. Consequently, the C/C++ compilation fails because `main.c` attempts to use `CONFIG_ISOTP_RX_BUF_COUNT` and `CONFIG_ISOTP_RX_BUF_SIZE`, which are undefined.

ISO-TP (ISO 15765-2) is a transport protocol *for* CAN, meaning it requires CAN to function. Therefore, the dependency should be `depends on CAN`, not `depends on !CAN`.

The fix involves changing the Kconfig dependency for `ISOTP` to correctly reflect its relationship with the `CAN` subsystem.

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

## 17. [b2] v3_c_kernel_queue_brace_bcf31c2 (c_syntax, c_remove_closing_brace)
- injected: ['tests/kernel/queue/src/main.c']
- applied: ['tests/kernel/queue/prj.conf', 'tests/kernel/queue/src/main.c']
- restored: ['tests/kernel/queue/prj.conf']

```
tests/kernel/queue/prj.conf
<<<<<<<< SEARCH
CONFIG_ZTEST=y
CONFIG_IRQ_OFFLOAD=y
CONFIG_TEST_USERSPACE=y
CONFIG_ZTEST_FATAL_HOOK=y
CONFIG_MAX_THREAD_BYTES=8
========
CONFIG_ZTEST=y
CONFIG_IRQ_OFFLOAD=y
CONFIG_TEST_USERSPACE=y
CONFIG_ZTEST_FATAL_HOOK=y
CONFIG_USERSPACE=y
CONFIG_MAX_THREAD_BYTES=8
>>>>>>>> REPLACE

tests/kernel/queue/src/main.c
<<<<<<<< SEARCH
	return NULL;

ZTEST_SUITE(queue_api, NULL, queue_test_setup, NULL, NULL, NULL);
ZTEST_SUITE(queue_api_1cpu, NULL, queue_test_setup,
		ztest_simple_1cpu_before, ztest_simple_1cpu_after, NULL);
========
	return NULL;
}

ZTEST_SUITE(queue_api, NULL, queue_test_setup, NULL, NULL, NULL);
ZTEST_SUITE(queue_api_1cpu, NULL, queue_test_setup,
		ztest_simple_1cpu_before, ztest_simple_1cpu_after, NULL);
>>>>>>>> REPLACE
```

## 18. [b2] v3new_dts_pwm_api_label_acc5365 (dts, dts_break_phandle)
- injected: ['tests/drivers/pwm/pwm_api/boards/native_sim.overlay']
- applied: ['tests/drivers/pwm/pwm_api/boards/native_sim.overlay']
- restored: []

```
tests/drivers/pwm/pwm_api/boards/native_sim.overlay
<<<<<<<< SEARCH
	aliases {
		pwm-test = &pwn0;
	};
========
	aliases {
		pwm-test = &pwm0;
	};
>>>>>>>> REPLACE
```

## 19. [b2] v3new_dts_adc_rescale_label_30bef2a (dts, dts_break_phandle)
- injected: ['tests/drivers/adc/adc_rescale/boards/native_sim.overlay']
- applied: ['tests/drivers/adc/adc_rescale/boards/native_sim.overlay']
- restored: []

```
tests/drivers/adc/adc_rescale/boards/native_sim.overlay
<<<<<<<< SEARCH
	sensor0: vd {
		compatible = "voltage-divider";
		io-channels = <&adc_0 0>;
		output-ohms = <50>;
		full-ohms = <100>;
	};
========
	sensor0: vd {
		compatible = "voltage-divider";
		io-channels = <&adc0 0>;
		output-ohms = <50>;
		full-ohms = <100>;
	};
>>>>>>>> REPLACE
```

## 20. [b2] v3_runtime_cbprintf_package_compare_buf_shrink_9be6ee7 (runtime_crash, runtime_buffer_shrink)
- injected: ['tests/lib/cbprintf_package/src/main.c']
- applied: ['tests/lib/cbprintf_package/src/main.c']
- restored: []

```
tests/lib/cbprintf_package/src/main.c
<<<<<<<< SEARCH
static char runtime_buf[512];
static char compare_buf[4];
========
static char runtime_buf[512];
static char compare_buf[256];
>>>>>>>> REPLACE
```


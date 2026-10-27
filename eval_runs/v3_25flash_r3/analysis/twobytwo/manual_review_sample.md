## 1. [proposed] v3new_c_syntax_sleep_134e694_fx (c_syntax, c_remove_semicolon)
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

## 2. [proposed] v3_api_substitute_event_reset_on_wait_5058917 (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/events/event_api/src/main.c']
- applied: ['tests/kernel/events/event_api/src/main.c']
- restored: []

```
tests/kernel/events/event_api/src/main.c
<<<<<<<< SEARCH
	k_sem_give(&sync_sem);
	k_sem_take(&receiver_sem, K_FOREVER);
	k_event_post(&test_event, 0x123);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0);
	zassert_true(test_event.events == 0x123);

	/*
	 * Sync point 2-2. Reset events before receive.
	 * Short timeout, k_event_wait(), no matches
	 */

	k_sem_give(&sync_sem);
	k_sem_take(&receiver_sem, K_FOREVER);
	k_event_post(&test_event, 0x248);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0);
	zassert_true(test_event.events == 0x248);

	/*
	 * Sync point 2-3. Reset events before receive.
	 * Short timeout, k_event_wait_all(), complete match
	 */

	k_sem_give(&sync_sem);
	k_sem_take(&receiver_sem, K_FOREVER);
	k_event_post(&test_event, 0x248021);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0x248001);
	zassert_true(test_event.events  == 0x248021);

	/*
	 * Sync point 2-4. Reset events before receive.
	 * Short timeout, k_event_wait(), partial match
	 */

	k_sem_give(&sync_sem);
	k_sem_take(&receiver_sem, K_FOREVER);
	k_event_post(&test_event, 0x123456);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0x123450);
	zassert_true(test_event.events  == 0x123456);
========
	k_sem_give(&sync_sem);
	k_sleep(DELAY); /* Give receiver time to pend */
	k_event_post(&test_event, 0x123);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0);
	zassert_true(test_event.events == 0x123);

	/*
	 * Sync point 2-2. Reset events before receive.
	 * Short timeout, k_event_wait(), no matches
	 */

	k_sem_give(&sync_sem);
	k_sleep(DELAY); /* Give receiver time to pend */
	k_event_post(&test_event, 0x248);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0);
	zassert_true(test_event.events == 0x248);

	/*
	 * Sync point 2-3. Reset events before receive.
	 * Short timeout, k_event_wait_all(), complete match
	 */

	k_sem_give(&sync_sem);
	k_sleep(DELAY); /* Give receiver time to pend */
	k_event_post(&test_event, 0x248021);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0x248001);
	zassert_true(test_event.events  == 0x248021);

	/*
	 * Sync point 2-4. Reset events before receive.
	 * Short timeout, k_event_wait(), partial match
	 */

	k_sem_give(&sync_sem);
	k_sleep(DELAY); /* Give receiver time to pend */
	k_event_post(&test_event, 0x123456);
	rv = k_sem_take(&receiver_sem, LONG_TIMEOUT);
	zassert_true(rv == 0);
	zassert_true(test_events == 0x123450);
	zassert_true(test_event.events  == 0x123456);
>>>>>>>> REPLACE
```

## 3. [proposed] v3_c_net_buf_brace_34f8f2d_fx (c_syntax, c_typo_identifier)
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

## 4. [proposed] v3_api_substitute_early_sleep_161f758 (runtime_crash, c_api_substitute)
- injected: ['tests/kernel/early_sleep/src/main.c']
- applied: ['tests/kernel/early_sleep/src/main.c']
- restored: []

```
tests/kernel/early_sleep/src/main.c
<<<<<<<< SEARCH
	start_time = k_cycle_get_32();
	k_yield();
	stop_time = k_cycle_get_32();
========
	start_time = k_cycle_get_32();
	k_sleep(K_TICKS(ticks));
	stop_time = k_cycle_get_32();
>>>>>>>> REPLACE
```

## 5. [proposed] v3_c_api_substitute_modem_pipe_6182bc0 (runtime_crash, c_api_substitute)
- injected: ['tests/subsys/modem/modem_pipe/src/main.c']
- applied: ['tests/subsys/modem/modem_pipe/src/main.c']
- restored: []

```
tests/subsys/modem/modem_pipe/src/main.c
<<<<<<<< SEARCH
	zassert_equal(atomic_get(&test_state), 0, "Unexpected state %u",
		      (uint32_t)atomic_get(&test_state));
	k_yield();
	zassert_equal(atomic_get(&test_state), BIT(TEST_MODEM_PIPE_EVENT_TRANSMIT_IDLE_BIT),
		      "Unexpected state %u", (uint32_t)atomic_get(&test_state));
========
	zassert_equal(atomic_get(&test_state), 0, "Unexpected state %u",
		      (uint32_t)atomic_get(&test_state));
	/* Allow the system workqueue to process the delayed notification */
	k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT);
	zassert_equal(atomic_get(&test_state), BIT(TEST_MODEM_PIPE_EVENT_TRANSMIT_IDLE_BIT),
		      "Unexpected state %u", (uint32_t)atomic_get(&test_state));
>>>>>>>> REPLACE
```

## 6. [proposed] v3new_kconfig_max17048_f914b6d (kconfig, kconfig_invert_depends)
- injected: ['drivers/fuel_gauge/max17048/Kconfig']
- applied: ['drivers/fuel_gauge/max17048/Kconfig', 'tests/drivers/fuel_gauge/max17048/CMakeLists.txt']
- restored: ['tests/drivers/fuel_gauge/max17048/CMakeLists.txt']

```
drivers/fuel_gauge/max17048/Kconfig
<<<<<<<< SEARCH
config MAX17048
	bool "MAX17048 Li-Po fuel gauge"
	default y
	depends on !DT_HAS_MAXIM_MAX17048_ENABLED
	select I2C
	help
	  Enable driver for the MAX17048 fuel gauge device.
========
config MAX17048
	bool "MAX17048 Li-Po fuel gauge"
	default y
	depends on DT_HAS_MAXIM_MAX17048_ENABLED
	select I2C
	help
	  Enable driver for the MAX17048 fuel gauge device.
>>>>>>>> REPLACE

tests/drivers/fuel_gauge/max17048/CMakeLists.txt
<<<<<<<< SEARCH
FILE(GLOB app_sources src/test_max17048.c)
target_sources(app PRIVATE ${app_sources})
========
FILE(GLOB app_sources src/test_max17048.c)
target_sources(app PRIVATE ${app_sources}
                           ${CMAKE_CURRENT_LIST_DIR}/../../../drivers/fuel_gauge/max17048/emul_max17048.c)
>>>>>>>> REPLACE
```

## 7. [proposed] v3new_dts_rtc_shell_compatible_3628e91 (dts, dts_remove_compatible)
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

## 8. [proposed] v3_compound_i2c_emul_kconfig_dts_daad7c5 (compound, kconfig_invert_depends+dts_corrupt_reg)
- injected: ['drivers/i2c/Kconfig.i2c_emul', 'tests/drivers/i2c/i2c_emul/boards/native_sim.overlay']
- applied: ['drivers/i2c/Kconfig.i2c_emul']
- restored: []

```
drivers/i2c/Kconfig.i2c_emul
<<<<<<<< SEARCH
config I2C_EMUL
	bool "I2C emulator"
	default y
	depends on !DT_HAS_ZEPHYR_I2C_EMUL_CONTROLLER_ENABLED
	depends on EMUL
	help
	  Enable the I2C emulator driver. This is a fake driver in that it
	  does not talk to real hardware. Instead it talks to emulation
	  drivers that pretend to be devices on the emulated I2C bus. It is
	  used for testing drivers for I2C devices.
========
config I2C_EMUL
	bool "I2C emulator"
	default y
	depends on DT_HAS_ZEPHYR_I2C_EMUL_CONTROLLER_ENABLED
	depends on EMUL
	help
	  Enable the I2C emulator driver. This is a fake driver in that it
	  does not talk to real hardware. Instead it talks to emulation
	  drivers that pretend to be devices on the emulated I2C bus. It is
	  used for testing drivers for I2C devices.
>>>>>>>> REPLACE
```


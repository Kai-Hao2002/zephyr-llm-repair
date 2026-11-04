#!/bin/bash
# v3 pilot smoke test: per commit, build/run hello_world (native_sim, qemu_cortex_m3) and a small ztest on qemu_x86/qemu_riscv32
for c in 0bce58b4feb6951cc089ebf557884fe0d661fef4 b0ed2b52093a5f19181b6b47021e1c18877f1e0c bcf31c20f01f11609dbff940483d5172d7897691 5058917ea61b9d075f24c0dfeb89fdb95a405d52; do
  echo "=== COMMIT $c"
  docker run --rm --cpus=4 --memory=4g --name v3smoke_${c:0:8} zephyr-sandbox bash -c "
    cd /zephyrproject/zephyr && git checkout -q $c && git log -1 --format='%h %cs' &&
    cd samples/hello_world &&
    (timeout 300 west build -b native_sim -d /tmp/b1 -p always -t run . > /tmp/l1 2>&1; grep -m1 'Hello World' /tmp/l1 && echo 'RESULT native_sim hello OK' || { echo 'RESULT native_sim hello FAIL'; tail -25 /tmp/l1; })
    (timeout 300 west build -b qemu_cortex_m3 -d /tmp/b2 -p always -t run . > /tmp/l2 2>&1; grep -m1 'Hello World' /tmp/l2 && echo 'RESULT qemu_cortex_m3 hello OK' || { echo 'RESULT qemu_cortex_m3 hello FAIL'; tail -25 /tmp/l2; })
    cd /zephyrproject/zephyr/tests/lib/ringbuffer &&
    for b in qemu_x86 qemu_riscv32; do (timeout 600 west build -b \$b -d /tmp/b_\$b -p always -t run . > /tmp/l_\$b 2>&1; grep -m1 'PROJECT EXECUTION SUCCESSFUL' /tmp/l_\$b && echo \"RESULT \$b ringbuffer OK\" || { echo \"RESULT \$b ringbuffer FAIL\"; tail -25 /tmp/l_\$b; }); done
  "
done
echo SMOKE_DONE

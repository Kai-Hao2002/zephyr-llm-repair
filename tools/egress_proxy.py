# tools/egress_proxy.py
"""
OpenHands baseline 的對外白名單代理 (2026-10-18)：只接受 HTTP CONNECT 到白名單上的 host:port (預設只有
LLM API)，其他一律回 403。OpenHands V1 的 LLM 呼叫由容器內的 agent-server 發出，而容器的 iptables 只放行
連到這個代理，所以 agent 在容器裡連不到任何其他網站 (例如真實 bug 的上游修正 commit)。

Allowlist egress proxy for the OpenHands baseline (2026-10-18): accepts HTTP CONNECT only to allowlisted
host:port pairs (by default just the LLM API) and answers 403 otherwise. In OpenHands V1 the agent-server inside
the container makes the LLM calls, and the container's iptables only allow traffic to this proxy, so the agent
cannot reach any other site (e.g. the upstream fix commits of the real-bug set).

Usage: start_proxy(allowed={"generativelanguage.googleapis.com:443"}) -> (port, stop_fn); or run as a script.
"""
import asyncio
import logging
import threading
from typing import Callable, Iterable, Set, Tuple

logger = logging.getLogger(__name__)
DEFAULT_ALLOWED = {"generativelanguage.googleapis.com:443"}


async def _pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


def _handler(allowed: Set[str], log: list):
    async def handle(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        except Exception:  # noqa: BLE001
            writer.close()
            return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT" or parts[1].lower() not in allowed:
            log.append(("denied", line[:200]))
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        host, port = parts[1].rsplit(":", 1)
        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout=30)
        except Exception as e:  # noqa: BLE001
            log.append(("upstream_error", f"{parts[1]}: {e}"))
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        log.append(("allowed", parts[1]))
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    return handle


def start_proxy(allowed: Iterable[str] = DEFAULT_ALLOWED, host: str = "127.0.0.1") -> Tuple[int, Callable[[], list]]:
    """在背景執行緒啟動代理，回傳 (port, stop)；stop() 停止並回傳連線紀錄 [(allowed|denied|upstream_error, target)]。
    Starts the proxy in a background thread; returns (port, stop); stop() returns the connection log."""
    allowed_set = {a.lower() for a in allowed}
    log: list = []
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state = {}

    def run():
        asyncio.set_event_loop(loop)
        server = loop.run_until_complete(asyncio.start_server(_handler(allowed_set, log), host, 0))
        state["server"] = server
        state["port"] = server.sockets[0].getsockname()[1]
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(10)

    def stop():
        def _close():
            state["server"].close()
            loop.stop()
        loop.call_soon_threadsafe(_close)
        t.join(5)
        return log

    return state["port"], stop


if __name__ == "__main__":
    import time
    port, stop = start_proxy()
    print(f"egress proxy on 127.0.0.1:{port}, allowed={sorted(DEFAULT_ALLOWED)}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print(stop())

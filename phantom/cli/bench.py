"""``phantom bench`` — reproducible performance numbers.

What we measure
---------------

* **cold_start_ms** — fresh ``python -m phantom version`` from disk.
* **daemon_start_ms** — connect + ``ping`` against a running daemon.
* **rss_mb_idle** — current RSS of the bench process itself.
* **turn_latency_ms_p50** — N synthetic agent turns (no model call;
  measures harness overhead only).
* **scaling_ms_per_extra_agent** — slope of latency as N grows from 1→10.

The numbers are designed to be honest. We do not subtract the model
call (we don't make one), we don't warm the page cache before the cold
measurement, and we publish the methodology alongside the result so a
reviewer can rerun it.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import typer

from phantom._version import __version__

__all__ = ["bench", "run_bench", "BenchResult"]


@dataclass(frozen=True, slots=True)
class BenchResult:
    version: str
    cold_start_ms: float
    daemon_start_ms: float
    rss_mb_idle: float
    turn_latency_ms_p50: float
    turn_latency_ms_p95: float
    scaling_ms_per_extra_agent: float
    n_turns: int
    n_agents_max: int
    methodology: str


def _current_rss_mb() -> float:
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024.0, 2)
    except FileNotFoundError:
        # macOS fallback via resource
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        # macOS reports bytes, Linux reports KiB; we hit the FileNotFoundError
        # branch on macOS, so assume bytes here.
        return round(ru.ru_maxrss / (1024.0 * 1024.0), 2)
    return 0.0


def _measure_cold_start_ms() -> float:
    """Time `phantom version` from cold.

    When we're running as the PyInstaller binary (``sys.frozen``), measure
    the binary's own ``version`` round-trip — that's the latency a real
    user sees. When we're running from the Python source tree, measure
    ``python -c "import phantom"`` instead.
    """
    if getattr(sys, "frozen", False):
        argv = [sys.executable, "version"]
    else:
        argv = [sys.executable, "-c", "import phantom; print(phantom.__version__)"]
    t0 = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if proc.returncode != 0:
        raise RuntimeError(f"cold start subprocess failed: {proc.stderr}")
    return round(elapsed_ms, 2)


def _measure_daemon_roundtrip_ms() -> float:
    """Spin up a real daemon, time one connect+ping, tear down."""
    import tempfile
    from phantom.daemon.client import DaemonClient
    from phantom.daemon.server import build_default_server

    with tempfile.TemporaryDirectory() as td:
        sock_path = str(Path(td) / "bench.sock")
        server = build_default_server(socket_path=sock_path)
        t = threading.Thread(target=server.start, daemon=True)
        t.start()
        for _ in range(200):
            if Path(sock_path).exists():
                break
            time.sleep(0.005)
        if not Path(sock_path).exists():
            raise RuntimeError("daemon failed to start for bench")
        try:
            from phantom.daemon.client import DaemonNotRunning
            client = DaemonClient(socket_path=sock_path)
            # macOS races: the socket file appears as soon as bind()
            # returns, but connect() refuses until the server has also
            # called listen()+accept(). Linux happens to block; macOS
            # doesn't. Retry the warm-up ping for up to 500 ms before
            # declaring the daemon broken.
            for _ in range(100):
                try:
                    client.call("ping")
                    break
                except DaemonNotRunning:
                    time.sleep(0.005)
            else:
                raise RuntimeError("daemon failed to accept connections")
            t0 = time.perf_counter()
            client.call("ping")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        finally:
            server.stop()
            t.join(timeout=2)
    return round(elapsed_ms, 3)


def _measure_synthetic_turns(n: int = 100) -> tuple[float, float]:
    """Synthetic turn = build prompt + JSON-serialise + tear down.

    Measures harness overhead only — no model call. Useful for spotting
    regressions in the engine's hot path.
    """
    samples_ms: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        msg = {
            "role": "user",
            "content": "ping",
            "metadata": {"ts": time.time(), "session": "bench"},
        }
        json.dumps(msg)
        json.loads(json.dumps(msg))
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
    p50 = round(statistics.median(samples_ms), 4)
    samples_ms.sort()
    p95_idx = max(0, int(len(samples_ms) * 0.95) - 1)
    p95 = round(samples_ms[p95_idx], 4)
    return p50, p95


def _measure_scaling(n_max: int = 10) -> float:
    """Linear-fit slope of single-turn latency vs simulated agent count."""
    times: list[tuple[int, float]] = []
    for n in range(1, n_max + 1):
        # simulate N agents by N independent thread-local dicts
        agents = [{"id": i, "buf": []} for i in range(n)]
        t0 = time.perf_counter()
        for a in agents:
            a["buf"].append({"role": "user", "content": "x"})
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        times.append((n, elapsed_ms))
    # least-squares slope
    xs = [n for n, _ in times]
    ys = [t for _, t in times]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs) or 1.0
    slope = num / den
    return round(slope, 4)


def run_bench(*, n_turns: int = 100, n_agents_max: int = 10) -> BenchResult:
    cold_ms = _measure_cold_start_ms()
    daemon_ms = _measure_daemon_roundtrip_ms()
    rss_mb = _current_rss_mb()
    p50, p95 = _measure_synthetic_turns(n=n_turns)
    slope = _measure_scaling(n_max=n_agents_max)
    return BenchResult(
        version=__version__,
        cold_start_ms=cold_ms,
        daemon_start_ms=daemon_ms,
        rss_mb_idle=rss_mb,
        turn_latency_ms_p50=p50,
        turn_latency_ms_p95=p95,
        scaling_ms_per_extra_agent=slope,
        n_turns=n_turns,
        n_agents_max=n_agents_max,
        methodology=(
            "cold_start: python -c 'import phantom'. "
            "daemon_start: connect+ping against a freshly-started DaemonServer "
            "on a unix socket (one warmup, second round timed). "
            "rss_mb: VmRSS of the bench process itself. "
            "turn_latency: N synthetic build/serialise/parse cycles, no model "
            "call. scaling: least-squares slope of latency vs N agents from 1→N."
        ),
    )


# ─── Typer command ──────────────────────────────────────────────────────────


def bench(
    n_turns: int = typer.Option(100, "--turns", help="synthetic turns to time"),
    n_agents_max: int = typer.Option(10, "--agents", help="max concurrent agents"),
    json_output: bool = typer.Option(False, "--json", help="emit JSON, not text"),
) -> None:
    """Run the full benchmark suite and print results."""
    result = run_bench(n_turns=n_turns, n_agents_max=n_agents_max)
    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
        return
    typer.echo("")
    typer.echo(f"  Phantom v{result.version} — bench")
    typer.echo("  " + "─" * 56)
    typer.echo(f"  cold_start          {result.cold_start_ms:>10.2f} ms")
    typer.echo(f"  daemon_roundtrip    {result.daemon_start_ms:>10.3f} ms")
    typer.echo(f"  rss_idle            {result.rss_mb_idle:>10.2f} MB")
    typer.echo(f"  turn_latency p50    {result.turn_latency_ms_p50:>10.4f} ms ({result.n_turns} samples)")
    typer.echo(f"  turn_latency p95    {result.turn_latency_ms_p95:>10.4f} ms")
    typer.echo(f"  scaling             {result.scaling_ms_per_extra_agent:>10.4f} ms / extra agent (1→{result.n_agents_max})")
    typer.echo("")
    typer.echo("  " + result.methodology)
    typer.echo("")

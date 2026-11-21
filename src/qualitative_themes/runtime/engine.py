"""Start, check and stop the bundled local AI engine (Ollama) for theme naming.

The app runs its own private copy of Ollama:

* bound to loopback only, on a private port (default 11500), so it never clashes
  with an Ollama the researcher installed themselves and is unreachable from
  other machines;
* with ``OLLAMA_MODELS`` pointing at the app's data folder, never ``~/.ollama``;
* started when the app opens and stopped when it closes, together with the
  "runner" processes it starts to hold a model (about 5–7 GB each for an 8B model);
* leftovers from a session that didn't close normally (a crash, a stopped
  terminal) are shut down the next time the app starts. Only processes running
  from the app's own engine folder are touched, never an Ollama the researcher
  installed.

When no bundled engine exists (a source checkout), the app falls back to an
already-running Ollama on the standard port, which keeps development working.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from qualitative_themes.runtime.paths import engine_binary, engine_models_dir, logs_dir

PRIVATE_PORT = int(os.environ.get("DOC_TOPIC_MODEL_ENGINE_PORT", "11500"))
SYSTEM_URL = "http://127.0.0.1:11434"


def _get_json(url: str, timeout: float = 2.0):
    # Loopback only: proxies are bypassed so nothing is routed off the machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def engine_responds(base_url: str, timeout: float = 2.0) -> bool:
    """True if an Ollama at `base_url` answers /api/tags. Never raises."""
    try:
        data = _get_json(base_url.rstrip("/") + "/api/tags", timeout)
        return isinstance(data, dict) and isinstance(data.get("models"), list)
    except (OSError, ValueError, urllib.error.URLError):
        return False


@dataclass(frozen=True)
class EngineProcess:
    """A running process from the bundled engine's folder: the server, or a model runner.

    Older Ollama runs models as `ollama runner …`; newer versions start a
    `llama-server` from the engine's lib folder. Both count.
    """
    pid: int
    parent: int
    command: str

    @property
    def is_server(self) -> bool:
        return "serve" in self.command.split()


def _hidden():
    """creationflags that keep Windows from flashing a console window."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def engine_processes(binary: Path) -> list[EngineProcess]:
    """Processes running from `binary`'s folder (the server and its runners). Empty if they can't be listed."""
    folder = str(binary.parent)
    try:
        if sys.platform == "win32":
            path = folder.replace("'", "''").rstrip("\\") + "\\"
            script = ("Get-CimInstance Win32_Process | "
                      f"Where-Object {{ $_.ExecutablePath -and $_.ExecutablePath.StartsWith('{path}', "
                      "[StringComparison]::OrdinalIgnoreCase) } | "
                      "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }")
            output = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                    capture_output=True, text=True, timeout=20, creationflags=_hidden()).stdout
            rows = [line.split("\t", 2) for line in output.splitlines() if line.strip()]
        else:
            output = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True,
                                    timeout=20).stdout
            rows = [line.split(None, 2) for line in output.splitlines()]
            rows = [row for row in rows if len(row) == 3 and row[2].split()[0].startswith(folder + os.sep)]
        return [EngineProcess(int(pid), int(parent), command) for pid, parent, command in
                (row + [""] * (3 - len(row)) for row in rows)]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def kill_process_tree(pid: int) -> None:
    """End a process and everything it started. Never raises."""
    try:
        if sys.platform == "win32":
            # terminate() on Windows ends only the one process, leaving the model runners behind.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20,
                           creationflags=_hidden())
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


@dataclass
class EngineStatus:
    """Snapshot of the engine for the UI. `models` is filled by callers that list them."""
    state: str  # "running", "starting", "stopped", "missing", "failed"
    base_url: str | None = None
    source: str | None = None  # "bundled" or "system"
    message: str = ""
    models: list = field(default_factory=list)


class LocalEngine:
    """One engine per app session. Thread-safe; ``start`` may run on a worker thread."""

    def __init__(self, binary: Path | None = None, models_dir: Path | None = None,
                 port: int = PRIVATE_PORT, popen=subprocess.Popen, responds=engine_responds,
                 processes=engine_processes, kill=kill_process_tree):
        self._binary = binary
        self._models_dir = models_dir
        self.port = port
        self._popen = popen
        self._responds = responds
        self._processes, self._kill = processes, kill
        self._process = None
        self._lock = threading.Lock()
        self.status = EngineStatus("stopped")

    @property
    def private_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def base_url(self) -> str | None:
        return self.status.base_url if self.status.state == "running" else None

    def environment(self, models_dir: Path) -> dict:
        """Environment for `ollama serve`: private port, app-owned model store, no proxies, no cloud."""
        env = dict(os.environ)
        env.update({
            "OLLAMA_HOST": f"127.0.0.1:{self.port}",
            "OLLAMA_MODELS": str(models_dir),
            "OLLAMA_NOPRUNE": "1",
            "OLLAMA_NO_CLOUD": "1",
            "OLLAMA_KEEP_ALIVE": "5m",
        })
        # Never inherit a proxy: the engine only talks to this computer.
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env.pop(key, None)
        return env

    def start(self, timeout: float = 30.0) -> EngineStatus:
        """Make sure an engine is available and return its status.

        Order: already running -> no bundled binary (use a system Ollama on 11434
        if one answers, else 'missing') -> a previous session's engine still on
        our port (reuse it) -> launch `ollama serve` and wait up to `timeout`.
        Post: status.state is 'running', 'missing' or 'failed'; never raises.
        Blocks while waiting, so the GUI calls it from a worker thread.
        """
        with self._lock:
            if self.status.state == "running" and self._responds(self.status.base_url):
                return self.status
            binary = self._binary if self._binary is not None else engine_binary()
            if binary is None:
                if self._responds(SYSTEM_URL):
                    self.status = EngineStatus("running", SYSTEM_URL, "system",
                                               "Using the Ollama already running on this computer.")
                else:
                    self.status = EngineStatus("missing", message="The local AI engine is not installed. "
                                               "Keyword theme names will be used.")
                return self.status
            if self._responds(self.private_url):
                # A previous session's engine is still alive; reuse it, but not its stranded runners.
                self.clear_leftovers(binary, keep_servers=True)
                self.status = EngineStatus("running", self.private_url, "bundled", "Local AI engine running.")
                return self.status
            self.clear_leftovers(binary, keep_servers=False)
            models_dir = self._models_dir or engine_models_dir()
            log = open(logs_dir() / "engine.log", "ab")
            flags = 0
            if sys.platform == "win32":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._process = self._popen([str(binary), "serve"], env=self.environment(models_dir),
                                            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                            creationflags=flags)
            except OSError as exc:
                log.close()
                self.status = EngineStatus("failed", message=f"The local AI engine could not start: {exc}")
                return self.status
            self.status = EngineStatus("starting", self.private_url, "bundled", "Starting the local AI engine…")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._responds(self.private_url):
                with self._lock:
                    self.status = EngineStatus("running", self.private_url, "bundled", "Local AI engine running.")
                    return self.status
            if self._process is not None and self._process.poll() is not None:
                break
            time.sleep(0.25)
        with self._lock:
            self.stop_locked()
            self.status = EngineStatus("failed", message="The local AI engine did not respond. "
                                       "Choose Check & repair, or keep using keyword theme names.")
            return self.status

    def clear_leftovers(self, binary: Path, keep_servers: bool) -> int:
        """End engine processes from `binary` that nothing is using. Returns how many were ended.

        keep_servers=True keeps running servers (one is about to be reused) and
        ends only runners whose server is gone; False ends everything from `binary`.
        A second copy of the app sharing the engine would lose it; that's not a
        supported way to run the app.
        """
        found = self._processes(binary)
        servers = {p.pid for p in found if p.is_server} if keep_servers else set()
        ended = 0
        for process in found:
            if process.pid in servers or process.parent in servers:
                continue
            if process.is_server or process.parent not in {p.pid for p in found}:
                self._kill(process.pid)  # a whole server tree, or a runner left on its own
                ended += 1
        if ended:
            print(f"Ended {ended} leftover engine process(es) from an earlier session.", file=sys.stderr)
        return ended

    def stop_locked(self):
        """End our process and everything it started (its runners). Caller holds self._lock."""
        process, self._process = self._process, None
        if process is None:
            return
        self._kill(process.pid)
        try:
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def stop(self):
        """Stop the engine we started or reused, with its runners. A system Ollama is never stopped.

        Safe to call twice.
        """
        with self._lock:
            self.stop_locked()
            if self.status.source == "bundled":
                binary = self._binary if self._binary is not None else engine_binary()
                if binary is not None:
                    self.clear_leftovers(binary, keep_servers=False)  # a reused server, stray runners
                self.status = EngineStatus("stopped")


_engine: LocalEngine | None = None


def get_engine() -> LocalEngine:
    """The session's LocalEngine (created on first use)."""
    global _engine
    if _engine is None:
        _engine = LocalEngine()
    return _engine


def set_engine(engine: LocalEngine | None):
    """Test hook: replace the session engine."""
    global _engine
    _engine = engine


def current_base_url() -> str:
    """URL theme naming should call: the running engine, else the standard port."""
    env = os.environ.get("DOC_TOPIC_MODEL_ENGINE_URL")
    if env:
        return env
    engine = _engine
    if engine is not None and engine.base_url:
        return engine.base_url
    return SYSTEM_URL

"""Run an agent CLI as a subprocess and stream its stdout lines.

Uses subprocess.Popen + reader threads instead of asyncio subprocesses so it works under any
Windows event loop policy (uvicorn may install the Selector loop, which lacks subprocess support).
Arguments are passed as a list (no shell), so prompts can't be interpreted as shell syntax.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

_IS_WIN = sys.platform == "win32"


@dataclass
class CliOutcome:
    exit_code: int | None
    stderr_tail: str
    timed_out: bool
    cancelled: bool
    duration_ms: int


def resolve_executable(name_or_path: str) -> str | None:
    if not name_or_path:
        return None
    p = Path(name_or_path)
    if p.is_file():
        return str(p)
    return shutil.which(name_or_path)


def _kill_tree(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    if _IS_WIN:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()


async def run_version(executable: str, args: list[str] | None = None, timeout: float = 20) -> tuple[int | None, str]:
    """Run `<exe> --version` style probe; returns (exit_code, combined output)."""

    def _go():
        try:
            r = subprocess.run(
                [executable, *(args or ["--version"])],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if _IS_WIN else 0,
            )
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except OSError as e:
            return None, str(e)

    return await asyncio.to_thread(_go)


async def run_streaming(
    argv: list[str],
    *,
    cwd: Path,
    on_line: Callable[[str], Awaitable[None]],
    cancel: asyncio.Event,
    timeout_sec: float,
    stdin_text: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> CliOutcome:
    env = os.environ.copy()
    # Some CLIs trust an inherited $PWD over the real cwd; pin it to the task workspace.
    env["PWD"] = str(cwd)
    env.update(env_extra or {})
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if _IS_WIN else 0,
    )
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    stderr_tail: deque[str] = deque(maxlen=40)

    def _read_stdout():
        try:
            for line in proc.stdout:
                loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\r\n"))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    def _read_stderr():
        for line in proc.stderr:
            stderr_tail.append(line.rstrip("\r\n"))

    def _write_stdin():
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except OSError:
            pass

    threading.Thread(target=_read_stdout, daemon=True).start()
    threading.Thread(target=_read_stderr, daemon=True).start()
    if stdin_text is not None:
        threading.Thread(target=_write_stdin, daemon=True).start()

    timed_out = cancelled = False
    deadline = started + timeout_sec
    while True:
        if cancel.is_set():
            cancelled = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            line = await asyncio.wait_for(queue.get(), timeout=min(0.5, remaining))
        except TimeoutError:
            continue
        if line is None:
            break
        if line.strip():
            await on_line(line)

    if timed_out or cancelled:
        await asyncio.to_thread(_kill_tree, proc)
    try:
        exit_code = await asyncio.to_thread(proc.wait, 10)
    except subprocess.TimeoutExpired:
        await asyncio.to_thread(_kill_tree, proc)
        exit_code = None
    return CliOutcome(
        exit_code=exit_code,
        stderr_tail="\n".join(stderr_tail),
        timed_out=timed_out,
        cancelled=cancelled,
        duration_ms=int((time.monotonic() - started) * 1000),
    )

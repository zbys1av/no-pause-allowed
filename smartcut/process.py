"""Owned child processes, bounded diagnostics and interruptible blocking reads."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from .errors import AppError


_spawn_lock = threading.Lock()


def spawn(args, **kwargs):
    # PyInstaller changes Windows DLL search globally for its Qt libraries. An
    # FFmpeg child must inherit the normal OS search, not Qt's private DLLs.
    # Restore immediately after CreateProcess so the GUI keeps its own search.
    frozen_windows = os.name == "nt" and getattr(sys, "frozen", False)
    with _spawn_lock:
        if frozen_windows:
            import ctypes
            set_directory = ctypes.windll.kernel32.SetDllDirectoryW
            set_directory.argtypes = [ctypes.c_wchar_p]
            set_directory.restype = ctypes.c_int
            if not set_directory(None):
                raise OSError("Could not initialize the media process DLL search")
        try:
            return subprocess.Popen(args, **kwargs)
        finally:
            if frozen_windows:
                set_directory(str(sys._MEIPASS))


class Cancelled(Exception):
    pass


class Job:
    def __init__(self):
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled("Operation cancelled")


def tool(name):
    variable = "SMARTCUT_" + name.upper()
    explicit = os.environ.get(variable)
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    filename = name + (".exe" if os.name == "nt" else "")
    options = [explicit] if explicit else [str(base / "tools" / filename), shutil.which(name)]
    for option in options:
        if option and Path(option).is_file():
            return str(option)
    raise AppError("error.tool", tool=name)


@contextmanager
def child(args, job, **kwargs):
    job.check()
    try:
        process = spawn(args, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **kwargs)
    except OSError as exc:
        raise AppError("error.start", str(exc), tool=Path(args[0]).name) from exc
    stopped = threading.Event()

    def watch():
        while not stopped.wait(.08):
            if job.cancelled.is_set():
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                except OSError:
                    pass
                return
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        yield process
        job.check()
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
        watcher.join(timeout=3)
        for pipe in (process.stdout, process.stderr):
            if pipe:
                pipe.close()


def run(args, job, duration=0, progress=lambda *_: None, stage="Processing"):
    """FFmpeg progress comes from output media timestamps, never wall-clock guesses."""
    # Drain progress via independent regular-file handles. This avoids Windows
    # anonymous-pipe backpressure in GUI executables while keeping live, real
    # timestamps. Only new bytes are read; diagnostics are bounded in memory.
    with tempfile.TemporaryDirectory(prefix="smartcut-process-") as directory:
        output_path, error_path = Path(directory)/"progress.log", Path(directory)/"errors.log"
        with output_path.open("wb") as output, error_path.open("wb") as errors:
            with child(args, job, stdout=output, stderr=errors) as process:
                with output_path.open("rb") as updates:
                    pending = b""
                    while True:
                        pending += updates.read()
                        lines = pending.split(b"\n")
                        pending = lines.pop()
                        for raw in lines:
                            line = raw.decode(errors="replace").strip()
                            if line.startswith("out_time_us=") and duration:
                                try:
                                    progress(min(.999, max(0., int(line.split("=")[1])/1e6/duration)), stage)
                                except ValueError:
                                    pass
                        code = process.poll()
                        if code is not None:
                            break
                        job.check()
                        job.cancelled.wait(.1)
                job.check()
        if code:
            with error_path.open("rb") as errors:
                errors.seek(0, os.SEEK_END)
                errors.seek(max(0, errors.tell()-16000))
                tail = errors.read().decode(errors="replace").splitlines()[-40:]
            raise AppError("error.ffmpeg", str(stage)+"\n"+"\n".join(tail))
    progress(1., stage)

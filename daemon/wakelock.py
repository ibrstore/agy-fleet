"""
Cross-Platform Wake-Lock.
Prevents PC / Laptop from sleeping or suspending during active execution phases.
Works on Linux (systemd-inhibit / DBus), Windows (SetThreadExecutionState), and macOS (caffeinate).
"""
import contextlib
import platform
import subprocess
import sys
from typing import Optional


class WakeLock:
    def __init__(self, reason: str = "agy-fleet active execution"):
        self.reason = reason
        self.os_type = platform.system().lower()
        self._proc: Optional[subprocess.Popen] = None

    def acquire(self) -> None:
        if self.os_type == "windows":
            try:
                import ctypes
                # ES_CONTINUOUS = 0x80000000
                # ES_SYSTEM_REQUIRED = 0x00000001
                # ES_DISPLAY_REQUIRED = 0x00000002
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            except Exception:
                pass

        elif self.os_type == "darwin":
            try:
                # Spawn caffeinate process preventing sleep
                self._proc = subprocess.Popen(
                    ["caffeinate", "-d", "-i", "-m"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

        elif self.os_type == "linux":
            # On Linux, systemd-inhibit can wrap a dummy sleep or process
            try:
                self._proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", f"--why={self.reason}", "sleep", "86400"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

    def release(self) -> None:
        if self.os_type == "windows":
            try:
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:
                pass

        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            finally:
                self._proc = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    async def __aenter__(self):
        self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()

"""
Antigravity CLI (agy) Adapter.
Provides an isolated, non-interactive/headless interface to invoke local `agy`.
Decoupled so CLI changes can be accommodated without affecting the rest of the daemon.
"""
import asyncio
import json
import shutil
import sys
from typing import Dict, Any, Optional
from shared.redactor import Redactor


class AgyAdapter:
    def __init__(self, binary_path: str = "agy", redactor: Optional[Redactor] = None):
        self.binary_path = binary_path
        self.redactor = redactor or Redactor()
        self._resolve_binary_path()

    def _resolve_binary_path(self) -> None:
        """Resolve agy binary path if not in current standard PATH."""
        if shutil.which(self.binary_path):
            return

        candidates = []
        if sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE", "")
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            candidates = [
                os.path.join(local_appdata, "Programs", "agy", "agy.exe"),
                os.path.join(local_appdata, "agy", "bin", "agy.exe"),
                os.path.join(user_profile, ".local", "bin", "agy.exe"),
                os.path.join(user_profile, "AppData", "Roaming", "npm", "agy.cmd"),
                r"C:\Program Files\agy\agy.exe",
            ]
        else:
            home = os.path.expanduser("~")
            candidates = [
                "/usr/local/bin/agy",
                os.path.join(home, ".local", "bin", "agy"),
                os.path.join(home, "bin", "agy"),
                "/opt/agy/bin/agy",
            ]

        for cand in candidates:
            if os.path.isfile(cand) and (sys.platform == "win32" or os.access(cand, os.X_OK)):
                self.binary_path = cand
                break

    def is_available(self) -> bool:
        """Check if agy binary is available in PATH or specified location."""
        self._resolve_binary_path()
        return (shutil.which(self.binary_path) is not None) or (os.path.isfile(self.binary_path) and (sys.platform == "win32" or os.access(self.binary_path, os.X_OK)))

    async def get_version(self) -> str:
        """Detect agy installation presence/version."""
        if not self.is_available():
            return "not_installed"
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary_path, "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if b"Usage of agy:" in stdout:
                return "antigravity-cli (ready)"
            return "detected"
        except Exception as e:
            return f"error: {str(e)}"

    async def run_prompt(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        timeout_sec: int = 60,
        output_format: str = "json",
        skip_permissions: bool = True,
        effort: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a non-interactive prompt through agy.
        Returns structured dictionary:
        {
            "status": "SUCCESS" | "ERROR",
            "conversation_id": "...",
            "response": "...",
            "error": "...",
            "raw_output": "..."
        }
        """
        if not self.is_available():
            return {
                "status": "ERROR",
                "conversation_id": conversation_id or "",
                "response": "",
                "error": f"Antigravity CLI binary '{self.binary_path}' not found on this node.",
                "raw_output": ""
            }

        # Build CLI command arguments
        cmd = [self.binary_path, "-p", prompt, "--output-format", output_format]

        # Use fast conversational model by default to prevent 2-minute thinking stall
        effective_model = model or "gemini-3.8-flash-low"
        cmd.extend(["--model", effective_model])
        cmd.append("--disable-slash-commands")

        # Use print-timeout 0s so agy's thinking is not cut off prematurely;
        # Python's asyncio.wait_for handles overall safety timeout.
        cmd.extend(["--print-timeout", "0s"])

        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if conversation_id:
            cmd.extend(["--conversation", conversation_id])

        if effort and effort in ["low", "medium", "high", "xhigh", "max"]:
            cmd.extend(["--effort", effort])

        try:
            extra_kwargs = {"stdin": asyncio.subprocess.DEVNULL}
            if sys.platform == "win32":
                import subprocess
                extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **extra_kwargs
            )

            try:
                # Add 5s buffer over print-timeout
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_sec + 5.0
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return {
                    "status": "ERROR",
                    "conversation_id": conversation_id or "",
                    "response": "",
                    "error": f"agy execution timed out after {timeout_sec}s",
                    "raw_output": ""
                }

            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")

            # Redact outputs before parsing or returning
            stdout_clean = self.redactor.redact(stdout_str)
            stderr_clean = self.redactor.redact(stderr_str)

            if output_format == "json":
                # Look for JSON payload in stdout lines
                parsed = self._extract_json_response(stdout_clean)
                if parsed:
                    return parsed

            # Fallback if text output or parsing failed
            status = "SUCCESS" if proc.returncode == 0 else "ERROR"
            return {
                "status": status,
                "conversation_id": conversation_id or "",
                "response": stdout_clean.strip(),
                "error": stderr_clean.strip() if proc.returncode != 0 else "",
                "raw_output": stdout_clean
            }

        except Exception as e:
            return {
                "status": "ERROR",
                "conversation_id": conversation_id or "",
                "response": "",
                "error": f"Subprocess exception: {str(e)}",
                "raw_output": ""
            }

    def _extract_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        """Find and parse JSON object from agy CLI output."""
        # agy may print lines before or after the JSON payload
        lines = text.strip().splitlines()
        for line in reversed(lines):
            line_str = line.strip()
            if line_str.startswith("{") and line_str.endswith("}"):
                try:
                    data = json.loads(line_str)
                    if "status" in data and ("response" in data or "error" in data):
                        return {
                            "status": data.get("status", "SUCCESS"),
                            "conversation_id": data.get("conversation_id", ""),
                            "response": data.get("response", ""),
                            "error": data.get("error", ""),
                            "num_turns": data.get("num_turns", 1),
                            "usage": data.get("usage", {}),
                            "raw_output": text
                        }
                except json.JSONDecodeError:
                    continue
        return None

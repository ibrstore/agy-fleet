"""
Manifest Executor.
Enforces the Plan-and-Approve signed manifest protocol at the node level.
Verifies SHA-256 hash before executing any modifying command.
Injects secrets safely into temporary environment variables.
"""
import asyncio
import os
import sys
import time
from typing import Dict, List, Optional
from shared.manifest import verify_manifest_hash
from shared.models import (
    ExecutionManifest,
    ExecutionResult,
    StepExecutionResult,
    CommandStep,
)
from shared.redactor import Redactor
from shared.vault import SecretVault
from daemon.wakelock import WakeLock


class ManifestVerificationError(Exception):
    """Raised when manifest hash fails verification."""
    pass


class ManifestExecutor:
    def __init__(self, node_id: str, vault: Optional[SecretVault] = None, redactor: Optional[Redactor] = None):
        self.node_id = node_id
        self.vault = vault or SecretVault()
        self.redactor = redactor or Redactor(vault=self.vault)

    async def execute_manifest(self, manifest: ExecutionManifest, expected_hash: str) -> ExecutionResult:
        """
        Verify manifest hash, acquire wake-lock, execute steps, rollback on failure,
        and run objective verification.
        """
        # 1. Strict Hash Verification
        if not verify_manifest_hash(manifest, expected_hash):
            raise ManifestVerificationError(
                f"Tolak eksekusi di node '{self.node_id}': Hash manifest ({manifest.hash}) "
                f"tidak cocok dengan hash tervalidasi ({expected_hash}). Integritas manifes rusak!"
            )

        node_manifest = manifest.nodes.get(self.node_id)
        if not node_manifest:
            return ExecutionResult(
                manifest_id=manifest.manifest_id,
                node_id=self.node_id,
                success=True,
                error_message=f"Tidak ada perintah untuk node '{self.node_id}' dalam manifest ini."
            )

        step_results: List[StepExecutionResult] = []
        rollback_results: List[StepExecutionResult] = []
        rollback_performed = False

        # 2. Acquire Wake-Lock during active execution
        async with WakeLock(reason=f"Manifest {manifest.manifest_id} execution"):
            # 3. Execute Command Steps
            for idx, step in enumerate(node_manifest.commands):
                result = await self._run_step(step)
                step_results.append(result)

                if not result.success and not step.ignore_failure:
                    # Halt immediately on step failure
                    rollback_performed = True
                    # Run rollback commands
                    for r_step in node_manifest.rollback_commands:
                        r_result = await self._run_step(r_step)
                        rollback_results.append(r_result)

                    return ExecutionResult(
                        manifest_id=manifest.manifest_id,
                        node_id=self.node_id,
                        success=False,
                        step_results=step_results,
                        rollback_performed=True,
                        rollback_results=rollback_results,
                        error_message=(
                            f"Langkah {idx + 1} gagal dengan exit code {result.exit_code}. "
                            f"Eksekusi dihentikan dan rollback telah dijalankan."
                        )
                    )

            # 4. Run Objective Verification Commands
            verification_results: List[StepExecutionResult] = []
            all_verifications_ok = True
            for v_step in node_manifest.verification_commands:
                v_res = await self._run_step(v_step)
                verification_results.append(v_res)
                if not v_res.success and not v_step.ignore_failure:
                    all_verifications_ok = False

            overall_success = all_verifications_ok
            err_msg = None if overall_success else "Satu atau lebih langkah verifikasi objektif gagal."

            return ExecutionResult(
                manifest_id=manifest.manifest_id,
                node_id=self.node_id,
                success=overall_success,
                step_results=step_results,
                verification_results=verification_results,
                rollback_performed=False,
                error_message=err_msg
            )

    async def _run_step(self, step: CommandStep) -> StepExecutionResult:
        """Execute a single step with safe secret injection into subprocess env."""
        start_time = time.time()

        # Build runtime environment
        env = os.environ.copy()
        for env_var, vault_key in step.env_secrets.items():
            secret_value = self.vault.get_secret(vault_key)
            if not secret_value:
                return StepExecutionResult(
                    command=step.command,
                    exit_code=127,
                    stdout="",
                    stderr=f"Secret key '{vault_key}' tidak ditemukan di vault lokal node {self.node_id}.",
                    duration_sec=0.0,
                    success=False,
                    error_message=f"Missing vault secret: {vault_key}"
                )
            env[env_var] = secret_value

        # Expand {{SECRET:KEY}} syntax into env var placeholder if present
        cmd_str = step.command
        for env_var, vault_key in step.env_secrets.items():
            placeholder = f"{{{{SECRET:{vault_key}}}}}"
            if placeholder in cmd_str:
                cmd_str = cmd_str.replace(placeholder, f"${env_var}")

        try:
            extra_kwargs = {"stdin": asyncio.subprocess.DEVNULL}
            if sys.platform == "win32":
                import subprocess
                extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                **extra_kwargs
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=step.timeout_sec
                )
                duration = time.time() - start_time
                exit_code = proc.returncode if proc.returncode is not None else 1
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return StepExecutionResult(
                    command=step.command,
                    exit_code=124,
                    stdout="",
                    stderr=f"Timeout melebihi {step.timeout_sec} detik.",
                    duration_sec=time.time() - start_time,
                    success=False,
                    error_message="Step execution timed out"
                )

            stdout_clean = self.redactor.redact(stdout_bytes.decode("utf-8", errors="replace"))
            stderr_clean = self.redactor.redact(stderr_bytes.decode("utf-8", errors="replace"))

            return StepExecutionResult(
                command=step.command,
                exit_code=exit_code,
                stdout=stdout_clean.strip(),
                stderr=stderr_clean.strip(),
                duration_sec=duration,
                success=(exit_code == 0)
            )

        except Exception as e:
            return StepExecutionResult(
                command=step.command,
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration_sec=time.time() - start_time,
                success=False,
                error_message=str(e)
            )

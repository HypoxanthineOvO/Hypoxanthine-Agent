from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "start.sh"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_start_script(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _make_stub_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    fake_conda_base = tmp_path / "fake-conda"
    conda_profile = fake_conda_base / "etc" / "profile.d"
    conda_profile.mkdir(parents=True)
    fake_server = tmp_path / "fake_server.py"

    fake_server.write_text(
        """
import signal
import socket
import sys
import time

port = int(sys.argv[1])
service = sys.argv[2]
stop = False

def handle_term(signum, frame):
    del signum, frame
    global stop
    stop = True

signal.signal(signal.SIGTERM, handle_term)
signal.signal(signal.SIGINT, handle_term)

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", port))
sock.listen(5)
print(f"{service} listening on {port}", flush=True)
sock.settimeout(0.2)

while not stop:
    try:
        conn, _ = sock.accept()
        conn.close()
    except TimeoutError:
        pass
    except OSError:
        break
    time.sleep(0.05)

sock.close()
print(f"{service} stopped", flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    conda_profile.joinpath("conda.sh").write_text(
        f"""
conda() {{
  if [[ "${{1:-}}" == "activate" ]]; then
    if [[ "${{2:-}}" == "HypoAgent" ]]; then
      export CONDA_DEFAULT_ENV="HypoAgent"
      return 0
    fi
    echo "Environment not found: ${{2:-}}" >&2
    return 1
  fi
  command conda "$@"
}}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    fake_bin.joinpath("conda").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

if [[ "${{1:-}}" == "info" && "${{2:-}}" == "--base" ]]; then
  printf '%s\\n' "{fake_conda_base}"
  exit 0
fi

if [[ "${{1:-}}" == "env" && "${{2:-}}" == "list" ]]; then
  cat <<'EOF'
# conda environments:
#
base                  *  {fake_conda_base}
HypoAgent                {fake_conda_base}/envs/HypoAgent
EOF
  exit 0
fi

echo "unsupported conda invocation: $*" >&2
exit 1
""",
        encoding="utf-8",
    )

    fake_bin.joinpath("python").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

port=""
prev=""
for arg in "$@"; do
  if [[ "$prev" == "--port" ]]; then
    port="$arg"
    break
  fi
  prev="$arg"
done

if [[ -z "$port" ]]; then
  port="${{HYPO_PORT:-0}}"
fi

exec python3 "{fake_server}" "$port" backend
""",
        encoding="utf-8",
    )

    fake_bin.joinpath("npm").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

port=""
prev=""
for arg in "$@"; do
  if [[ "$prev" == "--port" ]]; then
    port="$arg"
    break
  fi
  prev="$arg"
done

if [[ -z "$port" ]]; then
  port="5173"
fi

exec python3 "{fake_server}" "$port" frontend
""",
        encoding="utf-8",
    )

    for path in fake_bin.iterdir():
        path.chmod(0o755)

    backend_port = str(_free_port())
    frontend_port = str(_free_port())
    run_dir = tmp_path / "run"
    log_dir = tmp_path / "logs"
    memory_dir = tmp_path / "memory"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "HYPO_BACKEND_PORT": backend_port,
            "HYPO_FRONTEND_PORT": frontend_port,
            "HYPO_RUN_DIR": str(run_dir),
            "HYPO_LOG_DIR": str(log_dir),
            "HYPO_MEMORY_DIR": str(memory_dir),
            "HYPO_CONDA_BASE": str(fake_conda_base),
            "HYPO_STARTUP_TIMEOUT": "5",
        }
    )
    return env


def _wait_for_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_help_lists_all_supported_commands(tmp_path: Path) -> None:
    env = _make_stub_environment(tmp_path)

    result = _run_start_script(["--help"], env)

    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "start" in result.stdout
    assert "stop" in result.stdout
    assert "restart" in result.stdout
    assert "status" in result.stdout
    assert "logs" in result.stdout
    assert "--backend-port PORT" in result.stdout
    assert "--frontend-port PORT" in result.stdout


def test_start_status_and_stop_manage_backend_and_frontend(tmp_path: Path) -> None:
    env = _make_stub_environment(tmp_path)

    started_at = time.monotonic()
    start = _run_start_script(["start"], env)
    elapsed = time.monotonic() - started_at

    assert start.returncode == 0, start.stderr or start.stdout
    assert "Backend PID" in start.stdout
    assert "Frontend PID" in start.stdout
    assert elapsed < 4.0

    run_dir = Path(env["HYPO_RUN_DIR"])
    log_dir = Path(env["HYPO_LOG_DIR"])
    backend_pid = int((run_dir / "hypo-agent-backend.pid").read_text(encoding="utf-8").strip())
    frontend_pid = int((run_dir / "hypo-agent-frontend.pid").read_text(encoding="utf-8").strip())

    assert (log_dir / "backend.log").exists()
    assert (log_dir / "frontend.log").exists()
    assert not _wait_for_exit(backend_pid, timeout=0.5)
    assert not _wait_for_exit(frontend_pid, timeout=0.5)
    time.sleep(0.5)
    assert not _wait_for_exit(backend_pid, timeout=0.2)
    assert not _wait_for_exit(frontend_pid, timeout=0.2)

    status = _run_start_script(["status"], env)
    assert status.returncode == 0
    assert "backend: running" in status.stdout.lower()
    assert "frontend: running" in status.stdout.lower()
    assert env["HYPO_BACKEND_PORT"] in status.stdout
    assert env["HYPO_FRONTEND_PORT"] in status.stdout

    stop = _run_start_script(["stop"], env)
    assert stop.returncode == 0, stop.stderr or stop.stdout
    assert "backend stopped" in stop.stdout.lower()
    assert "frontend stopped" in stop.stdout.lower()
    assert not (run_dir / "hypo-agent-backend.pid").exists()
    assert not (run_dir / "hypo-agent-frontend.pid").exists()
    assert _wait_for_exit(backend_pid)
    assert _wait_for_exit(frontend_pid)


def test_start_exits_when_required_port_is_busy(tmp_path: Path) -> None:
    env = _make_stub_environment(tmp_path)

    busy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy_socket.bind(("127.0.0.1", int(env["HYPO_BACKEND_PORT"])))
    busy_socket.listen(1)
    try:
        result = _run_start_script(["start"], env)
    finally:
        busy_socket.close()

    assert result.returncode != 0
    assert "already in use" in (result.stderr + result.stdout).lower()


def test_start_accepts_cli_ports_and_status_uses_them(tmp_path: Path) -> None:
    env = _make_stub_environment(tmp_path)
    backend_port = str(_free_port())
    frontend_port = str(_free_port())

    start = _run_start_script(
        ["start", "--backend-port", backend_port, "--frontend-port", frontend_port],
        env,
    )

    assert start.returncode == 0, start.stderr or start.stdout
    assert f"Backend port: {backend_port}" in start.stdout
    assert f"Frontend port: {frontend_port}" in start.stdout

    status = _run_start_script(["status"], env)
    assert status.returncode == 0
    assert backend_port in status.stdout
    assert frontend_port in status.stdout
    assert env["HYPO_BACKEND_PORT"] not in status.stdout
    assert env["HYPO_FRONTEND_PORT"] not in status.stdout

    stop = _run_start_script(["stop"], env)
    assert stop.returncode == 0, stop.stderr or stop.stdout

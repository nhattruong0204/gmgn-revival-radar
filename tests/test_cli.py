import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


def test_cli_offline_demo(tmp_path):
    command = [
        sys.executable,
        "-m",
        "revival_radar.main",
        "demo",
        "--database",
        str(tmp_path / "demo.db"),
    ]
    env = os.environ | {"DRY_RUN": "false", "GMGN_API_KEY": "", "TELEGRAM_BOT_TOKEN": ""}
    run = subprocess.run(command, capture_output=True, text=True, env=env, cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "Confirmed revival" in run.stdout
    assert "Setup 90/100" in run.stdout and "Confirm 75/100" in run.stdout
    assert "sent=0 errors=0" in run.stderr
    assert (tmp_path / "demo.db").exists()


def test_missing_live_credentials_exit_cleanly(tmp_path):
    env = os.environ | {"GMGN_API_KEY": ""}
    run = subprocess.run(
        [sys.executable, "-m", "revival_radar.main", "scan-once"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    )
    assert run.returncode == 2 and "GMGN_API_KEY" in run.stderr
    assert "Traceback" not in run.stderr


def test_ci_compose_and_devcontainer_configuration():
    root = Path(__file__).resolve().parents[1]
    # BaseLoader preserves YAML 1.2's 'on' key instead of interpreting it as boolean.
    ci = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert ci["on"] == ["push", "pull_request"]
    assert any("pytest" in s.get("run", "") for s in ci["jobs"]["test"]["steps"])
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert compose["services"]["radar"]["volumes"] == ["./data:/app/data"]
    dev = json.loads((root / ".devcontainer/devcontainer.json").read_text())
    assert "3.12" in dev["image"]

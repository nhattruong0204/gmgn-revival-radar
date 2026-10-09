import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from revival_radar.main import execute


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


@pytest.mark.parametrize("hot_search_fails", [False, True])
async def test_inspect_without_hot_search_match_never_falls_back_to_trending(
    config, token, responses, tmp_path, monkeypatch, capsys, hot_search_fails
):
    config.database_path = tmp_path / "inspect.db"
    monkeypatch.setattr("time.time", lambda: token.timestamp)
    responses["info"]["data"]["address"] = token.contract_address
    requests = []

    def handler(request):
        path = request.url.path
        requests.append(path)
        assert path != "/v1/market/rank", "Inspect must honor disabled Trending"
        if path == "/v1/market/hot_searches":
            return httpx.Response(
                403 if hot_search_fails else 200,
                json={"code": 0, "data": [{"chain": "sol", "tokens": []}]},
            )
        assert path == "/v1/token/info"
        return httpx.Response(200, json=responses["info"])

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "revival_radar.main.httpx.AsyncClient",
        lambda: original_client(transport=httpx.MockTransport(handler)),
    )
    args = argparse.Namespace(command="inspect", chain="sol", contract=token.contract_address)
    assert await execute(args, config) == 0
    assert requests == ["/v1/market/hot_searches", "/v1/token/info"]
    output = json.loads(capsys.readouterr().out)
    assert output["token"]["contract_address"] == token.contract_address
    assert output["token"]["discovery_source"] == []
    assert output["signal"]["eligible"] is False


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

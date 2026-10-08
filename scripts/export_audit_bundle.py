#!/usr/bin/env python3
"""Standalone read-only VPS exporter. Python stdlib only; never imports DB migrations.

Run on the Docker host. Only new private audit files are written. No service restart,
package installation, production configuration edit, or network API call occurs.
Raw logs/environment/inspect output are deliberately not exported.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Executed inside the existing container, using its installed version and environment.
# Configuration errors must never stringify Pydantic exceptions (they include inputs).
CONFIG_PROBE = r"""
import hashlib, importlib.util, json
try:
    from revival_radar.config import Settings
    from revival_radar.config_context import configuration_context
    from revival_radar.runtime_settings import RuntimeSettings
    runtime = RuntimeSettings(Settings())
    cfg = runtime.effective()
    context = configuration_context(cfg, runtime.revision)
    modules = ["scanner", "enrichment", "analysis.scoring", "analysis.acceleration",
               "analysis.market_structure", "clients.gmgn", "clients.normalization",
               "storage.database", "storage.repository", "service"]
    hashes = {}
    for module in modules:
        spec = importlib.util.find_spec("revival_radar." + module)
        if spec and spec.origin:
            with open(spec.origin, "rb") as handle:
                hashes[module] = hashlib.file_digest(handle, "sha256").hexdigest()
    print(json.dumps({"database_path": str(cfg.database_path.resolve()),
                      "context": context,
                      "running_source_sha256": hashes,
                      "http_attempts": cfg.http_attempts,
                      "http_timeout_seconds": cfg.http_timeout_seconds,
                      "retry_max_wait_seconds": cfg.retry_max_wait_seconds}))
except Exception as exc:
    print(json.dumps({"error_type": type(exc).__name__}))
    raise SystemExit(1)
"""
BACKUP_PROBE = r"""
import json, os, sqlite3, sys, tempfile, time
from pathlib import Path
source = Path(sys.argv[1]).resolve()
if not source.is_file():
    raise SystemExit(2)
fd, target = tempfile.mkstemp(prefix="revival-radar-audit-", suffix=".db", dir="/tmp")
os.close(fd)
original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
backup = sqlite3.connect(target)
started = time.time()
def progress(status, remaining, total):
    if time.time() - started > 120:
        raise TimeoutError("Audit backup deadline")
try:
    original.backup(backup, pages=256, progress=progress, sleep=0.05)
    integrity = [r[0] for r in backup.execute("PRAGMA integrity_check")]
    print(json.dumps({"audit_copy": target, "source_bytes": source.stat().st_size,
        "wal_bytes": Path(str(source)+"-wal").stat().st_size
            if Path(str(source)+"-wal").exists() else 0,
        "source_journal_mode": original.execute("PRAGMA journal_mode").fetchone()[0],
        "schema_version": backup.execute("PRAGMA user_version").fetchone()[0],
        "integrity": integrity, "backup_started_at": started, "backup_finished_at": time.time()}))
finally:
    backup.close()
    original.close()
"""


def run(args, *, input_text=None, timeout=30):
    """No shell, no inherited secret values printed, no stderr persisted."""
    try:
        proc = subprocess.run(
            args, input=input_text, text=True, capture_output=True, timeout=timeout, check=False
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout[:2_000_000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": -1, "error_type": type(exc).__name__, "stdout": ""}


def safe_probe(args):
    result = run(args)
    return {"returncode": result["returncode"], "output": result["stdout"][:32_000]}


def log_event(line):
    """Export only known event grammar and allowlisted numeric/identity fields."""
    stamp = re.search(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:\d\d)?", line)
    text = re.sub(r"https?://\S+", "", line.lower())
    category = None
    for needle, label in (
        ("rate_limited", "gmgn_429"),
        ("http=429", "gmgn_429"),
        ("alert cooldown", "alert_cooldown"),
        ("gmgn cooldown", "gmgn_cooldown"),
        ("timeout", "gmgn_timeout"),
        ("database is locked", "sqlite_locked"),
        ("sqlite", "sqlite"),
        ("malformed token", "token_normalization"),
        ("scan exceeded", "scan_overrun"),
        ("interrupted", "interrupted_scan"),
        ("traceback", "traceback"),
    ):
        if needle in text:
            category = label
            break
    if category is None and any(f"http={code}" in text for code in (500, 502, 503, 504)):
        category = "gmgn_5xx"
    if category is None and "telegram" in text and any(x in text for x in ("failed", "error")):
        category = "telegram"
    if category is None and re.search(r"\b(?:failed|error|warning)\b", text):
        category = "other_unclassified"
    complete = "scan complete processed=" in line
    if not category and not complete and "scan configuration revision=" not in line:
        return None
    result = {
        "timestamp_text": stamp.group(0) if stamp else None,
        "category": category,
        "event": "scan_complete" if complete else "diagnostic",
    }
    for name in (
        "processed",
        "potential_alerts",
        "sent",
        "errors",
        "sources_ok",
        "duration_seconds",
        "revision",
        "threshold",
        "retry_in",
        "target_seconds",
    ):
        match = re.search(rf"\b{name}=(\d+(?:\.\d+)?)\b", line)
        if match:
            result[name] = float(match.group(1))
    chain = re.search(r"\bchain=(sol|base|bsc|robinhood|arc)\b", line)
    address = re.search(r"\baddress=(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b", line)
    if chain:
        result["chain"] = chain.group(1)
    if address:
        result["contract_address"] = address.group(1)
    return result


def summarize_logs(container, hours, tail):
    events, counts = [], Counter()
    command = [
        "docker",
        "logs",
        "--timestamps",
        "--since",
        f"{hours}h",
        "--tail",
        str(tail),
        container,
    ]
    # Docker writes application stderr to stderr too. Parse both streams without retaining text.
    result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    read_bytes = 0
    truncated = False
    deadline = time.monotonic() + 30
    try:
        for raw in result.stdout:
            read_bytes += len(raw)
            if read_bytes > 16_000_000 or time.monotonic() > deadline:
                truncated = True
                result.terminate()  # Only the bounded docker-logs reader, never the bot.
                break
            event = log_event(raw[:8192].decode("utf-8", "replace"))
            if event:
                events.append(event)
                if event["category"]:
                    counts[event["category"]] += 1
        code = result.wait(timeout=5)
    finally:
        if result.poll() is None:
            result.kill()
            result.wait()
        result.stdout.close()
    return {
        "hours": hours,
        "tail_limit": tail,
        "byte_limit": 16_000_000,
        "truncated": truncated or read_bytes >= 16_000_000,
        "tail_may_truncate": True,
        "returncode": code,
        "categories": dict(counts),
        "events": events,
        "limitations": "Counts are diagnostic log lines, not unique incidents. "
        "Exception-type-only logs cannot identify 429 versus other DataSourceError. "
        "Recovery and exact scan ID attribution are unknown for legacy logs.",
    }


def select_container(name):
    listed = run(["docker", "ps", "--format", "{{.ID}} {{.Names}}"])
    if listed["returncode"]:
        raise RuntimeError("Docker inventory unavailable")
    rows = [line.split(maxsplit=1) for line in listed["stdout"].splitlines()]
    if name:
        matches = [row for row in rows if name in row]
    else:
        matches = [row for row in rows if "radar" in row[1].lower()]
    if len(matches) != 1:
        raise RuntimeError("Specify --container with one running container name or ID")
    return matches[0][0]


def inspect_container(container):
    result = run(["docker", "inspect", container])
    if result["returncode"]:
        raise RuntimeError("Container inspection unavailable")
    raw = json.loads(result["stdout"])[0]
    env = raw.get("Config", {}).get("Env", [])
    secret_values = [
        s.partition("=")[2]
        for s in env
        if re.search(r"KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL", s.partition("=")[0])
        and len(s.partition("=")[2]) >= 8
    ]
    state = raw.get("State", {})
    host = raw.get("HostConfig", {})
    config = raw.get("Config", {})
    labels = config.get("Labels", {}) or {}
    safe = {
        "id": raw["Id"],
        "name": raw.get("Name"),
        "image_id": raw.get("Image"),
        "image": config.get("Image"),
        "created": raw.get("Created"),
        "working_directory": config.get("WorkingDir"),
        "entrypoint_executable": (config.get("Entrypoint") or [None])[0],
        "command_executable": (config.get("Cmd") or [None])[0],
        "state": {
            k: state.get(k) for k in ("Status", "Running", "OOMKilled", "StartedAt", "FinishedAt")
        },
        "restart_count": raw.get("RestartCount"),
        "restart_policy": host.get("RestartPolicy"),
        "limits": {
            k: host.get(k) for k in ("Memory", "MemorySwap", "NanoCpus", "CpuQuota", "CpuPeriod")
        },
        "mounts": [
            {k: m.get(k) for k in ("Type", "Source", "Destination", "RW", "Name")}
            for m in raw.get("Mounts", [])
        ],
        "logging": {
            "type": host.get("LogConfig", {}).get("Type"),
            "rotation": {
                k: v
                for k, v in host.get("LogConfig", {}).get("Config", {}).items()
                if k in {"max-size", "max-file"}
            },
        },
        "compose_directory": labels.get("com.docker.compose.project.working_dir"),
        "environment_names": sorted(
            s.partition("=")[0]
            for s in env
            if re.fullmatch(r"[A-Z_][A-Z0-9_]*", s.partition("=")[0])
        ),
    }
    log_path = raw.get("LogPath")
    if log_path and Path(log_path).is_file():
        safe["current_docker_log_bytes"] = Path(log_path).stat().st_size
    return safe, secret_values


def oom_summary():
    result = run(["journalctl", "-k", "--since", "72 hours ago", "--no-pager", "-n", "20000"])
    if result["returncode"]:
        return {"available": False, "reason": "kernel journal inaccessible"}
    lines = result["stdout"].splitlines()
    count = sum(
        bool(re.search(r"out of memory|oom-kill|killed process", line, re.I)) for line in lines
    )
    return {
        "available": True,
        "matching_lines": count,
        "bounded_lines": 20000,
        "note": "No raw kernel log retained; bounded journal absence is not proof of no OOM.",
    }


def assert_no_secrets(path, secrets):
    """Refuse an export containing known credentials, including inside the private DB."""
    longest = max(map(len, secrets), default=1)
    overlap = b""
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            data = overlap + chunk
            if any(value.encode() in data for value in secrets):
                raise RuntimeError("Known credential found in audit data; export withheld")
            overlap = data[-longest:]


def export(output, container=None, hours=72, tail=100000):
    os.umask(0o077)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    container = select_container(container)
    info, secrets = inspect_container(container)
    probe = run(["docker", "exec", "-i", container, "python", "-"], input_text=CONFIG_PROBE)
    if probe["returncode"]:
        raise RuntimeError("Effective configuration probe failed; no secrets or errors exported")
    configuration = json.loads(probe["stdout"])
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    destination = output / f"revival-radar-audit-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="revival-radar-export-") as directory:
        work = Path(directory)
        backup = run(
            ["docker", "exec", "-i", container, "python", "-", configuration["database_path"]],
            input_text=BACKUP_PROBE,
            timeout=130,
        )
        if backup["returncode"]:
            raise RuntimeError("Consistent backup failed; production remains running")
        metadata = json.loads(backup["stdout"])
        if metadata["integrity"] != ["ok"]:
            raise RuntimeError("Backup integrity check failed")
        copied = run(
            [
                "docker",
                "cp",
                f"{container}:{metadata['audit_copy']}",
                str(work / "production_snapshot.db"),
            ],
            timeout=120,
        )
        if copied["returncode"]:
            raise RuntimeError("Audit copy transfer failed")
        os.chmod(work / "production_snapshot.db", 0o600)
        with sqlite3.connect(
            (work / "production_snapshot.db").as_uri() + "?mode=ro", uri=True
        ) as db:
            if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
                raise RuntimeError("Transferred backup integrity check failed")
        root = info.get("compose_directory")
        git = {}
        if root and Path(root).is_dir():
            for key, args in {
                "sha": ["rev-parse", "HEAD"],
                "status": ["status", "--short"],
                "main_tracking_sha": ["rev-parse", "refs/remotes/origin/main"],
            }.items():
                git[key] = safe_probe(["git", "-C", root, *args])
        probes = {
            "hostname": ["hostname"],
            "utc_time": ["date", "-u", "+%FT%TZ"],
            "uptime": ["uptime"],
            "kernel": ["uname", "-srmo"],
            "disk": ["df", "-h"],
            "memory": ["free", "-h"],
            "cpu_count": ["nproc"],
            "processes": ["ps", "-eo", "pid,comm,pcpu,pmem,rss,etimes"],
            "vmstat": ["vmstat", "1", "3"],
            "docker_stats": ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
            "docker_inventory": [
                "docker",
                "ps",
                "-a",
                "--format",
                "{{.ID}} {{.Image}} {{.Status}} {{.Names}}",
            ],
        }
        with (work / "production_snapshot.db").open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        manifest = {
            "exported_at": time.time(),
            "producer": "export_audit_bundle.py-v1",
            "git": git,
            "backup": metadata,
            "production_mutations": [],
            "snapshot_sha256": digest,
            "limitations": [
                "Host checkout SHA may differ from built image contents; verify image provenance.",
                "Tracking main SHA may be stale; compare against current GitHub main separately.",
                "Remote audit copy is left in container /tmp; no production files deleted.",
                "Log summaries are bounded and omit arbitrary message bodies.",
            ],
        }
        artifacts = {
            "manifest.json": manifest,
            "production_config_sanitized.json": configuration,
            "container_inventory.json": info,
            "vps_inventory.json": {key: safe_probe(args) for key, args in probes.items()}
            | {"oom_evidence": oom_summary()},
            "production_logs_summary.json": summarize_logs(container, hours, tail),
        }
        for filename, value in artifacts.items():
            (work / filename).write_text(json.dumps(value, indent=2))
        for path in work.iterdir():
            assert_no_secrets(path, secrets)
        with (
            destination.open("xb") as fileobj,
            tarfile.open(fileobj=fileobj, mode="w:gz") as archive,
        ):
            for path in sorted(work.iterdir()):
                archive.add(path, arcname=path.name, recursive=False)
        os.chmod(destination, 0o600)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--container", help="Running radar container name or ID; auto-selects only if unambiguous"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--hours", type=int, choices=range(1, 169), default=72, metavar="1..168")
    parser.add_argument("--tail", type=int, default=100000)
    args = parser.parse_args()
    if not 1 <= args.tail <= 100000:
        parser.error("--tail must be between 1 and 100000")
    try:
        print(export(args.output_dir, args.container, args.hours, args.tail))
    except Exception as exc:
        # Deliberately omit exception message, subprocess output and validation inputs.
        parser.exit(1, f"Audit export failed ({type(exc).__name__}); no secret values printed.\n")


if __name__ == "__main__":
    main()

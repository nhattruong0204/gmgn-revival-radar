"""Install the pinned, project-local Codex UI/UX skill without changing app dependencies."""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install(source, destination, revision):
    actual = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != revision:
        raise ValueError("Skill checkout does not match the pinned revision")
    marker = destination / ".source-revision"
    if destination.exists():
        if (
            marker.is_file()
            and marker.read_text().strip() == revision
            and (destination / "SKILL.md").is_file()
            and (destination / "scripts/search.py").is_file()
            and (destination / "LICENSE").is_file()
        ):
            print("Pinned UI UX Pro Max installation is already present.")
            return
        raise ValueError("Existing skill differs; preserve and review it before replacing it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ui-ux-install-", dir=destination.parent) as directory:
        staged = Path(directory) / "skill"
        staged.mkdir()
        for name in ("data", "scripts"):
            shutil.copytree(
                source / "src/ui-ux-pro-max" / name,
                staged / name,
                ignore=shutil.ignore_patterns("__pycache__", "tests"),
            )
        upstream = source / ".claude/skills/ui-ux-pro-max"
        shutil.copytree(upstream / "references", staged / "references")
        content = (
            (upstream / "SKILL.md")
            .read_text()
            .replace(
                "${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max", str(destination.resolve())
            )
        )
        (staged / "SKILL.md").write_text(content)
        shutil.copyfile(source / "LICENSE", staged / "LICENSE")
        (staged / ".source-revision").write_text(revision + "\n")
        staged.rename(destination)
    print("Installed pinned UI UX Pro Max skill for this checkout.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Reuse a checkout at the pinned commit")
    args = parser.parse_args()
    lock = json.loads((ROOT / "tools/ui-ux-pro-max.json").read_text())
    destination = ROOT / lock["destination"]
    if args.source:
        install(args.source, destination, lock["revision"])
        return
    with tempfile.TemporaryDirectory(prefix="onchainflow-ui-ux-") as directory:
        source = Path(directory) / "upstream"
        subprocess.run(["git", "init", "--quiet", str(source)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "fetch",
                "--quiet",
                "--depth",
                "1",
                lock["repository"],
                lock["revision"],
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "checkout", "--quiet", "--detach", "FETCH_HEAD"], check=True
        )
        install(source, destination, lock["revision"])


if __name__ == "__main__":
    main()

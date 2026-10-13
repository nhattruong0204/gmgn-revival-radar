"""Set the website owner password privately on the host (standard library only)."""

import argparse
import getpass
import hashlib
import os
import secrets
import tempfile
from pathlib import Path


def configure(path: Path, password: str) -> None:
    if not 12 <= len(password) <= 1024:
        raise ValueError("Use 12–1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    encoded = f"scrypt$32768$8$1${salt.hex()}${digest.hex()}"
    lines = path.read_text().splitlines()
    lines = [
        line
        for line in lines
        if not line.startswith(("WEB_ADMIN_PASSWORD_HASH=", "WEB_ADMIN_ENABLED="))
    ]
    lines.extend(["WEB_ADMIN_ENABLED=true", f"WEB_ADMIN_PASSWORD_HASH={encoded}"])
    fd, temporary = tempfile.mkstemp(prefix=".web-owner-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.web"))
    args = parser.parse_args()
    password = getpass.getpass("Website owner password (12+ characters): ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match; nothing changed.")
    configure(args.env_file, password)
    print("Owner password updated. Recreate only the web service to apply it.")

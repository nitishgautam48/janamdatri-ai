"""Download (once) and start Mailpit, the local inbox that receives every SMS (OTPs and alerts).

    python -m scripts.mailpit        # start Mailpit (blocks); UI at http://localhost:8025
    python -m scripts.mailpit path   # print the binary path (downloads it if needed)

Uses a `mailpit` already on PATH if there is one, otherwise downloads the official release from
GitHub into backend/.tools/. If something is already listening on the SMTP port, it does nothing.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / ".tools"
RELEASE = "https://github.com/axllent/mailpit/releases/latest/download/"
SMTP_PORT, UI_PORT = 1025, 8025


def _asset() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if system not in ("linux", "darwin", "windows") or not arch:
        raise SystemExit(f"No Mailpit build for {system}/{machine}; install it from https://mailpit.axllent.org")
    if system == "windows":
        return f"mailpit-windows-{arch}.zip", "mailpit.exe"
    return f"mailpit-{system}-{arch}.tar.gz", "mailpit"


def binary() -> Path:
    found = shutil.which("mailpit")
    if found:
        return Path(found)
    asset, name = _asset()
    target = TOOLS / name
    if target.exists():
        return target
    TOOLS.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Mailpit ({asset}) …", flush=True)
    with urllib.request.urlopen(RELEASE + asset, timeout=120) as r:
        data = r.read()
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            target.write_bytes(z.read(name))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            member = t.extractfile(name)
            target.write_bytes(member.read())
    target.chmod(0o755)
    return target


def running(port: int = SMTP_PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "path":
        print(binary())
        return 0
    if running():
        print(f"Mailpit (or another SMTP server) is already running on port {SMTP_PORT}.")
        return 0
    exe = binary()
    TOOLS.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "--smtp", f"127.0.0.1:{SMTP_PORT}", "--listen", f"127.0.0.1:{UI_PORT}",
           "--database", str(TOOLS / "mailpit.db"), "--quiet"]
    if os.name == "posix":
        os.execv(cmd[0], cmd)  # become Mailpit, so stopping this process stops the inbox too
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())

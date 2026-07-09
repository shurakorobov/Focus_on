"""Швидке створення публічного HTTPS-тунелю до локального сервера.

Використовує безкоштовний Cloudflare quick tunnel (trycloudflare.com):
одна команда — і ви отримуєте публічну адресу, що вказує на ваш localhost.

    python expose.py

Скрипт:
1) Завантажує cloudflared (якщо ще немає) у .cloudflared/
2) Запускає тунель до порту 8000
3) Друкує публічний URL — вставте його у .env як WEBAPP_URL

Потрібен інтернет. Не потребує облікового запису Cloudflare.
"""
from __future__ import annotations

import os
import platform
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BIN_DIR = HERE / ".cloudflared"
BIN_DIR.mkdir(exist_ok=True)


def binary_name() -> str:
    return "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"


def binary_path() -> Path:
    return BIN_DIR / binary_name()


def download() -> Path | None:
    """Завантажує cloudflared, якщо його ще немає."""
    exe = binary_path()
    if exe.exists():
        return exe

    machine = platform.machine().lower()
    sysname = platform.system().lower()
    if sysname == "windows":
        arch = "amd64" if machine in ("amd64", "x86_64") else "386"
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-{arch}.exe"
        target = BIN_DIR / "cloudflared.exe"
    elif sysname == "darwin":
        arch = "arm64" if "arm" in machine else "amd64"
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-{arch}.tgz"
        target = BIN_DIR / "cloudflared"
    else:
        arch = "arm64" if "aarch64" in machine or "arm" in machine else "amd64"
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
        target = BIN_DIR / "cloudflared"

    print(f"Завантажую cloudflared → {url}")
    try:
        urllib.request.urlretrieve(url, target)
    except Exception as e:
        print(f"❌ Не вдалося завантажити cloudflared: {e}")
        print("   Встановіть вручную: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/")
        return None

    if sysname != "windows":
        target.chmod(target.stat().st_mode | stat.S_IEXEC)

    # розпакувати tgz для macOS
    if url.endswith(".tgz"):
        subprocess.run(["tar", "-xzf", str(target), "-C", str(BIN_DIR)], check=False)
        target.unlink()

    return binary_path()


def main() -> int:
    exe = download()
    if not exe or not exe.exists():
        return 1

    # Визначаємо порт з налаштувань
    try:
        from config import settings
        port = settings.PORT
    except Exception:
        port = 8000

    print()
    print("=" * 60)
    print(f"Запускаю тунель до http://localhost:{port}")
    print("Шукаю рядок 'https://....trycloudflare.com' нижче.")
    print("Це і є ваш WEBAPP_URL — вставте його у .env")
    print("Щоб зупинити: Ctrl+C")
    print("=" * 60)
    print()

    cmd = [str(exe), "tunnel", "--url", f"http://localhost:{port}"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(line)
    except KeyboardInterrupt:
        proc.terminate()
        print("\nТунель зупинено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

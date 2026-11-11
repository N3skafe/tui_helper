"""Install a downloaded tui.exe for the current Windows user.

Running the release executable copies it into %LOCALAPPDATA%\\tui_helper
and adds that folder to the user PATH. Source checkouts do nothing here.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from commands import restrict_to_current_user


def install_directory() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    return Path(local) / "tui_helper"


def merge_path_entries(existing: str, entry: str) -> tuple[str, bool]:
    """Keep ``entry`` once at the end. Return the new value and whether it changed."""
    target = entry.rstrip("\\/")
    kept: list[str] = []
    found = False
    for item in existing.split(";"):
        trimmed = item.strip()
        if not trimmed:
            continue
        if trimmed.rstrip("\\/").casefold() == target.casefold():
            if not found:
                kept.append(trimmed)
            found = True
        else:
            kept.append(trimmed)
    if not found:
        kept.append(entry)
    new = ";".join(kept)
    old = ";".join(part.strip() for part in existing.split(";") if part.strip())
    return new, new != old


def ensure_frozen_install() -> None:
    """Copy this exe into the user folder and register PATH. No-op for source runs."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    folder = install_directory()
    if folder is None:
        print("tui: LOCALAPPDATA is not set, so this exe was not registered on PATH.")
        return
    try:
        changed = _install_executable(folder)
    except OSError as exc:
        print(f"tui: could not finish user setup: {exc}")
        return
    if changed:
        print(f"Installed tui for this user: {folder / 'tui.exe'}")
        print("Open a new terminal and run: tui")


def _install_executable(folder: Path) -> bool:
    folder.mkdir(parents=True, exist_ok=True)
    current = Path(sys.executable).resolve()
    dest = folder / "tui.exe"
    shim = folder / "tui.cmd"
    changed = False
    same = dest.exists() and dest.resolve() == current
    if not same:
        shutil.copy2(current, dest)
        restrict_to_current_user(str(dest))
        changed = True
    if shim.exists():
        shim.unlink()
        changed = True
    if _ensure_user_path(str(folder)):
        changed = True
    return changed


def _ensure_user_path(entry: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            current, typ = winreg.QueryValueEx(key, "Path")
            current = str(current)
    except FileNotFoundError:
        current, typ = "", winreg.REG_EXPAND_SZ
    updated, changed = merge_path_entries(current, entry)
    if changed:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "Path", 0, typ, updated)
        _broadcast_environment_change()
    _refresh_process_path(entry)
    return changed


def _refresh_process_path(entry: str) -> None:
    updated, _changed = merge_path_entries(os.environ.get("PATH", ""), entry)
    os.environ["PATH"] = updated


def _broadcast_environment_change() -> None:
    import ctypes

    result = ctypes.c_ulong()
    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    user32.SendMessageTimeoutW(
        0xFFFF,
        0x001A,
        0,
        "Environment",
        0x0002,
        5000,
        ctypes.byref(result),
    )

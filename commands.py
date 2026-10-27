"""Windows command names for completion and shell choice.

The list is loaded once. Typing and completion do not run the user's
command. Git words live in git_commands.py.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

# Same names as completion.ROOT_COMMANDS. These win over cmd, PATH, and PowerShell.
APP_COMMAND_NAMES = (
    "help",
    "clear",
    "exit",
    "git",
    "docker",
    "npm",
    "cd",
    "open",
)

# Names printed by `cmd /c help`, with a short menu description.
CMD_COMMANDS: tuple[tuple[str, str], ...] = (
    ("assoc", "File extension associations"),
    ("attrib", "File attributes"),
    ("break", "CTRL+C checking"),
    ("bcdedit", "Boot configuration"),
    ("cacls", "File access control lists"),
    ("call", "Call a batch program"),
    ("cd", "Change directory"),
    ("chcp", "Active code page"),
    ("chdir", "Change directory"),
    ("chkdsk", "Check a disk"),
    ("chkntfs", "Boot-time disk check"),
    ("cls", "Clear the screen"),
    ("cmd", "Start a command interpreter"),
    ("color", "Console colors"),
    ("comp", "Compare files"),
    ("compact", "NTFS compression"),
    ("convert", "Convert a volume to NTFS"),
    ("copy", "Copy files"),
    ("date", "Show or set the date"),
    ("del", "Delete files"),
    ("dir", "List a directory"),
    ("diskpart", "Disk partitions"),
    ("doskey", "Command macros"),
    ("driverquery", "Device drivers"),
    ("echo", "Show messages"),
    ("endlocal", "End local environment"),
    ("erase", "Delete files"),
    ("exit", "Quit the command interpreter"),
    ("fc", "Compare files"),
    ("find", "Find text in files"),
    ("findstr", "Find strings in files"),
    ("for", "Run a command for each file"),
    ("format", "Format a disk"),
    ("fsutil", "File system properties"),
    ("ftype", "File type associations"),
    ("goto", "Go to a batch label"),
    ("gpresult", "Group Policy result"),
    ("help", "Windows command help"),
    ("icacls", "File access control lists"),
    ("if", "Conditional batch command"),
    ("label", "Disk volume label"),
    ("md", "Create a directory"),
    ("mkdir", "Create a directory"),
    ("mklink", "Create a link"),
    ("mode", "Configure a device"),
    ("more", "Show output one screen at a time"),
    ("move", "Move files"),
    ("openfiles", "Files opened remotely"),
    ("path", "Executable search path"),
    ("pause", "Pause a batch file"),
    ("popd", "Return to a saved directory"),
    ("print", "Print a text file"),
    ("prompt", "Command prompt text"),
    ("pushd", "Save the directory and change it"),
    ("rd", "Remove a directory"),
    ("recover", "Recover a file"),
    ("rem", "Batch comment"),
    ("ren", "Rename files"),
    ("rename", "Rename files"),
    ("replace", "Replace files"),
    ("rmdir", "Remove a directory"),
    ("robocopy", "Copy directory trees"),
    ("set", "Environment variables"),
    ("setlocal", "Begin local environment"),
    ("sc", "Windows services"),
    ("schtasks", "Scheduled tasks"),
    ("shift", "Shift batch parameters"),
    ("shutdown", "Shut down the computer"),
    ("sort", "Sort input"),
    ("start", "Start a program"),
    ("subst", "Substitute a drive letter"),
    ("systeminfo", "Machine information"),
    ("tasklist", "Running tasks"),
    ("taskkill", "Stop a task"),
    ("time", "Show or set the time"),
    ("title", "Window title"),
    ("tree", "Directory tree"),
    ("type", "Show a text file"),
    ("ver", "Windows version"),
    ("verify", "Verify disk writes"),
    ("vol", "Disk volume label"),
    ("xcopy", "Copy files and directories"),
    ("wmic", "WMI command line"),
)

_PATH_SUFFIXES = {".exe", ".bat", ".cmd", ".com"}
_APP_KEYS = frozenset(name.casefold() for name in APP_COMMAND_NAMES)
_CMD_KEYS = frozenset(name.casefold() for name, _description in CMD_COMMANDS)
_ORIGIN_RANK = {"app": 0, "cmd": 1, "path": 2, "powershell": 3}

# key -> (display name, description, origin). cmd overwrites path, path overwrites PowerShell.
_system_entries: dict[str, tuple[str, str, str]] | None = None
_english_ready = False


def prefer_english() -> None:
    """Ask Windows to load English text for this process and its children."""
    global _english_ready
    if _english_ready or os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        kernel.SetProcessPreferredUILanguages.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.ULONG),
        ]
        count = wintypes.ULONG()
        # MUI_LANGUAGE_NAME. The extra NUL ends the language list.
        kernel.SetProcessPreferredUILanguages(0x8, "en-US\0\0", ctypes.byref(count))
    except (AttributeError, OSError, ValueError):
        pass
    _english_ready = True


def command_origin(name: str) -> str:
    """Return app, cmd, path, powershell, or cmd when the name is unknown."""
    key = name.casefold()
    if key in _APP_KEYS:
        return "app"
    if key in _CMD_KEYS:
        return "cmd"
    entry = _system_entries_map().get(key)
    if entry is None:
        return "cmd"
    return entry[2]


def matching_commands(
    prefix: str,
    app_commands: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Commands for the first word.

    An empty prefix stays the short app list. On Windows a typed prefix also
    searches cmd, PATH, and PowerShell. App names stay ahead of the others.
    """
    if not prefix or os.name != "nt":
        return [(name, description) for name, description in app_commands if _starts(name, prefix)]

    rows: list[tuple[int, int, str, str, str]] = []
    app_keys: set[str] = set()
    for index, (name, description) in enumerate(app_commands):
        key = name.casefold()
        app_keys.add(key)
        if _starts(name, prefix):
            rows.append((0, index, key, name, description))
    for key, (name, description, origin) in _system_entries_map().items():
        if key in app_keys or not _starts(name, prefix):
            continue
        rows.append((_ORIGIN_RANK[origin], 0, key, name, description))
    rows.sort()
    return [(name, description) for _rank, _index, _key, name, description in rows]


def _starts(name: str, prefix: str) -> bool:
    return name.casefold().startswith(prefix.casefold())


def _system_entries_map() -> dict[str, tuple[str, str, str]]:
    global _system_entries
    if _system_entries is None:
        _system_entries = _load_system_entries()
    return _system_entries


def _load_system_entries() -> dict[str, tuple[str, str, str]]:
    if os.name != "nt":
        return {}
    entries: dict[str, tuple[str, str, str]] = {}
    for name, kind in _powershell_commands():
        entries[name.casefold()] = (name, kind, "powershell")
    for name in _path_commands():
        entries[name.casefold()] = (name, "program", "path")
    for name, description in CMD_COMMANDS:
        entries[name.casefold()] = (name, description, "cmd")
    return entries


def _path_commands() -> list[str]:
    found: dict[str, str] = {}
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        try:
            filenames = os.listdir(directory)
        except OSError:
            continue
        for filename in filenames:
            stem, suffix = os.path.splitext(filename)
            if not stem or suffix.casefold() not in _PATH_SUFFIXES:
                continue
            if any(char in stem for char in "\r\n\x00"):
                continue
            key = stem.casefold()
            if key not in found:
                found[key] = stem
    return list(found.values())


def powershell_executable() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _powershell_commands() -> list[tuple[str, str]]:
    script = (
        "[Threading.Thread]::CurrentThread.CurrentUICulture = 'en-US'; "
        "[Threading.Thread]::CurrentThread.CurrentCulture = 'en-US'; "
        "Get-Command | ForEach-Object { $_.CommandType.ToString() + [char]9 + $_.Name }"
    )
    prefer_english()
    try:
        completed = subprocess.run(
            [
                powershell_executable(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 and not completed.stdout:
        return []
    commands: list[tuple[str, str]] = []
    for line in decode_console_bytes(completed.stdout).splitlines():
        kind, separator, name = line.strip().partition("\t")
        if not separator or not name or any(char in name for char in "\r\n\x00 \t"):
            continue
        label = kind.casefold()
        if label not in {"cmdlet", "function", "alias"}:
            continue
        commands.append((name, label))
    return commands


# Bare entry stays in this program instead of handing over the terminal.
INTERACTIVE_PROGRAMS = frozenset({"netsh", "diskpart"})

# Documented diskpart words. diskpart.exe cannot be queried without elevation.
DISKPART_COMMANDS: dict[tuple[str, ...], tuple[tuple[str, str], ...]] = {
    (): (
        ("active", "Mark the selected partition active"),
        ("assign", "Assign a drive letter or mount point"),
        ("attach", "Attach a virtual disk"),
        ("attributes", "Show or set attributes"),
        ("automount", "Enable or disable automount"),
        ("break", "Break a mirror"),
        ("clean", "Remove partition information"),
        ("compact", "Compact a virtual disk"),
        ("convert", "Convert a disk"),
        ("create", "Create a partition, volume, or virtual disk"),
        ("delete", "Delete a disk, partition, or volume"),
        ("detail", "Show details for the selection"),
        ("detach", "Detach a virtual disk"),
        ("expand", "Expand a virtual disk"),
        ("extend", "Extend a volume"),
        ("filesystems", "Show file systems"),
        ("format", "Format the selected volume"),
        ("gpt", "Assign GPT attributes"),
        ("help", "Show diskpart help"),
        ("import", "Import a disk group"),
        ("inactive", "Mark the selected partition inactive"),
        ("list", "List disks, partitions, or volumes"),
        ("merge", "Merge a virtual disk"),
        ("offline", "Take a disk or volume offline"),
        ("online", "Bring a disk or volume online"),
        ("recover", "Refresh the state of disks"),
        ("rem", "Comment"),
        ("remove", "Remove a drive letter or mount point"),
        ("rescan", "Rescan disks"),
        ("retain", "Prepare a volume for use"),
        ("san", "Show or set SAN policy"),
        ("select", "Select a disk, partition, or volume"),
        ("setid", "Set the partition type"),
        ("shrink", "Shrink the selected volume"),
        ("uniqueid", "Show or set a disk id"),
    ),
    ("add",): (("disk", "Add a disk to a mirror"),),
    ("attach",): (("vdisk", "Attach a virtual disk file"),),
    ("attributes",): (
        ("disk", "Disk attributes"),
        ("volume", "Volume attributes"),
    ),
    ("break",): (("disk", "Break a mirrored disk"),),
    ("compact",): (("vdisk", "Compact a virtual disk file"),),
    ("convert",): (
        ("basic", "Convert to a basic disk"),
        ("dynamic", "Convert to a dynamic disk"),
        ("gpt", "Convert to GPT"),
        ("mbr", "Convert to MBR"),
    ),
    ("create",): (
        ("partition", "Create a partition"),
        ("volume", "Create a volume"),
        ("vdisk", "Create a virtual disk"),
    ),
    ("create", "partition"): (
        ("efi", "EFI system partition"),
        ("extended", "Extended partition"),
        ("logical", "Logical partition"),
        ("msr", "Microsoft reserved partition"),
        ("primary", "Primary partition"),
    ),
    ("create", "volume"): (
        ("mirror", "Mirrored volume"),
        ("raid", "RAID-5 volume"),
        ("simple", "Simple volume"),
        ("stripe", "Striped volume"),
    ),
    ("delete",): (
        ("disk", "Delete a missing dynamic disk"),
        ("partition", "Delete the selected partition"),
        ("volume", "Delete the selected volume"),
        ("vdisk", "Delete a virtual disk"),
    ),
    ("detail",): (
        ("disk", "Details for the selected disk"),
        ("partition", "Details for the selected partition"),
        ("volume", "Details for the selected volume"),
    ),
    ("detach",): (("vdisk", "Detach a virtual disk"),),
    ("expand",): (("vdisk", "Expand a virtual disk file"),),
    ("list",): (
        ("disk", "List disks"),
        ("partition", "List partitions"),
        ("volume", "List volumes"),
        ("vdisk", "List virtual disks"),
    ),
    ("merge",): (("vdisk", "Merge a child virtual disk"),),
    ("offline",): (
        ("disk", "Take the selected disk offline"),
        ("volume", "Take the selected volume offline"),
    ),
    ("online",): (
        ("disk", "Bring the selected disk online"),
        ("volume", "Bring the selected volume online"),
    ),
    ("select",): (
        ("disk", "Select a disk"),
        ("partition", "Select a partition"),
        ("volume", "Select a volume"),
        ("vdisk", "Select a virtual disk"),
    ),
    ("uniqueid",): (
        ("disk", "Disk identifier"),
        ("volume", "Volume identifier"),
    ),
}

_netsh_help_cache: dict[tuple[str, ...], list[tuple[str, str]]] = {}


def diskpart_child_commands(context: Sequence[str]) -> list[tuple[str, str]]:
    key = tuple(part.casefold() for part in context)
    return list(DISKPART_COMMANDS.get(key, ()))


def is_context_switch(description: str) -> bool:
    """True when netsh help says this word only changes context."""
    folded = description.casefold()
    if "changes to the" in folded and "context" in folded:
        return True
    return "컨텍스트" in description and "변경" in description


def netsh_child_commands(context: Sequence[str]) -> list[tuple[str, str]]:
    """Next words under ``netsh <context>``.

    The first visit to a context runs ``netsh <context> ?`` and keeps the
    result. Later keystrokes in that context do not run netsh again.
    """
    key = tuple(part.casefold() for part in context)
    if key not in _netsh_help_cache:
        _netsh_help_cache[key] = parse_netsh_help(_run_netsh_help(context))
    return _next_netsh_words(context, _netsh_help_cache[key])


def parse_netsh_help(text: str) -> list[tuple[str, str]]:
    """Read ``command - description`` lines from netsh help."""
    commands: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line or line[:1].isspace() or " - " not in line:
            continue
        name, description = line.split(" - ", 1)
        name = " ".join(name.split())
        description = description.strip()
        if not name or not description:
            continue
        commands.append((name, description))
    return commands


def _next_netsh_words(
    context: Sequence[str],
    commands: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    items = list(commands)
    if context:
        head = context[-1].casefold()
        stripped: list[tuple[str, str]] = []
        for name, description in commands:
            parts = name.split()
            if len(parts) >= 2 and parts[0].casefold() == head:
                stripped.append((" ".join(parts[1:]), description))
            else:
                stripped = []
                break
        if stripped:
            items = stripped
    seen: dict[str, tuple[str, str]] = {}
    for name, description in items:
        parts = name.split()
        if not parts:
            continue
        word = parts[0]
        key = word.casefold()
        if key not in seen:
            seen[key] = (word, description)
    return list(seen.values())


def _run_netsh_help(context: Sequence[str]) -> str:
    if os.name != "nt":
        return ""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = str(Path(root) / "System32" / "netsh.exe")
    if not os.path.isfile(executable):
        return ""
    prefer_english()
    try:
        completed = subprocess.run(
            [executable, *context, "?"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return decode_console_bytes(completed.stdout)


def decode_console_bytes(data: bytes) -> str:
    """Decode output from cmd or netsh.

    Windows Terminal often emits UTF-8 while the OEM code page stays cp949.
    Decoding those bytes as cp949 turns Korean descriptions into garbage.
    Some programs write UTF-16LE when stdout is a pipe.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    sample = data[:160]
    if len(sample) >= 8 and sample.count(0) >= len(sample) // 4:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return data.decode(_console_encoding(), errors="replace")


def _console_encoding() -> str:
    if os.name != "nt":
        return "utf-8"
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        for getter in (kernel.GetConsoleOutputCP, kernel.GetOEMCP):
            code_page = int(getter())
            if code_page == 65001:
                return "utf-8"
            if code_page > 0:
                return f"cp{code_page}"
    except (AttributeError, OSError, ValueError):
        pass
    return "utf-8"


# Help text for these commands does not list switches unless stdout is a console.
STATIC_SWITCHES: dict[str, tuple[tuple[str, str], ...]] = {
    "sfc": (
        ("/SCANNOW", "Scan protected system files and repair them"),
        ("/VERIFYONLY", "Scan protected system files without repairing"),
        ("/SCANFILE=", "Scan one file and repair it"),
        ("/VERIFYFILE=", "Verify one file without repairing"),
        ("/OFFBOOTDIR=", "Offline boot directory"),
        ("/OFFWINDIR=", "Offline Windows directory"),
    ),
}

_SWITCH_RE = re.compile(r"(?<![\w/])([/-][A-Za-z][A-Za-z0-9_]*)")
_switch_cache: dict[str, list[tuple[str, str]]] = {}


def command_switches(command: str) -> list[tuple[str, str]]:
    """Switches from ``command /?``, plus a few known lists such as sfc."""
    key = command.casefold()
    if key in _switch_cache:
        return _switch_cache[key]
    if os.name != "nt" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]*", command):
        _switch_cache[key] = []
        return []
    parsed = _parse_switches(_read_switch_help(command))
    seen = {name.casefold() for name, _description in parsed}
    for name, description in STATIC_SWITCHES.get(key, ()):
        if name.casefold() not in seen:
            parsed.append((name, description))
            seen.add(name.casefold())
    _switch_cache[key] = parsed
    return parsed


def _parse_switches(text: str) -> list[tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        for match in _SWITCH_RE.finditer(line):
            token = match.group(1)
            end = match.end()
            if end < len(line) and line[end] == "=":
                token += "="
            key = token.casefold()
            if key in found:
                continue
            tail = line[end:].strip(" \t-:=)")
            found[key] = (token, (tail or "option")[:80])
    return list(found.values())


def _read_switch_help(command: str) -> str:
    prefer_english()
    chunks: list[str] = []
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cmd = str(Path(root) / "System32" / "cmd.exe")
    attempts: list[list[str]] = []
    located = _which(command)
    if located:
        attempts.append([located, "/?"])
    attempts.append([cmd, "/d", "/c", f"{command} /?"])
    for args in attempts:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        chunks.append(decode_console_bytes(completed.stdout))
        chunks.append(decode_console_bytes(completed.stderr))
        if _parse_switches("\n".join(chunks)):
            break
    return "\n".join(chunks)


def _which(command: str) -> str | None:
    suffixes = os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";")
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        for suffix in suffixes:
            candidate = Path(directory) / f"{command}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return None

import os
import signal
import subprocess as sp
import sys


SRC_DIR = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SRC_DIR, os.pardir))


def is_windows():
    return sys.platform.startswith("win")


def is_macos():
    return sys.platform == "darwin"


def is_linux():
    return sys.platform.startswith("linux")


def resource_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


def tool_command(name):
    if is_windows() and not name.lower().endswith(".exe"):
        return "{}.exe".format(name)
    return name


def ffmpeg_null_output():
    return "NUL" if is_windows() else "-"


def popen_creation_flags():
    return getattr(sp, "CREATE_NEW_PROCESS_GROUP", 0) if is_windows() else 0


def add_windows_dll_search_paths(*paths):
    if not is_windows():
        return

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return

    for path in paths:
        if not path:
            continue
        try:
            add_dll_directory(path)
        except Exception:
            pass


def prepend_env_path(*paths):
    existing = os.environ.get("PATH", "")
    ordered = []
    seen = set()

    for path in paths:
        if not path:
            continue
        abspath = os.path.abspath(path)
        if abspath in seen:
            continue
        seen.add(abspath)
        ordered.append(abspath)

    if existing:
        ordered.append(existing)

    os.environ["PATH"] = os.pathsep.join(ordered)


def terminate_process(proc):
    if proc is None:
        return

    try:
        if is_windows():
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

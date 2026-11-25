#!/usr/bin/env python3
"""Install the Kartenwerk plugin into a QGIS profile (Linux, Windows, macOS).

The plugin is linked (or copied) into the profile's python/plugins directory
and enabled in the profile's QGIS3.ini. Close QGIS before running this script,
otherwise QGIS may overwrite the settings file on exit.

Alternatively build a ZIP and use QGIS → Plugins → Manage and Install Plugins →
Install from ZIP.

Usage:
    python3 install_plugin.py [--profile NAME] [--copy] [--force]
    python3 install_plugin.py --zip [PATH]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PLUGIN_SRC = PROJECT_DIR / "plugin" / "kartenwerk"


def profiles_root() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "QGIS" / "QGIS3" / "profiles"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "QGIS" / "QGIS3" / "profiles"
    return Path.home() / ".local" / "share" / "QGIS" / "QGIS3" / "profiles"


def active_profile() -> str:
    env = os.environ.get("QGIS_PROFILE")
    if env:
        return env
    profiles_ini = profiles_root() / "profiles.ini"
    if profiles_ini.is_file():
        for line in profiles_ini.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("lastProfile="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    return "default"


def qgis_running() -> bool:
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
            return "qgis" in out
        # Match the process name only: a full command line may merely contain "QGIS"
        # (e.g. an IDE that has this project folder open).
        return subprocess.run(["pgrep", "-ix", r"qgis(-bin|\.bin)?"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def enable_plugin(ini_path: Path) -> bool:
    """Add kartenwerk=true to [PythonPlugins]. Returns True if changed."""
    ini_path.parent.mkdir(parents=True, exist_ok=True)
    text = ini_path.read_text(encoding="utf-8") if ini_path.is_file() else ""
    lines = text.splitlines()

    section = next((i for i, line in enumerate(lines) if line.strip() == "[PythonPlugins]"), None)
    if section is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[PythonPlugins]", "kartenwerk=true"])
        ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True

    end = next((i for i in range(section + 1, len(lines)) if lines[i].lstrip().startswith("[")),
               len(lines))
    for index in range(section + 1, end):
        key, _, value = lines[index].partition("=")
        if key.strip() == "kartenwerk":
            if value.strip().lower() == "true":
                return False
            lines[index] = "kartenwerk=true"
            ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True

    lines.insert(section + 1, "kartenwerk=true")
    ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def build_zip(target: Path) -> Path:
    target = target.resolve()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN_SRC.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                archive.write(path, Path("kartenwerk") / path.relative_to(PLUGIN_SRC))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default=None, help="QGIS profile name (default: last used profile)")
    parser.add_argument("--copy", action="store_true",
                        help="copy the plugin instead of linking it (always used on Windows)")
    parser.add_argument("--force", action="store_true", help="replace an existing plugin directory")
    parser.add_argument("--zip", nargs="?", const=str(PROJECT_DIR / "kartenwerk.zip"), metavar="PATH",
                        help="only build a ZIP for 'Install from ZIP' (default: ./kartenwerk.zip)")
    args = parser.parse_args()

    if not PLUGIN_SRC.is_dir():
        print(f"Error: plugin source not found: {PLUGIN_SRC}", file=sys.stderr)
        return 1

    if args.zip:
        print(f"ZIP written: {build_zip(Path(args.zip))}")
        print("Install it in QGIS: Plugins → Manage and Install Plugins → Install from ZIP.")
        return 0

    if qgis_running():
        print("Warning: QGIS is running. Close it first, otherwise QGIS may overwrite "
              "its settings on exit.", file=sys.stderr)

    profile = args.profile or active_profile()
    profile_dir = profiles_root() / profile
    if not profile_dir.is_dir():
        print(f"Error: QGIS profile not found: {profile_dir}\n"
              "Start QGIS once to create it, or pass --profile.", file=sys.stderr)
        return 1

    plugins_dir = profile_dir / "python" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / "kartenwerk"

    if target.is_symlink():
        target.unlink()
    elif target.exists():
        if not args.force:
            print(f"Error: {target} already exists. Use --force to replace it.", file=sys.stderr)
            return 1
        shutil.rmtree(target)

    # Symlinks on Windows need admin rights or developer mode, so copy there.
    if args.copy or sys.platform.startswith("win"):
        shutil.copytree(PLUGIN_SRC, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        mode = "copied"
    else:
        os.symlink(PLUGIN_SRC, target, target_is_directory=True)
        mode = "linked"

    ini_path = profile_dir / "QGIS" / "QGIS3.ini"
    changed = enable_plugin(ini_path)

    print(f"Plugin {mode}: {target}")
    print(f"Settings:      {ini_path} ({'enabled' if changed else 'already enabled'})")
    print("Restart QGIS. The plugin appears under Plugins → Kartenwerk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

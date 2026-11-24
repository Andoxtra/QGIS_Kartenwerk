#!/usr/bin/env python3
"""Install the Kartenwerk plugin into the active QGIS profile.

The plugin is linked (or copied) into the profile's python/plugins directory
and enabled in the profile's QGIS3.ini. Close QGIS before running this script,
otherwise QGIS may overwrite the settings file on exit.

Usage:
    python3 install_plugin.py [--profile NAME] [--copy] [--force]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PLUGIN_SRC = PROJECT_DIR / "plugin" / "kartenwerk"
PROFILES_ROOT = Path.home() / ".local" / "share" / "QGIS" / "QGIS3" / "profiles"


def active_profile() -> str:
    env = os.environ.get("QGIS_PROFILE")
    if env:
        return env
    profiles_ini = PROFILES_ROOT / "profiles.ini"
    if profiles_ini.is_file():
        for line in profiles_ini.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("lastProfile="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    return "default"


def enable_plugin(ini_path: Path) -> bool:
    """Add kartenwerk=true to [PythonPlugins]. Returns True if changed."""
    ini_path.parent.mkdir(parents=True, exist_ok=True)
    text = ini_path.read_text(encoding="utf-8") if ini_path.is_file() else ""
    lines = text.splitlines()

    section = None
    for index, line in enumerate(lines):
        if line.strip() == "[PythonPlugins]":
            section = index
            break

    if section is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[PythonPlugins]", "kartenwerk=true"])
        ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True

    end = len(lines)
    for index in range(section + 1, len(lines)):
        if lines[index].lstrip().startswith("["):
            end = index
            break

    for line in lines[section + 1:end]:
        if line.split("=", 1)[0].strip() == "kartenwerk":
            return False

    lines.insert(section + 1, "kartenwerk=true")
    ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=active_profile(),
                        help="QGIS profile name (default: active profile)")
    parser.add_argument("--copy", action="store_true",
                        help="copy the plugin instead of creating a symlink")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing plugin directory")
    args = parser.parse_args()

    try:
        running = subprocess.run(
            ["pgrep", "-x", "qgis"], capture_output=True
        ).returncode == 0
    except FileNotFoundError:
        running = False
    if running:
        print("Warnung: QGIS läuft. Bitte QGIS schließen, sonst kann QGIS die "
              "Einstellungen beim Beenden überschreiben.", file=sys.stderr)

    if not PLUGIN_SRC.is_dir():
        print(f"Fehler: Plugin-Quelle nicht gefunden: {PLUGIN_SRC}", file=sys.stderr)
        return 1

    profile_dir = PROFILES_ROOT / args.profile
    if not profile_dir.is_dir():
        print(f"Fehler: QGIS-Profil nicht gefunden: {profile_dir}", file=sys.stderr)
        return 1

    plugins_dir = profile_dir / "python" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / "kartenwerk"

    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        elif args.force:
            shutil.rmtree(target)
        else:
            print(f"Fehler: {target} existiert bereits. Nutze --force zum Ersetzen.",
                  file=sys.stderr)
            return 1

    if args.copy:
        shutil.copytree(PLUGIN_SRC, target)
        mode = "kopiert"
    else:
        os.symlink(PLUGIN_SRC, target, target_is_directory=True)
        mode = "verlinkt"

    ini_path = profile_dir / "QGIS" / "QGIS3.ini"
    changed = enable_plugin(ini_path)

    print(f"Plugin {mode}: {target}")
    print(f"Einstellungen:   {ini_path} ({'aktiviert' if changed else 'bereits aktiv'})")
    print("Bitte QGIS neu starten. Das Plugin erscheint unter Plugins → Kartenwerk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
install_sentinelfoundry.py — create a desktop launcher (with icon) for Sentinel Foundry.

Run ONCE after cloning the repository:

    python install_sentinelfoundry.py        (Windows)
    python3 install_sentinelfoundry.py       (macOS / Linux)

Git cannot ship a desktop icon (shortcuts are machine- and OS-specific), so this
small setup step creates one for your system:
  • Windows → a .lnk on the Desktop, icon = sentinel_foundry.ico
  • Linux   → a .desktop entry (app menu + Desktop), icon = sentinel_foundry.png
  • macOS   → a minimal .app bundle on the Desktop, icon from the .png

It points at sentinel_foundry.py using the Python that runs this script.
Stdlib only.
"""
import os
import sys
import shlex
import shutil
import platform
import subprocess
import tempfile

DIR = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(DIR, "sentinel_foundry.py")
ICO = os.path.join(DIR, "sentinel_foundry.ico")
PNG = os.path.join(DIR, "sentinel_foundry.png")
NAME = "Sentinel Foundry"


def _desktop():
    d = os.path.join(os.path.expanduser("~"), "Desktop")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def _windows():
    py = shutil.which("pythonw") or shutil.which("python") or sys.executable
    lnk = os.path.join(_desktop(), NAME + ".lnk")
    q = lambda s: s.replace("'", "''")
    icon = ("$s.IconLocation='%s';" % q(ICO)) if os.path.isfile(ICO) else ""
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "$s.TargetPath='%s';"
          "$s.Arguments='\"%s\"';"
          "$s.WorkingDirectory='%s';"
          "%s"
          "$s.Description='Sentinel Foundry launcher';"
          "$s.Save()" % (q(lnk), q(py), q(TARGET), q(DIR), icon))
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", ps], check=True)
    return lnk


def _linux():
    exec_line = "%s %s" % (shlex.quote(sys.executable), shlex.quote(TARGET))
    content = ("[Desktop Entry]\n"
               "Type=Application\n"
               "Name=Sentinel Foundry\n"
               "Comment=Sentinel-1 & Sentinel-2 analysis-ready COG indices\n"
               "Exec=%s\n"
               "Path=%s\n"
               "Icon=%s\n"
               "Terminal=false\n"
               "Categories=Science;Education;\n" % (exec_line, DIR, PNG))
    made = []
    apps = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")
    os.makedirs(apps, exist_ok=True)
    for d in (apps, _desktop()):
        p = os.path.join(d, "sentinel-foundry.desktop")
        with open(p, "w") as f:
            f.write(content)
        os.chmod(p, 0o755)
        made.append(p)
    try:  # GNOME: mark the desktop launcher trusted
        subprocess.run(["gio", "set", os.path.join(_desktop(), "sentinel-foundry.desktop"),
                        "metadata::trusted", "true"], capture_output=True)
    except Exception:
        pass
    return made


def _png_to_icns(png, icns):
    try:
        iconset = tempfile.mkdtemp(suffix=".iconset")
        for sz in (16, 32, 128, 256, 512):
            subprocess.run(["sips", "-z", str(sz), str(sz), png, "--out",
                            os.path.join(iconset, "icon_%dx%d.png" % (sz, sz))],
                           check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False


def _macos():
    app = os.path.join(_desktop(), NAME + ".app")
    macos_dir = os.path.join(app, "Contents", "MacOS")
    res_dir = os.path.join(app, "Contents", "Resources")
    os.makedirs(macos_dir, exist_ok=True)
    os.makedirs(res_dir, exist_ok=True)
    run = os.path.join(macos_dir, "run")
    with open(run, "w") as f:
        f.write('#!/bin/bash\ncd %s\nexec %s %s\n'
                % (shlex.quote(DIR), shlex.quote(sys.executable), shlex.quote(TARGET)))
    os.chmod(run, 0o755)
    icon_key = ""
    if os.path.isfile(PNG) and _png_to_icns(PNG, os.path.join(res_dir, "appicon.icns")):
        icon_key = "    <key>CFBundleIconFile</key>\n    <string>appicon</string>\n"
    with open(os.path.join(app, "Contents", "Info.plist"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                '    <key>CFBundleName</key><string>Sentinel Foundry</string>\n'
                '    <key>CFBundleExecutable</key><string>run</string>\n'
                '    <key>CFBundleIdentifier</key><string>org.sentinelfoundry.launcher</string>\n'
                '    <key>CFBundlePackageType</key><string>APPL</string>\n'
                + icon_key +
                '</dict></plist>\n')
    return app


def main():
    if not os.path.isfile(TARGET):
        print("Cannot find sentinel_foundry.py next to this script — run it from the repo folder.")
        return 1
    system = platform.system()
    try:
        if system == "Windows":
            out = _windows()
        elif system == "Darwin":
            out = _macos()
        else:
            out = _linux()
        print("Created desktop launcher:")
        for p in (out if isinstance(out, list) else [out]):
            print("  " + p)
        print("\nYou can now open Sentinel Foundry from your desktop.")
        if system == "Darwin":
            print("First time on macOS: right-click the app → Open (to bypass Gatekeeper).")
        return 0
    except Exception as e:
        print("Could not create the desktop launcher: %s" % e)
        print("You can still run it with:  python sentinel_foundry.py")
        return 1


if __name__ == "__main__":
    sys.exit(main())

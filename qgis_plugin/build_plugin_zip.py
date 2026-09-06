#!/usr/bin/env python3
"""Build the QGIS plugin ZIP for a given version (default: from metadata).

Usage: python3 build_plugin_zip.py [version]

Produces plugin_dist/sentinel1_windthrow_plugin_v<version>.zip with the
package root folder (sentinel1_windthrow_plugin/...) inside, plus a copy
in /home/z/my-project/download/.  Excludes __pycache__ and test caches.
"""
import os
import shutil
import sys
import zipfile

PLUGIN_ROOT = os.path.dirname(os.path.abspath(__file__))
PKG = "sentinel1_windthrow_plugin"
DIST = os.path.join(os.path.dirname(PLUGIN_ROOT), "plugin_dist")
DOWNLOAD = "/home/z/my-project/download"
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git"}


def main() -> None:
    version = None
    if len(sys.argv) > 1:
        version = sys.argv[1]
    else:
        with open(os.path.join(PLUGIN_ROOT, PKG, "metadata.txt"),
                  encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("version="):
                    version = line.split("=", 1)[1].strip()
                    break
    if not version:
        raise SystemExit("version not found")
    out_name = f"{PKG}_v{version}.zip"
    out_path = os.path.join(DIST, out_name)
    os.makedirs(DIST, exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)
    src = os.path.join(PLUGIN_ROOT, PKG)
    n = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.join(PKG, os.path.relpath(full, src))
                z.write(full, rel)
                n += 1
    os.makedirs(DOWNLOAD, exist_ok=True)
    shutil.copy2(out_path, os.path.join(DOWNLOAD, out_name))
    print(f"OK {out_name}: {n} files -> {out_path} (+download)")


if __name__ == "__main__":
    main()

"""
check_safe.py
=============
Standalone integrity checker for downloaded Sentinel-1 GRD products.

Verifies the .zip archives and/or extracted .SAFE directories produced by the
SAR Foundry download step (s1_pipeline_ui.py) so you can catch corrupt or
truncated downloads *before* feeding them to SNAP.

It targets the failure mode seen with parallel ASF downloads - a complete-
looking archive whose VV band is silently truncated - which shows up here as:
  * a .zip whose CRC test fails (zipfile.testzip), and
  * a VV measurement file that is missing, unreadable, or far smaller than its
    VH counterpart (the two bands of a dual-pol GRD scene are normally similar
    in size).

Usage:
    python check_safe.py                       # auto-detect the SAFE folder
    python check_safe.py  E:/path/to/safe_dir  # scan a given folder (report only)
    python check_safe.py  .  --delete          # scan and DELETE corrupted products
    python check_safe.py  .  --no-crc          # skip the slow per-byte zip CRC test
    python check_safe.py  .  --no-deep         # skip the rasterio band-read test

Exit code is 0 if everything is OK, 1 if any product is CORRUPT or SUSPECT.
Requires only the standard library; uses rasterio for a deeper read if present.
"""

import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

try:
    import rasterio
    HAS_RASTERIO = True
except Exception:
    HAS_RASTERIO = False


def _gdal_ok():
    """True only if rasterio can actually create AND read a raster. A broken
    GDAL/DLL env (the documented Windows Smart-App-Control case) imports fine
    but throws at open — which would mark every scene CORRUPT and, under
    --delete, mass-delete valid data. When this returns False we skip the deep
    raster read so an env failure can never drive a deletion."""
    if not HAS_RASTERIO:
        return False
    try:
        import numpy as np, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sf_gdal_selftest.tif")
            with rasterio.open(p, "w", driver="GTiff", height=1, width=1,
                               count=1, dtype="uint8") as dst:
                dst.write(np.ones((1, 1), dtype="uint8"), 1)
            with rasterio.open(p) as src:
                src.read(1)
        return True
    except Exception:
        return False

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SIZE_ASYMMETRY_RATIO = 0.5            # flag if smaller band < 50% of the larger band
MIN_BAND_BYTES       = 50 * 1024**2   # a real IW GRD band is hundreds of MB; <50MB = bad

OK, SUSPECT, CORRUPT = "OK", "SUSPECT", "CORRUPT"


# ---- helpers -----------------------------------------------------------------

def _human(n):
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"   # ponytail: unreachable, but keeps the return total


def _expected_pols(name):
    """Polarisations a VV/VH scene should contain, parsed from its product-ID
    token (…_1SDV_… = dual VV+VH, …_1SSV_… = single VV). Returns a set of
    'vv'/'vh', or None when it can't be determined or the scene is an HH/HV
    product (those bands aren't tracked here). Used so a legitimate single-pol
    GRD is never treated as a dual-pol scene with a missing band."""
    import re
    m = re.search(r"_1s([sd])([vh])_", os.path.basename(name).lower())
    if not m or m.group(2) != "v":     # 'v' primary = VV(+VH); 'h' = HH/HV, skip
        return None
    return {"vv", "vh"} if m.group(1) == "d" else {"vv"}


import re as _re
_POL_RE = _re.compile(r"[-_]v([vh])[-_.]")   # delimited pol token, not a substring


def _band_of(name):
    """Return 'vv'/'vh' if the filename is a measurement band, else None."""
    m = _POL_RE.search(os.path.basename(name).lower())
    return ("v" + m.group(1)) if m else None


def _is_tiff_header(path):
    """True if the file starts with a valid TIFF / BigTIFF magic number."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        return head[:2] in (b"II", b"MM") and \
            head[2:4] in (b"\x2a\x00", b"\x00\x2a", b"\x2b\x00", b"\x00\x2b")
    except Exception:
        return False


def _check_band_pair(sizes, issues, expected=None):
    """sizes = {'vv': bytes, 'vh': bytes} - append issues for missing/tiny/asymmetric.
    A *missing* band is only SUSPECT, never CORRUPT: a valid single-pol GRD
    legitimately ships one band, so it must never be auto-deleted. A band that
    IS present but truncated (< MIN_BAND_BYTES) is still CORRUPT."""
    want = expected if expected else {"vv", "vh"}
    for pol in ("vv", "vh"):
        if pol not in sizes:
            if pol in want:
                issues.append((SUSPECT, "missing %s measurement band "
                               "(single-pol scene? not deleted)" % pol.upper()))
        elif sizes[pol] < MIN_BAND_BYTES:
            issues.append((CORRUPT, "%s band only %s (expected hundreds of MB)"
                           % (pol.upper(), _human(sizes[pol]))))
    if sizes.get("vv") and sizes.get("vh"):
        lo, hi = sorted((sizes["vv"], sizes["vh"]))
        if hi and lo / hi < SIZE_ASYMMETRY_RATIO:
            smaller = "VV" if sizes["vv"] < sizes["vh"] else "VH"
            issues.append((SUSPECT,
                           "%s band much smaller than the other "
                           "(VV=%s, VH=%s) - possible truncation"
                           % (smaller, _human(sizes["vv"]), _human(sizes["vh"]))))


def _worst(issues):
    if not issues:
        return OK, []
    status = CORRUPT if any(s == CORRUPT for s, _ in issues) else SUSPECT
    return status, [r for _, r in issues]


# ---- .zip checker ------------------------------------------------------------

def check_zip(path, do_crc=True):
    """Return (status, [reasons]) for a downloaded .SAFE .zip archive."""
    issues = []
    if not zipfile.is_zipfile(path):
        return CORRUPT, ["not a valid zip (truncated or wrong file type)"]
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if do_crc:
                bad = z.testzip()           # reads every byte - definitive CRC test
                if bad is not None:
                    issues.append((CORRUPT, "CRC failed on member: %s" % bad))
            if not any(n.lower().endswith("manifest.safe") for n in names):
                issues.append((CORRUPT, "manifest.safe not found inside archive"))
            sizes = {}
            for info in z.infolist():
                fn = info.filename.lower()
                if "/measurement/" in fn and fn.endswith((".tiff", ".tif")):
                    pol = _band_of(info.filename)
                    if pol:
                        sizes[pol] = max(sizes.get(pol, 0), info.file_size)
            _check_band_pair(sizes, issues, _expected_pols(path))
    except zipfile.BadZipFile as e:
        return CORRUPT, ["bad zip: %s" % e]
    except Exception as e:
        return CORRUPT, ["error reading zip: %s" % e]
    return _worst(issues)


# ---- .SAFE directory checker -------------------------------------------------

def check_safe(safe_path, deep=True):
    """Return (status, [reasons]) for an extracted .SAFE directory."""
    issues = []

    manifest = os.path.join(safe_path, "manifest.safe")
    if not os.path.isfile(manifest) or os.path.getsize(manifest) == 0:
        issues.append((CORRUPT, "manifest.safe missing or empty"))
    else:
        try:
            ET.parse(manifest)
        except Exception as e:
            issues.append((CORRUPT, "manifest.safe not valid XML: %s" % e))

    meas = os.path.join(safe_path, "measurement")
    if not os.path.isdir(meas):
        issues.append((CORRUPT, "measurement/ folder missing"))
        return _worst(issues)

    tiffs = [os.path.join(meas, f) for f in os.listdir(meas)
             if f.lower().endswith((".tiff", ".tif"))]
    if not tiffs:
        issues.append((CORRUPT, "no measurement GeoTIFFs found"))
        return _worst(issues)

    sizes = {}
    for t in tiffs:
        pol = _band_of(t)
        if pol:
            sizes[pol] = max(sizes.get(pol, 0), os.path.getsize(t))
    _check_band_pair(sizes, issues, _expected_pols(safe_path))

    for t in tiffs:
        if not _is_tiff_header(t):
            issues.append((CORRUPT, "%s: invalid TIFF header" % os.path.basename(t)))
            continue
        if deep and HAS_RASTERIO:
            try:
                with rasterio.open(t) as src:
                    h, w = src.height, src.width
                    win = rasterio.windows.Window(0, max(0, h - 4),
                                                  min(w, 256), min(h, 4))
                    src.read(1, window=win)   # truncation usually loses the tail
            except Exception as e:
                issues.append((CORRUPT, "%s: unreadable / truncated (%s)"
                               % (os.path.basename(t), e)))
    return _worst(issues)


# ---- folder discovery + driver -----------------------------------------------

def _auto_folder():
    """Use the SAR Foundry config's safe_dir if available, else current dir."""
    cfg_path = os.path.join(_SCRIPT_DIR, "sar_foundry_config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                sd = json.load(f).get("safe_dir", "").strip()
            if sd and os.path.isdir(sd):
                return sd
        except Exception:
            pass
    return os.getcwd()


def main():
    args        = sys.argv[1:]
    delete_mode = "--delete" in args
    do_crc      = "--no-crc" not in args
    deep        = "--no-deep" not in args
    positional  = [a for a in args if not a.startswith("--")]
    root        = os.path.abspath(positional[0] if positional else _auto_folder())

    # B8 guard: if the raster env is broken, disable the deep read so a driver
    # failure can't flag every scene CORRUPT and delete valid downloads.
    gdal_broken = deep and HAS_RASTERIO and not _gdal_ok()
    if gdal_broken:
        deep = False
        print("WARNING: rasterio/GDAL is installed but cannot open a raster — the\n"
              "         environment looks broken (see the Windows Smart-App-Control\n"
              "         note in the README). Skipping the deep raster-read test so\n"
              "         valid scenes are NOT mis-flagged as corrupt.\n")

    print("Scanning : %s" % root)
    print("Mode     : %s" % ("SCAN + DELETE corrupted products" if delete_mode
                             else "SCAN only  (add --delete to remove corrupted)"))
    print("Checks   : zip CRC=%s   raster read=%s%s"
          % ("on" if do_crc else "off",
             "on" if (HAS_RASTERIO and deep) else "off",
             "" if HAS_RASTERIO else " (rasterio not installed)"))
    print()

    if not os.path.isdir(root):
        print("ERROR: folder not found: %s" % root)
        return 2

    zips  = sorted(f for f in os.listdir(root) if f.lower().endswith(".zip"))
    safes = sorted(f for f in os.listdir(root)
                   if f.lower().endswith(".safe")
                   and os.path.isdir(os.path.join(root, f)))

    if not zips and not safes:
        print("No .zip or .SAFE products found in this folder.")
        return 0

    results = []
    for name in zips:
        status, reasons = check_zip(os.path.join(root, name), do_crc=do_crc)
        results.append((name, status, reasons))
    for name in safes:
        status, reasons = check_safe(os.path.join(root, name), deep=deep)
        results.append((name, status, reasons))

    icon = {OK: "  OK     ", SUSPECT: "  SUSPECT", CORRUPT: "  CORRUPT"}
    n_ok = n_susp = n_bad = 0
    deleted, failed_del = [], []

    for name, status, reasons in sorted(results):
        print("%s  %s" % (icon[status], name))
        for r in reasons:
            print("             - %s" % r)
        if status == OK:
            n_ok += 1
        elif status == SUSPECT:
            n_susp += 1
        else:
            n_bad += 1
            if delete_mode:
                full = os.path.join(root, name)
                try:
                    shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
                    deleted.append(name)
                    print("             --> DELETED")
                except Exception as e:
                    failed_del.append(name)
                    print("             --> DELETE FAILED: %s" % e)

    print("\n" + "=" * 64)
    print("Total: %d   OK: %d   SUSPECT: %d   CORRUPT: %d"
          % (len(results), n_ok, n_susp, n_bad))

    bad_names = [n for n, s, _ in results if s in (CORRUPT, SUSPECT)]
    if bad_names and not delete_mode:
        print("\nProducts to re-download / inspect:")
        for n in bad_names:
            print("  %s" % n)
    if delete_mode and deleted:
        print("\nDeleted %d corrupt product(s) - re-run the pipeline to re-download."
              % len(deleted))
    if failed_del:
        print("\nFailed to delete (%d): %s" % (len(failed_del), ", ".join(failed_del)))

    return 0 if (n_bad == 0 and n_susp == 0) else 1


if __name__ == "__main__":
    sys.exit(main())

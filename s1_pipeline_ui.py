"""
s1_pipeline_ui.py - SAR Foundry
Run: python s1_pipeline_ui.py
(venv is created and packages installed automatically on first run)
"""
import os, sys

_here = os.path.dirname(os.path.abspath(__file__))

def _find_venv_py():
    """Return the correct python path for the .venv, whether it exists or not."""
    win = os.path.join(_here, ".venv", "Scripts", "python.exe")
    nix = os.path.join(_here, ".venv", "bin", "python")
    if os.path.isfile(win): return win
    if os.path.isfile(nix): return nix
    # venv not yet created — return platform-appropriate path for after creation
    import platform
    return win if platform.system() == "Windows" else nix

def _in_venv():
    return os.path.abspath(sys.executable).startswith(
        os.path.abspath(os.path.join(_here, ".venv")))

if not _in_venv():
    venv_dir = os.path.join(_here, ".venv")
    if not os.path.isdir(venv_dir):
        print("[SETUP] Creating .venv ...")
        import subprocess
        r = subprocess.run([sys.executable, "-m", "venv", venv_dir])
        if r.returncode != 0:
            input("[ERROR] Could not create venv. Press Enter."); sys.exit(1)
    # resolve python path AFTER venv creation
    _venv_py = _find_venv_py()
    req = os.path.join(_here, "requirements.txt")
    if os.path.isfile(req):
        import subprocess, threading
        print("[SETUP] Installing packages (first run) ...")
        # show a spinner while pip runs
        _done = [False]
        def _spin():
            import time
            chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            i = 0
            while not _done[0]:
                print(f"\r  {chars[i % len(chars)]} installing...", end="", flush=True)
                i += 1
                time.sleep(0.1)
            print("\r  done.                    ")
        t = threading.Thread(target=_spin, daemon=True); t.start()
        r = subprocess.run([_venv_py, "-m", "pip", "install", "-r", req,
                            "--quiet", "--no-warn-script-location"],
                           capture_output=True, text=True)
        _done[0] = True; t.join(timeout=1)
        if r.returncode != 0:
            print(r.stderr[:500])
            input("[ERROR] pip install failed. Press Enter."); sys.exit(1)
        print("[SETUP] All packages installed.")
    import subprocess
    sys.exit(subprocess.run([_venv_py] + sys.argv).returncode)

_venv_py = _find_venv_py()



# stdlib imports (available once inside the venv)
import re, glob, shutil, subprocess, tempfile, threading, time

# Prevent child console programs (SNAP gpt, gdal, pip, …) from opening empty
# terminal windows when this GUI app runs without a console (Windows only).
# subprocess.run() goes through Popen, so patching Popen.__init__ covers all.
if os.name == "nt":
    import subprocess as _sp_nw
    _CREATE_NO_WINDOW = getattr(_sp_nw, "CREATE_NO_WINDOW", 0x08000000)
    _orig_popen_init_nw = _sp_nw.Popen.__init__
    def _popen_init_nw(self, *a, **k):
        if "creationflags" not in k:
            k["creationflags"] = _CREATE_NO_WINDOW
        _orig_popen_init_nw(self, *a, **k)
    _sp_nw.Popen.__init__ = _popen_init_nw
from concurrent.futures import ThreadPoolExecutor, as_completed

# Redirect GDAL/libtiff messages away from the terminal and into the
# pipeline log panel instead (so exported logs still contain them).
import os as _os, tempfile as _tempfile
_GDAL_LOG = _os.path.join(_tempfile.gettempdir(), "sar_foundry_gdal.log")
_os.environ.setdefault("CPL_LOG", _GDAL_LOG)
_os.environ.setdefault("CPL_LOG_ERRORS", "ON")   # keep messages, just not in terminal
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import xml.etree.ElementTree as ET

try:
    from tkcalendar import DateEntry as _DateEntry
    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

# ─────────────────────────────────────────────────────────────────────────────
# DEPENDENCY CHECK  (run at startup, results shown in UI status bar)
# ─────────────────────────────────────────────────────────────────────────────

def check_dependencies(gpt_path, gdal_path=None):
    """
    Returns dict of {name: (ok: bool, detail: str)}.
    Tries to call each tool and captures version string.
    """
    results = {}

    # ── SNAP GPT ──────────────────────────────────────────────────────────────
    gpt = gpt_path if os.path.isfile(gpt_path) else shutil.which("gpt") or shutil.which("gpt.bat") or ""
    if gpt and os.path.isfile(gpt):
        try:
            # Probe an actual SAR operator (not just --help): this forces SNAP to
            # load the Microwave / Sentinel-1 Toolbox (eu.esa.sar.*).  If that
            # toolbox is missing or broken, gpt crashes here with a Java
            # ClassNotFoundException / NoClassDefFoundError for eu.esa.sar.* —
            # exactly the failure that would otherwise kill every scene at step 2.
            r = subprocess.run([gpt, "Calibration", "-h"],
                               capture_output=True, text=True, timeout=150)
            combined = (r.stdout or "") + (r.stderr or "")
            low = combined.lower()
            sar_broken = ("eu.esa.sar" in low) and (
                "classnotfound" in low or "noclassdeffound" in low
                or "could not be instantiated" in low)
            if sar_broken or r.returncode != 0:
                results["SNAP GPT"] = (False,
                    f"{gpt}\n"
                    "SNAP runs but cannot load the SAR operators — the\n"
                    "Microwave Toolbox (= Sentinel-1 Toolbox) is missing or broken.\n"
                    "The pipeline WILL FAIL at SNAP processing for every scene.\n"
                    "HOW TO FIX:\n"
                    "  1. Reinstall SNAP and TICK the 'Microwave Toolbox' component\n"
                    "     (named 'Sentinel-1 Toolbox' in SNAP <= 11)\n"
                    "  2. Open SNAP once -> Help -> Check for Updates -> restart\n"
                    "  3. Verify in a terminal: gpt Calibration -h  (no Java errors)")
            else:
                ver = ""
                for line in combined.splitlines():
                    if "version" in line.lower() or "snap" in line.lower():
                        ver = line.strip()[:80]
                        break
                results["SNAP GPT"] = (True,
                    f"{gpt}\nSAR operators OK" + (f" — {ver}" if ver else ""))
        except subprocess.TimeoutExpired:
            # JVM cold-start on Windows can take 60-90 s on first call — file
            # exists, so treat as OK but note operators were not verified.
            results["SNAP GPT"] = (True,
                f"{gpt}\n(JVM slow first start — SAR operators not verified)")
        except Exception as e:
            results["SNAP GPT"] = (False, f"Found at {gpt} but failed to run: {e}")
    else:
        results["SNAP GPT"] = (False,
            f"Not found at {gpt_path}\n"
            "HOW TO INSTALL:\n"
            "  1. Download SNAP from https://step.esa.int/main/download/snap-download/\n"
            "  2. Choose 'All Toolboxes' installer\n"
            "  3. Install to default: C:\\Program Files\\esa-snap\\\n"
            "  4. Open SNAP GUI once to complete setup\n"
            "  5. Update the GPT path in section 8 of this UI if needed")

    # ── gdal_translate ────────────────────────────────────────────────────────
    gdal = gdal_path
    if not gdal or not os.path.isfile(str(gdal)):
        gdal = shutil.which("gdal_translate")
    if not gdal:
        # scan Program Files for any QGIS installation
        pf = r"C:\Program Files"
        candidates = [
            r"C:\OSGeo4W\bin\gdal_translate.exe",
            r"C:\OSGeo4W64\bin\gdal_translate.exe",
        ]
        if os.path.isdir(pf):
            for entry in sorted(os.listdir(pf), reverse=True):
                if entry.lower().startswith("qgis"):
                    exe = os.path.join(pf, entry, "bin", "gdal_translate.exe")
                    candidates.insert(0, exe)
        for c in candidates:
            if os.path.isfile(c):
                gdal = c
                break
    if gdal and os.path.isfile(str(gdal)):
        try:
            r = subprocess.run([gdal, "--version"], capture_output=True, text=True, timeout=10)
            ver = (r.stdout + r.stderr).strip()[:80]
            results["GDAL (gdal_translate)"] = (True, f"{gdal}\n{ver}")
        except Exception as e:
            results["GDAL (gdal_translate)"] = (False, f"Found but failed: {e}")
    else:
        results["GDAL (gdal_translate)"] = (False,
            "Not found on PATH or any known location.\n"
            "HOW TO INSTALL (pick one):\n"
            "  OPTION A — via QGIS (recommended):\n"
            "    1. Download QGIS from https://qgis.org/en/site/forusers/download.html\n"
            "    2. Install QGIS\n"
            "    3. Add to PATH: C:\\Program Files\\QGIS 3.xx\\bin\n"
            "    4. Open a NEW terminal and test: gdal_translate --version\n"
            "  OPTION B — via conda:\n"
            "    conda install -c conda-forge gdal\n"
            "  OPTION C — via OSGeo4W:\n"
            "    https://trac.osgeo.org/osgeo4w/ -> select gdal package\n"
            "    Add C:\\OSGeo4W\\bin to PATH")

    # ── Python packages ───────────────────────────────────────────────────────
    packages = {
        "rasterio":   "rasterio",
        "asf_search": "asf_search",
        "geopandas":  "geopandas",
        "shapely":    "shapely",
        "numpy":      "numpy",
        "tkcalendar": "tkcalendar",
        "psutil":     "psutil",
    }
    missing = []
    for pkg, imp in packages.items():
        try:
            mod = __import__(imp)
            ver = getattr(mod, "__version__", "?")
        except ImportError:
            missing.append(pkg)
            ver = None
    if missing:
        results["Python packages"] = ("install",
            f"Missing: {', '.join(missing)}\n"
            f"Click INSTALL to install them automatically in the venv.\n"
            f"Command: pip install {' '.join(missing)}")
    else:
        results["Python packages"] = (True, "rasterio, asf_search, geopandas, shapely, numpy, tkcalendar, psutil — all present")

    # ── unzip accelerator (optional — zipfile always works as fallback) ────────
    try:
        _kind, _exe = _find_fast_extractor()
    except Exception:
        _kind, _exe = ("zipfile", None)
    if _kind == "7z":
        results["Unzip tool"] = (True, f"7-Zip ({_exe}) — fast native .SAFE extraction")
    elif _kind == "tar":
        results["Unzip tool"] = (True, f"bsdtar ({_exe}) — fast native extraction; install 7-Zip for the quickest path")
    else:
        results["Unzip tool"] = ("info",
            "Python zipfile (slow on large products) — install 7-Zip for much faster unzip: https://www.7-zip.org/")

    return results


# ── dark theme — coral red accent for SAR Foundry ─────────────────────────────
BG        = "#1A1A2E"   # form panel background
BG2       = "#252542"   # card / section background
SURFACE   = "#0F0F1F"   # log & deep background
ACCENT    = "#FF5252"   # coral red (primary)
ACCENT2   = "#C62828"   # darker red (hover / banner start)
ACCENT_L  = "#FF8A80"   # light coral (secondary text)
FG        = "#E8EAED"   # primary text
FG2       = "#78909C"   # secondary / hint text
LOG_GREEN = "#66BB6A"   # success in log
GOLD      = "#FFA726"   # warning
RED       = "#EF5350"   # error
WHITE     = "#FFFFFF"
# legacy aliases kept so existing code doesn't break
DARK  = BG2
GREEN = ACCENT
MOSS  = ACCENT2
_FONT_FAM = "Segoe UI"
FONT      = (_FONT_FAM, 10)
FONT_BOLD = (_FONT_FAM, 10, "bold")
FONT_H    = (_FONT_FAM, 12, "bold")
FONT_MONO = ("Consolas", 9)

NODATA    = -9999.0


def _get_total_ram_gb():
    """Return total system RAM in GB — tries psutil, then OS calls, then 16 GB fallback."""
    try:
        import psutil
        return psutil.virtual_memory().total / 1024 ** 3
    except ImportError:
        pass
    try:
        import platform as _plt
        if _plt.system() == "Windows":
            import ctypes
            class _MEM(ctypes.Structure):
                _fields_ = [("dwLength",         ctypes.c_ulong),
                             ("dwMemoryLoad",     ctypes.c_ulong),
                             ("ullTotalPhys",     ctypes.c_ulonglong),
                             ("ullAvailPhys",     ctypes.c_ulonglong),
                             ("ullTotalPageFile", ctypes.c_ulonglong),
                             ("ullAvailPageFile", ctypes.c_ulonglong),
                             ("ullTotalVirtual",  ctypes.c_ulonglong),
                             ("ullAvailVirtual",  ctypes.c_ulonglong)]
            s = _MEM(); s.dwLength = ctypes.sizeof(s)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
            return s.ullTotalPhys / 1024 ** 3
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / 1024 ** 2
    except Exception:
        pass
    return 16.0  # conservative fallback


def _safe_snap_workers(jvm_mb):
    """Return a safe default number of parallel SNAP jobs for this machine.

    Each worker is a full JVM (jvm_mb) PLUS gdal children and, in the per-batch
    pipeline, a concurrent indexing pass — so 'JVM nominally fits in total RAM'
    oversubscribes and pushes the machine into paging (measured: 2×10 GB JVM on a
    32 GB laptop with a browser open → 98% commit, <1 GB free, SNAP thrashing).
    Size off *currently available* RAM (which already excludes other running
    apps) minus a headroom buffer for OS cache + gdal + indexing.
    ponytail: 6 GB buffer + total*0.6 fallback are heuristics; raise the buffer
    if paging still shows up on a given box."""
    jvm_gb = max(1.0, jvm_mb / 1024.0)
    buffer_gb = 6.0                      # OS cache + gdal + per-batch indexing
    try:
        import psutil
        budget = psutil.virtual_memory().available / 1024 ** 3
    except Exception:
        budget = _get_total_ram_gb() * 0.6   # no psutil → assume ~40% already used
    return max(1, min(4, int((budget - buffer_gb) / jvm_gb)))


# Default graph: look next to the script file (works on any machine with same folder layout)
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GRAPH = os.path.join(_SCRIPT_DIR,
    "snap_graphs", "s1_preprocessing_graph.xml")
import platform as _platform
if _platform.system() == "Windows":
    DEFAULT_GPT = r"C:\Program Files\esa-snap\bin\gpt.EXE"
elif _platform.system() == "Darwin":
    DEFAULT_GPT = "/Applications/snap/bin/gpt"
else:
    DEFAULT_GPT = os.path.expanduser("~/snap/bin/gpt")

ALL_BANDS = ["VV", "VH", "CR", "RVI", "DIFF"]

# Pretty pipeline labels for the Batch tab. Internal keys stay sigma0/gamma0
# (folder names, graph selection); only the UI text changes.
# σ⁰ = Filipponi (2019 GRD workflow); γ⁰ = Small (2011 gamma-flattening RTC).
PRESET_LABELS = {"sigma0": "σ⁰ (Filipponi)", "gamma0": "γ⁰ (Small)"}
PRESET_KEYS   = {v: k for k, v in PRESET_LABELS.items()}

_CONFIG_FILE = os.path.join(_SCRIPT_DIR, "sar_foundry_config.json")

def _load_config():
    """Load saved user preferences from JSON."""
    import json
    if os.path.isfile(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[CONFIG] Could not read {_CONFIG_FILE} ({e}); using defaults")
    return {}

def _save_config(data: dict):
    """Save user preferences to JSON next to the script."""
    import json
    try:
        existing = _load_config()
        existing.update(data)
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"[CONFIG] Could not save: {e}")


def _safe_union(gdf):
    """Union all geometries, repairing invalid ones first. Invalid polygons
    (self-intersections / holes not assignable to a shell) otherwise raise
    `TopologyException: unable to assign free hole to a shell` in union_all."""
    try:
        g = gdf.geometry.make_valid()
    except Exception:
        g = gdf.geometry.buffer(0)
    try:
        return g.union_all() if hasattr(g, "union_all") else g.unary_union
    except Exception:
        g = g.buffer(0)
        return g.union_all() if hasattr(g, "union_all") else g.unary_union


def _drawn_aoi_path():
    """Path to save the drawn AOI: the script folder if writable, else a temp
    dir (so it never fails silently on a read-only install)."""
    import tempfile
    try:
        if os.access(_SCRIPT_DIR, os.W_OK):
            return os.path.join(_SCRIPT_DIR, "drawn_aoi.geojson")
    except Exception:
        pass
    return os.path.join(tempfile.gettempdir(), "drawn_aoi.geojson")


# ═══════════════════════════════════════════════════════════════════════════
# ERROR LOGGING HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _error_dir(cfg):
    """Return the folder where per-scene error logs are written.
    Priority: COG indices folder > SNAP folder > SAFE folder."""
    base = cfg.get("out_dir") or cfg.get("snap_dir") or cfg.get("safe_dir", ".")
    return os.path.join(base, "pipeline_errors")

def _write_error(cfg, stem, phase, message):
    """Write a per-scene error log file so the user knows what to retry."""
    edir = _error_dir(cfg)
    os.makedirs(edir, exist_ok=True)
    fname = os.path.join(edir, f"{stem}__{phase}.error.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(f"Pipeline Error Log\n{'='*60}\n")
        f.write(f"Phase  : {phase}\n")
        f.write(f"Scene  : {stem}\n")
        f.write(f"Time   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"\nError:\n{message}\n")
        f.write(f"\n{'='*60}\n")
        f.write("To retry: re-run the pipeline covering the date in the scene name.\n")
        f.write("The scene name contains the acquisition date (YYYYMMDD).\n")


def _clear_error(cfg, stem, phase):
    """Delete a per-scene error log once that step succeeds, so a recovered
    scene (esp. on retry) isn't falsely re-reported in the end-of-run summary."""
    try:
        os.remove(os.path.join(_error_dir(cfg), f"{stem}__{phase}.error.txt"))
    except OSError:
        pass


def _pending_index_error(cfg, date, orbit):
    """True if a previous run left an unresolved indices error for this
    date/orbit, so it must be reprocessed rather than skipped."""
    try:
        return os.path.isfile(os.path.join(
            _error_dir(cfg), f"S1_{date}_{orbit}__indices.error.txt"))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE LOGIC  (runs in background thread)
# ═══════════════════════════════════════════════════════════════════════════

def _reproject_outputs(out_dir: str, target_crs: str, log) -> None:
    """Reproject all COG GeoTIFFs in out_dir to target_crs in-place (if different)."""
    import glob, tempfile, shutil
    import rasterio
    from rasterio.warp import calculate_default_transform, reproject, Resampling

    tifs = glob.glob(os.path.join(out_dir, "**", "*.tif"), recursive=True)
    if not tifs:
        return
    log(f"  Reprojecting {len(tifs)} files to {target_crs} ...")
    done = 0
    for tif in tifs:
        _part = None
        try:
            with rasterio.open(tif) as src:
                if str(src.crs).upper() == target_crs.upper():
                    continue
                transform, width, height = calculate_default_transform(
                    src.crs, target_crs, src.width, src.height, *src.bounds)
                meta = src.meta.copy()
                meta.update({"crs": target_crs, "transform": transform,
                              "width": width, "height": height,
                              "driver": "GTiff", "compress": "deflate",
                              "tiled": True, "blockxsize": 512, "blockysize": 512,
                              "bigtiff": "IF_SAFER"})
                _part = tif + ".reproj.tif"   # same dir -> os.replace is atomic
                with rasterio.open(_part, "w", **meta) as dst:
                    for i in range(1, src.count + 1):
                        reproject(source=rasterio.band(src, i),
                                  destination=rasterio.band(dst, i),
                                  src_transform=src.transform,
                                  src_crs=src.crs,
                                  dst_transform=transform,
                                  dst_crs=target_crs,
                                  resampling=Resampling.bilinear)
            os.replace(_part, tif)
            done += 1
        except Exception as e:
            if _part:
                try: os.remove(_part)
                except Exception: pass
            log(f"    WARNING: could not reproject {os.path.basename(tif)}: {e}")
    log(f"  Reprojection done: {done} files")


def run_pipeline(cfg, log, done_cb, progress_cb=None):
    """
    cfg: dict with all user settings
    log: callable(str) — sends text to the UI log
    done_cb: callable(success: bool) — called when finished
    progress_cb: callable(phase, current, total, label) — updates UI progress bars
    """
    if progress_cb is None:
        progress_cb = lambda phase, cur, tot, lbl="": None
    try:
        log("="*60)
        log(f"Pipeline started  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log("="*60)

        safe_dir   = cfg["safe_dir"]
        snap_dir   = cfg["snap_dir"]
        out_dir    = cfg["out_dir"]
        # Optional separate .SAFE output dir (e.g. a faster/healthier drive). Zips are
        # still read from — and deleted in — safe_dir; blank → .SAFE stay in safe_dir.
        safe_out   = (cfg.get("safe_out_dir") or "").strip() or safe_dir
        if safe_out != safe_dir and cfg.get("dl_source") == "cdse_s3":
            log("  ⚠ CDSE S3 delivers .SAFE directly — '.SAFE unzip folder' ignored "
                "(using the download folder).")
            safe_out = safe_dir
        os.makedirs(safe_dir,  exist_ok=True)
        os.makedirs(snap_dir,  exist_ok=True)
        os.makedirs(out_dir,   exist_ok=True)
        os.makedirs(safe_out,  exist_ok=True)
        if safe_out != safe_dir:
            log(f"  .SAFE output → {safe_out}   (zips read from {safe_dir})")

        # Optional batching: cap how much .SAFE sits on disk at once by processing in
        # chunks (extract → SNAP → delete, repeat) so a small/slow output drive never
        # fills. Needs SNAP enabled (nothing consumes/frees .SAFE otherwise).
        _batch_gb = _resolve_batch_gb(cfg, safe_out)
        _batching = _batch_gb > 0 and cfg.get("do_snap")
        if _batching:
            cfg["_batch_defer"] = True   # keep the download path from unzipping; we chunk below
            log(f"  Batch mode ON: ≤ {_batch_gb:g} GB of .SAFE at a time (extract → SNAP → delete per chunk).")

        # ── step 1: download ─────────────────────────────────────────────
        # Broken/empty .SAFE products (interrupted download or extraction) are
        # removed first — they fail SNAP and block re-download/unzip otherwise.
        _broken_safes = _prune_broken_safes(safe_out, log)
        failed_downloads = []
        if cfg["do_download"]:
            failed_downloads = _download_dispatch(cfg, safe_dir, log, progress_cb)
        else:
            log("\n── STEP 1: Skipped (use existing .SAFE files) ──")
            _stop_ev = cfg.get("stop_event")
            # Re-fetch any broken products so a single corrupt download doesn't
            # silently drop a scene, even though download is otherwise off.
            if _broken_safes:
                log(f"  {len(_broken_safes)} broken/incomplete .SAFE found — "
                    f"attempting re-download…")
                _redownload_missing(cfg, safe_dir, _broken_safes, log, progress_cb)
            # Still unzip any .zip files present — they won't be found by SNAP otherwise.
            # Batch mode does its own chunked extract in step 2, so skip the bulk unzip here.
            _zips = glob.glob(os.path.join(safe_dir, "*.zip"))
            if _batching and _zips:
                log(f"  Found {len(_zips)} .zip archive(s) — will extract in batches (step 2).")
            elif _zips:
                log(f"  Found {len(_zips)} .zip archive(s) in folder — extracting before SNAP…")
                _unzip_zips(safe_dir, log, progress_cb, _stop_ev, cfg)
            else:
                log(f"  Folder contains .SAFE directories — ready for SNAP.")

        # ── stop check ───────────────────────────────────────────────────
        if cfg.get("stop_event") and cfg["stop_event"].is_set():
            log("[Stopped by user]")
            done_cb(False); return

        # ── step 2: SNAP processing ───────────────────────────────────────
        if _batching:
            cfg.pop("_batch_defer", None)
            log("\n── STEP 1b+2: Batched  extract → SNAP → delete .SAFE  (per chunk) ──")
            _process_in_batches(cfg, safe_dir, safe_out, snap_dir, _batch_gb,
                                 log, progress_cb, cfg.get("stop_event"))
        elif cfg["do_snap"]:
            log("\n── STEP 2: SNAP GPT preprocessing ──")
            _snap_process(cfg, safe_out, snap_dir, log, progress_cb)
        else:
            log("\n── STEP 2: Skipped ──")

        # ── cleanup .SAFE source dirs (optional; batch mode already deletes per chunk) ──
        if not _batching and cfg.get("clean_safe") and cfg["do_snap"]:
            log("\n── Deleting .SAFE source files (only those converted to GeoTIFF) ──")
            _clean_safe(safe_out, log, snap_dir, cfg)

        # ── stop check ───────────────────────────────────────────────────
        if cfg.get("stop_event") and cfg["stop_event"].is_set():
            log("[Stopped by user]")
            done_cb(False); return

        # ── step 3: compute indices ───────────────────────────────────────
        if cfg["do_indices"]:
            log("\n── STEP 3: Computing indices ──")
            _compute_indices(cfg, snap_dir, out_dir, log, progress_cb)
        else:
            log("\n── STEP 3: Skipped ──")

        # SNAP tiles are deleted per-scene on success inside _compute_indices.
        # Any tile still present here means its indices step failed — keep it for retry.

        # ── optional CRS reprojection ─────────────────────────────
        target_crs = cfg.get("output_crs", "").strip()
        if target_crs and target_crs.upper() not in ("AUTO", "UTM", ""):
            log(f"\n── Reprojecting outputs to {target_crs} ──")
            _reproject_outputs(cfg["out_dir"], target_crs, log)

        # ── retry failed downloads (ASF only) ────────────────────────────
        if (failed_downloads and cfg.get("retry_download", True)
                and cfg.get("dl_source", "asf") == "asf"):
            log(f"\n── RETRY: re-attempting {len(failed_downloads)} failed download(s) ──")
            import asf_search as _asf2
            token = cfg.get("asf_token","").strip()
            _sess = None
            try:
                if token:
                    _sess = _asf2.ASFSession().auth_with_token(token)
                else:
                    # asf_search 8+ dropped auth_with_netrc — read ~/.netrc ourselves
                    import netrc as _netrc
                    _u, _, _p = _netrc.netrc().authenticators("urs.earthdata.nasa.gov")
                    _sess = _asf2.ASFSession().auth_with_creds(_u, _p)
            except Exception as _re:
                log(f"  Retry auth failed: {_re}")
            if _sess is not None:
                _mount_retries(_sess)
            _retry_failed = []
            if _sess:
                for _ri, _sc in enumerate(failed_downloads, 1):
                    _nm = _sc.properties.get("sceneName","")
                    _dt = _sc.properties.get("startTime","")[:10]
                    _dr = _sc.properties.get("flightDirection","?")[:3].upper()
                    log(f"  [retry {_ri}/{len(failed_downloads)}] {_dt} {_dr}  {_nm[:40]}...")
                    progress_cb("download", _ri-1, len(failed_downloads), f"retry {_dt}...")
                    _ok, _re2 = _download_scene_with_retries(
                        _sc, safe_dir, _sess, _asf2, log, _nm, attempts=2)
                    if _ok:
                        log(f"     OK retry")
                        _clear_error(cfg, _nm, "download")   # recovered on retry
                        progress_cb("download", _ri, len(failed_downloads), f"retry ok")
                    else:
                        log(f"     FAILED retry: {_re2}")
                        _retry_failed.append(_sc)
                        progress_cb("download", _ri, len(failed_downloads), f"retry fail")
            if _retry_failed:
                log(f"  {len(_retry_failed)} scene(s) still failed after retry.")
            elif failed_downloads:
                log("  All retries succeeded.")
            # Extract any newly-downloaded .zip archives so they are ready for
            # SNAP (the main download pass already unzipped earlier ones).
            _unzip_zips(safe_dir, log, progress_cb, cfg.get("stop_event"), cfg)

        # ── error summary ─────────────────────────────────────────────────
        _edir = _error_dir(cfg)
        _errs = glob.glob(os.path.join(_edir, "*.error.txt"))
        log("\n" + "="*60)
        if _errs:
            log(f"Pipeline complete — {len(_errs)} scene(s) had errors.")
            log(f"Error logs saved to: {_edir}")
            for _ef in sorted(_errs):
                log(f"  ✗ {os.path.basename(_ef)}")
        else:
            log("Pipeline complete — no errors.")
        log("="*60)
        done_cb(True)

    except Exception as e:
        import traceback
        log(f"\nERROR: {e}")
        log(traceback.format_exc())
        done_cb(False)


# ── step 1: download ──────────────────────────────────────────────────────────

def _mount_retries(session, total=4, backoff=2.0):
    """Attach urllib3 connection-level retries with exponential backoff to a
    requests session (covers dropped connections, 429 and 5xx responses)."""
    try:
        from requests.adapters import HTTPAdapter
        try:
            from urllib3.util.retry import Retry
        except ImportError:                       # very old urllib3 layout
            from requests.packages.urllib3.util.retry import Retry
        retry = Retry(
            total=total, connect=total, read=total, status=total,
            backoff_factor=backoff,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    except Exception:
        # Retries are best-effort hardening — never block downloads if the
        # adapter cannot be configured in this environment.
        pass
    return session


def _download_scene_with_retries(scene, safe_dir, session, asf, log, name,
                                 attempts=3):
    """Download one ASF scene, retrying with exponential backoff.
    asf_search has no resume, so any partial .zip is removed between attempts
    to avoid leaving a truncated archive behind."""
    zip_path = os.path.join(safe_dir, name + ".zip")
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            scene.download(path=safe_dir, session=session,
                           fileType=asf.FileDownloadType.DEFAULT_FILE)
            return True, None
        except Exception as e:
            last_err = e
            try:
                if os.path.isfile(zip_path):
                    os.remove(zip_path)
            except Exception:
                pass
            if attempt < attempts:
                wait = 2 ** attempt
                log(f"     ⚠ attempt {attempt}/{attempts} failed: {e}"
                    f" — retrying in {wait}s")
                time.sleep(wait)
    return False, last_err


def _download(cfg, safe_dir, log, progress_cb=None):
    if progress_cb is None:
        progress_cb = lambda *a, **kw: None
    import warnings as _warnings
    try:
        import asf_search as asf
        import geopandas as gpd
        from shapely.geometry import mapping
    except ImportError as e:
        raise ImportError(f"Missing package: {e}. Run: pip install asf_search geopandas shapely")

    # redirect Python warnings (e.g. WKT repair messages) to the UI log
    _orig_showwarning = _warnings.showwarning
    def _log_warning(message, category, filename, lineno, file=None, line=None):
        log(f"  [WARNING] {category.__name__}: {message}")
    _warnings.showwarning = _log_warning

    aoi_path = cfg["aoi_path"]
    gdf = gpd.read_file(aoi_path).to_crs("EPSG:4326")
    union = _safe_union(gdf)
    # CMR rejects complex WKTs (many vertices) with HTTP 500
    # simplify to convex hull if polygon has many vertices, else use bounding box
    # Count vertices recursively — a MultiPolygon (scattered field clusters) has
    # no .exterior, so the old hasattr check saw 0 and sent the full detailed
    # geometry, which CMR rejects with HTTP 500 (mirror of the CDSE 414 fix).
    def _vcount(geom):
        if hasattr(geom, "geoms"):
            return sum(_vcount(g) for g in geom.geoms)
        return len(geom.exterior.coords) if hasattr(geom, "exterior") else 0
    nverts = _vcount(union)
    if nverts > 50:
        search_geom = union.convex_hull
        log(f"  AOI simplified to convex hull ({nverts} vertices → CMR limit)")
    else:
        search_geom = union
    # round coordinates to 4 decimal places — CMR chokes on float64 precision strings
    from shapely.wkt import dumps as _wkt_dumps
    aoi_wkt = _wkt_dumps(search_geom, rounding_precision=4)

    start = cfg["start_date"] + "T00:00:00Z"
    end   = cfg["end_date"]   + "T23:59:59Z"

    # ── CMR health check before searching ────────────────────────────────────
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen(
                "https://cmr.earthdata.nasa.gov/search/health", timeout=6) as _r:
            _h = _json.loads(_r.read())
        _indexer_ok = _h.get("indexer", {}).get("ok?", False)
        if not _indexer_ok:
            _prob = _h.get("indexer", {}).get("dependencies", {})
            log("⚠  NASA CMR indexer is currently DOWN — granule search will fail.")
            log("   Check https://cmr.earthdata.nasa.gov/search/health for status.")
            log(f"   Details: {_prob}")
            log("   Try again in 30–60 minutes.")
            return
    except Exception as _ce:
        log(f"⚠  Could not reach NASA CMR health endpoint ({_ce}). Proceeding anyway...")

    log(f"Searching ASF: {cfg['start_date']} → {cfg['end_date']}")
    search_kwargs = dict(
        platform=asf.PLATFORM.SENTINEL1,
        processingLevel=asf.PRODUCT_TYPE.GRD_HD,
        beamMode=asf.BEAMMODE.IW,
        polarization=asf.POLARIZATION.VV_VH,
        start=start, end=end,
    )
    try:
        results = asf.search(intersectsWith=aoi_wkt, **search_kwargs)
    except Exception as e:
        if any(c in str(e) for c in ("500", "502", "503")):
            minx, miny, maxx, maxy = search_geom.bounds
            bbox_wkt = (f"POLYGON(({minx} {miny},{maxx} {miny},"
                        f"{maxx} {maxy},{minx} {maxy},{minx} {miny}))")
            log(f"  WKT search failed ({e}) — retrying with bounding box...")
            results = asf.search(intersectsWith=bbox_wkt, **search_kwargs)
        else:
            raise
    results = sorted(results, key=lambda r: r.properties.get("startTime", ""))
    log(f"Found {len(results)} scenes total")

    # Retry run: keep only scenes acquired on the failed dates.
    _retry_dates = cfg.get("retry_dates")
    if _retry_dates:
        results = [r for r in results
                   if r.properties.get("startTime", "")[:10] in _retry_dates]
        log(f"Retry filter: {len(results)} scene(s) on {len(_retry_dates)} failed date(s)")

    # filter by orbit direction
    orbit_filter = cfg["orbit_dir"]
    if orbit_filter != "Both":
        direction = "ASCENDING" if orbit_filter == "ASC" else "DESCENDING"
        results = [r for r in results
                   if r.properties.get("flightDirection","").upper() == direction]
        log(f"After {orbit_filter} filter: {len(results)} scenes")

    _sats = _selected_sats(cfg)
    if _sats:
        results = [r for r in results if r.properties.get("sceneName", "")[:3] in _sats]
        log(f"Satellite filter {sorted(_sats)}: {len(results)} scene(s)")

    if not results:
        log("No scenes found — check AOI, dates, orbit and satellite selection")
        return

    # authenticate — Earthdata Bearer token, else .netrc. Username/password is
    # intentionally unsupported: the asf_search creds path is broken and it only
    # widens the credential surface (see audit B2 / PRD).
    token = cfg.get("asf_token","").strip()
    session = None
    try:
        if token:
            try:
                session = asf.ASFSession().auth_with_token(token)
                log("  Authenticated with Bearer token")
            except Exception as tok_err:
                log(f"  Token auth failed: {tok_err} — trying .netrc...")
        if session is None:
            try:
                import netrc
                user, _, passwd = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
                session = asf.ASFSession().auth_with_creds(user, passwd)
                log("  Authenticated via .netrc")
            except Exception as netrc_err:
                raise RuntimeError(
                    "ASF authentication failed.\n"
                    "  → Paste an Earthdata Bearer token (Get token at "
                    "https://urs.earthdata.nasa.gov/documentation/for_users/user_token)\n"
                    "  → Or set up a ~/.netrc entry for urs.earthdata.nasa.gov"
                ) from netrc_err
    finally:
        _warnings.showwarning = _orig_showwarning

    # Harden the session against transient connection drops / 5xx responses.
    if session is not None:
        _mount_retries(session)

    n_dl          = len(results)
    _stop_ev_dl   = cfg.get("stop_event")
    dl_done       = 0
    failed_scenes = []
    _lock         = threading.Lock()

    def _scan_bytes(path, max_depth=1):
        total = 0
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_file(follow_symlinks=False):
                            total += e.stat().st_size
                        elif e.is_dir(follow_symlinks=False) and max_depth > 0:
                            total += _scan_bytes(e.path, max_depth - 1)
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    # ── Real-time speed monitor ───────────────────────────────────────────
    _mon_stop = threading.Event()
    _prev     = [_scan_bytes(safe_dir, max_depth=0), time.time()]

    def _speed_monitor():
        while not _mon_stop.is_set():
            time.sleep(1)
            sz = _scan_bytes(safe_dir, max_depth=0)   # only the in-flight zip(s)
            t  = time.time()
            with _lock:
                delta_b = sz - _prev[0]; delta_t = t - _prev[1]
                _prev[0] = sz; _prev[1] = t
                done = dl_done
            if delta_t > 0 and delta_b > 1024:
                speed = delta_b * 8 / delta_t / 1_048_576
                progress_cb("download", done, n_dl,
                            f"↓ {speed:.0f} Mbps  ({done}/{n_dl} done)",
                            speed=f"{speed:.0f} Mbps")

    _mon_thread = threading.Thread(target=_speed_monitor, daemon=True)
    _mon_thread.start()

    # ── Sequential download loop (one scene at a time) ────────────────────
    log("  Download mode: sequential (1 scene at a time)")
    try:
        for i, scene in enumerate(results, 1):
            if _stop_ev_dl and _stop_ev_dl.is_set():
                log("  [Stopped by user]")
                break

            name      = scene.properties.get("sceneName", "")
            date      = scene.properties.get("startTime", "")[:10]
            dirn      = scene.properties.get("flightDirection", "?")[:3].upper()
            safe_path = os.path.join((cfg.get("safe_out_dir") or "").strip() or safe_dir, name + ".SAFE")
            zip_path  = os.path.join(safe_dir, name + ".zip")

            # skip if already extracted or zip already present
            if os.path.isdir(safe_path) or os.path.isfile(zip_path):
                dl_done += 1
                log(f"  [{i}/{n_dl}] SKIP {date} {dirn}  (exists)")
                progress_cb("download", dl_done, n_dl, f"{date} {dirn}  skip")
                continue

            log(f"  [{i}/{n_dl}] ↓ {date} {dirn}  {name[:38]}…")
            t0        = time.time()
            sz_before = _scan_bytes(safe_dir)
            ok, _dl_err = _download_scene_with_retries(
                scene, safe_dir, session, asf, log, name, attempts=3)
            if ok:
                elapsed  = time.time() - t0
                sz_after = _scan_bytes(safe_dir)
                delta_mb = max(0, sz_after - sz_before) / 1_048_576
                spd_str  = f"{delta_mb * 8 / elapsed:.0f} Mbps" if elapsed > 0 else ""
                dl_done += 1
                progress_cb("download", dl_done, n_dl, f"{date} {dirn} ✓ {delta_mb:.0f}MB {spd_str}",
                            speed=spd_str)
                log(f"     ✓  {elapsed:.0f}s  {delta_mb:.0f} MB  {spd_str}")
                _clear_error(cfg, name, "download")   # recovered — drop stale log
            else:
                _write_error(cfg, name, "download", f"{_dl_err}")
                log(f"     ✗ ERROR after retries: {_dl_err}")
                failed_scenes.append(scene)
                progress_cb("download", dl_done, n_dl, f"{date} {dirn}  ✗")
    finally:
        _mon_stop.set()
        _mon_thread.join(timeout=2)

    _unzip_zips(safe_dir, log, progress_cb, _stop_ev_dl, cfg)
    return failed_scenes


def _cdse_auth_data(cfg):
    """Build the CDSE token-endpoint payload for the *initial* login only.
    Username/password (ROPC) grant. The password is sent on this call and
    never again — every renewal after this uses the rotating refresh token
    CDSE hands back (see _refresh_token), so the password itself is never
    stored or resent."""
    user = cfg.get("cdse_user", "").strip()
    pwd  = cfg.get("cdse_pass", "")
    if user and pwd:
        return {"grant_type": "password", "username": user, "password": pwd}
    raise RuntimeError(
        "Copernicus CDSE credentials missing.\n"
        "Enter your CDSE username/password in section 6b of the Download tab.\n"
        "If CDSE keeps failing (MFA-protected accounts can't use password login),"
        " switch 'Download source' to ASF instead.")


def _selected_sats(cfg):
    """Set of satellite codes to keep (e.g. {'S1C'}), or None to keep all.
    Used to fetch only certain platforms — e.g. S1C-only to backfill AOIs already
    downloaded from ASF (which has no S1C). Codes match the scene-name prefix."""
    sats = cfg.get("satellites")
    if not sats:
        return None
    s = {x.upper() for x in sats}
    return None if s >= {"S1A", "S1B", "S1C"} else s


def _cdse_search(cfg, log):
    """Search the CDSE catalogue (public OData, no auth) for Sentinel-1 IW GRDH
    scenes over the AOI + date range. Returns a list of product dicts (each has
    Name, Id, S3Path, ContentDate, Attributes). Shared by the CDSE OData and S3
    downloaders. Returns [] if nothing matches."""
    import requests
    import geopandas as gpd
    from shapely.wkt import dumps as _wkt_dumps

    aoi_path = cfg["aoi_path"]
    gdf = gpd.read_file(aoi_path).to_crs("EPSG:4326")
    union = _safe_union(gdf)
    # ponytail: MultiPolygon AOIs (multiple field clusters) have no .exterior,
    # so vertex count must recurse into .geoms or a scattered-cluster AOI's
    # full detailed shape gets sent as WKT and CDSE 414s on the URL length.
    def _vertex_count(geom):
        if hasattr(geom, "geoms"):
            return sum(_vertex_count(g) for g in geom.geoms)
        return len(geom.exterior.coords) if hasattr(geom, "exterior") else 0
    search_geom = union.convex_hull if _vertex_count(union) > 50 else union
    aoi_wkt = _wkt_dumps(search_geom, rounding_precision=4)

    start_str    = cfg["start_date"]
    end_str      = cfg["end_date"]
    orbit_filter = cfg.get("orbit_dir", "Both")

    orbit_clause = ""
    if orbit_filter != "Both":
        direction = "ASCENDING" if orbit_filter == "ASC" else "DESCENDING"
        orbit_clause = (
            " and Attributes/OData.CSC.StringAttribute/any(att:"
            "att/Name eq 'orbitDirection' and "
            f"att/OData.CSC.StringAttribute/Value eq '{direction}')")

    # ponytail: the strict `polarisationChannels eq 'VV&VH'` clause was dropped
    # (B1) — the '&' encoding is brittle in OData and silently returned 0 hits.
    # IW_GRDH is VV+VH in practice; check_safe handles the odd single-pol scene.
    odata_filter = (
        "Collection/Name eq 'SENTINEL-1'"
        " and Attributes/OData.CSC.StringAttribute/any(att:"
        "att/Name eq 'productType' and "
        "att/OData.CSC.StringAttribute/Value eq 'IW_GRDH_1S')"
        f" and ContentDate/Start ge {start_str}T00:00:00.000Z"
        f" and ContentDate/Start le {end_str}T23:59:59.000Z"
        f" and OData.CSC.Intersects(area=geography'SRID=4326;{aoi_wkt}')"
        f"{orbit_clause}"
    )

    SEARCH_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
    log(f"Searching CDSE: {start_str} → {end_str}")
    all_items = []
    skip, page = 0, 100
    while True:
        resp = requests.get(SEARCH_URL, params={
            "$filter":  odata_filter,
            "$orderby": "ContentDate/Start",
            "$top":     page,
            "$skip":    skip,
            "$expand":  "Attributes",
        }, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"CDSE search error (HTTP {resp.status_code}): {resp.text[:300]}")
        items = resp.json().get("value", [])
        all_items.extend(items)
        if len(items) < page:
            break
        skip += page
        if skip > 10_000:
            log("  ⚠ Search hit the 10 000-result cap — the scene list is INCOMPLETE. "
                "Narrow the date range (or split it) and re-run to get the rest.")
            break

    log(f"Found {len(all_items)} scenes total")

    # Retry run: keep only scenes acquired on the failed dates.
    _retry_dates = cfg.get("retry_dates")
    if _retry_dates:
        all_items = [it for it in all_items
                     if it.get("ContentDate", {}).get("Start", "")[:10] in _retry_dates]
        log(f"Retry filter: {len(all_items)} scene(s) on {len(_retry_dates)} failed date(s)")

    _sats = _selected_sats(cfg)
    if _sats:
        all_items = [it for it in all_items if it.get("Name", "")[:3] in _sats]
        log(f"Satellite filter {sorted(_sats)}: {len(all_items)} scene(s)")

    if not all_items:
        log("No scenes found — check AOI, dates, orbit and satellite selection")
    return all_items


def _download_cdse(cfg, safe_dir, log, progress_cb=None):
    """Download S1 GRD scenes from Copernicus Data Space Ecosystem (CDSE)."""
    if progress_cb is None:
        progress_cb = lambda *a, **kw: None

    try:
        import requests
    except ImportError:
        raise ImportError("requests package needed for CDSE. Run: pip install requests")
    try:
        import geopandas as gpd
        from shapely.wkt import dumps as _wkt_dumps
    except ImportError as e:
        raise ImportError(f"Missing package: {e}. Run: pip install geopandas shapely")

    # ── Authenticate with Keycloak (OIDC) ─────────────────────────────────────
    # Username/password (ROPC) grant, used only for this initial login call —
    # the original B1 bug was silently re-sending the password on every
    # *refresh*, not using it once up front. Access tokens live ~10 min; the
    # refresh token CDSE hands back (~60 min) renews them without ever
    # touching the password again (see _refresh_token). Doesn't work for
    # MFA-protected accounts — that's a Keycloak limitation, not fixable
    # here. Any auth failure raises a clear error suggesting the ASF source,
    # never hangs.
    _cdse_auth_data(cfg)   # fail fast if no username/password

    TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
                 "/protocol/openid-connect/token")

    def _token_request(data, what):
        data.setdefault("client_id", "cdse-public")
        try:
            r = requests.post(TOKEN_URL, data=data, timeout=30)
        except Exception as e:
            raise RuntimeError(
                f"Could not reach the CDSE auth server ({e}).\n"
                "Check your connection, or switch 'Download source' to ASF.") from e
        if r.status_code != 200:
            raise RuntimeError(
                f"CDSE {what} failed (HTTP {r.status_code}).\n"
                "Verify your token/credentials at https://dataspace.copernicus.eu/,\n"
                "or switch 'Download source' to ASF as a fallback.\n"
                f"Details: {r.text[:300]}")
        j = r.json()
        return j.get("access_token"), j.get("refresh_token")

    def _auth_initial():
        return _token_request(_cdse_auth_data(cfg), "login")

    def _refresh_token():
        """New access token from the stored refresh token; fall back to a full
        re-auth if the refresh token is expired/revoked. Returns (access, refresh)."""
        rt = _tok_state.get("refresh")
        if rt:
            try:
                at, new_rt = _token_request(
                    {"grant_type": "refresh_token", "refresh_token": rt}, "token refresh")
                return at, (new_rt or rt)
            except Exception:
                pass   # refresh token dead → re-run the initial grant
        return _auth_initial()

    log("  Authenticating with Copernicus CDSE...")
    access_token, refresh_token = _auth_initial()
    log("  Authenticated with CDSE")
    _token_ts = time.time()   # track when token was issued (expires ~600 s)

    all_items = _cdse_search(cfg, log)
    if not all_items:
        return []

    # ── Download loop (parallel) ──────────────────────────────────────────────
    # CDSE serves files from S3-compatible object storage — no server-side
    # truncation issue like ASF.  Bearer tokens are stateless JWTs and can be
    # shared across threads; only token *refresh* needs a lock.
    import threading as _thr
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    _stop_ev   = cfg.get("stop_event")
    _force_ev  = cfg.get("force_event")   # 2nd Stop press → abort the in-flight scene too
    n_workers  = max(1, min(cfg.get("max_dl_workers", 1), 5))
    n_dl       = len(all_items)
    dl_done    = 0
    failed     = []
    _lock      = _thr.Lock()          # protects token, dl_done, failed
    _tok_state = {"token": access_token, "refresh": refresh_token,
                  "ts": _token_ts}   # mutable ref
    _sess      = _mount_retries(requests.Session())   # connection-level retries

    log(f"  Download mode: {'parallel (' + str(n_workers) + ' workers)' if n_workers > 1 else 'sequential (1 worker)'}"
        f"  —  CDSE object storage, no truncation risk")

    def _dl_one(args):
        nonlocal dl_done
        i, scene = args
        if _stop_ev and _stop_ev.is_set():
            return None  # skipped

        name     = scene.get("Name", "")
        scene_id = scene.get("Id", "")
        date     = scene.get("ContentDate", {}).get("Start", "")[:10]
        dirn     = "?"
        for a in scene.get("Attributes", []):
            if a.get("Name") == "orbitDirection":
                dirn = "ASC" if a.get("Value","").upper().startswith("ASC") else "DSC"
                break

        # Normalise to the bare product id (drop CDSE's trailing .SAFE) so the
        # zip is named <product>.zip exactly like ASF's. One spelling means a
        # scene already fetched from either source is recognised — and skipped —
        # by the other, and unzip yields <product>.SAFE, not <product>.SAFE.SAFE.
        base      = name[:-5] if name.endswith(".SAFE") else name
        safe_path = os.path.join((cfg.get("safe_out_dir") or "").strip() or safe_dir, base + ".SAFE")
        zip_path  = os.path.join(safe_dir, base + ".zip")

        if os.path.isdir(safe_path) or os.path.isfile(zip_path):
            with _lock:
                dl_done += 1; _c = dl_done
            log(f"  [{i}/{n_dl}] SKIP {date} {dirn}  (exists)")
            progress_cb("download", _c, n_dl, f"{date} {dirn}  skip")
            return None

        # Refresh token if stale (only one thread does it at a time)
        with _lock:
            if time.time() - _tok_state["ts"] > 500:
                try:
                    _tok_state["token"], _tok_state["refresh"] = _refresh_token()
                    _tok_state["ts"]    = time.time()
                    log("  Token refreshed")
                except Exception as _te:
                    log(f"  ⚠ Token refresh failed: {_te}")
            _bearer = _tok_state["token"]

        log(f"  [{i}/{n_dl}] ↓ {date} {dirn}  {name[:38]}…")
        t0      = time.time()
        dl_url  = (f"https://download.dataspace.copernicus.eu"
                   f"/odata/v1/Products({scene_id})/$value")
        zip_tmp = zip_path + ".part"

        # Per-scene attempt loop with exponential backoff.  A partial .part file
        # is kept between attempts and resumed via an HTTP Range request, so a
        # dropped connection mid-transfer does not throw the whole file away.
        max_attempts = 4
        last_err     = None

        for attempt in range(1, max_attempts + 1):
            if _stop_ev and _stop_ev.is_set():
                break

            resume_from = os.path.getsize(zip_tmp) if os.path.isfile(zip_tmp) else 0
            with _lock:
                _bearer = _tok_state["token"]
            hdrs = {"Authorization": f"Bearer {_bearer}"}
            if resume_from > 0:
                hdrs["Range"] = f"bytes={resume_from}-"

            try:
                # (connect timeout, read timeout-between-chunks)
                with _sess.get(dl_url, headers=hdrs, stream=True,
                               timeout=(30, 300)) as r:
                    if r.status_code == 401:
                        with _lock:
                            _tok_state["token"], _tok_state["refresh"] = _refresh_token()
                            _tok_state["ts"]    = time.time()
                            _bearer = _tok_state["token"]
                        hdrs["Authorization"] = f"Bearer {_bearer}"
                        r = _sess.get(dl_url, headers=hdrs, stream=True,
                                      timeout=(30, 300))
                    # If we requested a range but the server ignored it (200),
                    # restart from scratch to avoid corrupt concatenation.
                    if resume_from > 0 and r.status_code == 200:
                        resume_from = 0
                        try: os.remove(zip_tmp)
                        except Exception: pass
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                    # Total size for a live %/speed readout: Content-Range on a
                    # resumed (206) transfer, else Content-Length on a fresh one.
                    total_sz = None
                    _cr = r.headers.get("Content-Range", "")
                    if "/" in _cr:
                        try: total_sz = int(_cr.rsplit("/", 1)[1])
                        except Exception: pass
                    elif r.headers.get("Content-Length"):
                        try: total_sz = resume_from + int(r.headers["Content-Length"])
                        except Exception: pass
                    written = resume_from
                    mode    = "ab" if resume_from > 0 else "wb"
                    _last_emit = time.time()
                    with open(zip_tmp, mode) as fh:
                        for chunk in r.iter_content(chunk_size=65536):
                            # Graceful stop lets the in-flight scene finish (like ASF,
                            # whose blocking download has no mid-stream hook); only a
                            # 2nd Stop press (force) aborts mid-transfer.
                            if _force_ev and _force_ev.is_set():
                                break
                            if chunk:
                                fh.write(chunk); written += len(chunk)
                                # live speed/% ~1x per second (the count-based bar
                                # can't move within one scene, so show it in text)
                                _now = time.time()
                                if _now - _last_emit >= 1.0:
                                    _el   = _now - t0
                                    _mbps = (written - resume_from) * 8 / _el / 1e6 if _el > 0 else 0
                                    _mb   = written / 1_048_576
                                    if total_sz:
                                        _lbl = f"↓ {date} {written/total_sz*100:.0f}% {_mb:.0f}MB {_mbps:.0f}Mbps"
                                    else:
                                        _lbl = f"↓ {date} {_mb:.0f}MB {_mbps:.0f}Mbps"
                                    progress_cb("download", dl_done, n_dl, _lbl,
                                                speed=f"{_mbps:.0f} Mbps")
                                    _last_emit = _now

                if _force_ev and _force_ev.is_set():
                    # force-killed mid-scene — keep the .part so a later run resumes
                    return None

                os.rename(zip_tmp, zip_path)
                elapsed = time.time() - t0
                mb      = written / 1_048_576
                spd_str = f"{mb * 8 / elapsed:.0f} Mbps" if elapsed > 0 else ""
                with _lock:
                    dl_done += 1; _c = dl_done
                progress_cb("download", _c, n_dl, f"{date} {dirn} ✓ {mb:.0f}MB {spd_str}",
                            speed=spd_str)
                log(f"     ✓  {elapsed:.0f}s  {mb:.0f} MB  {spd_str}"
                    + ("  (resumed)" if resume_from > 0 else ""))
                _clear_error(cfg, name, "download_cdse")   # recovered — drop stale log
                return None

            except Exception as _dl_err:
                last_err = _dl_err
                if attempt < max_attempts and not (_stop_ev and _stop_ev.is_set()):
                    wait = 2 ** attempt
                    have = os.path.getsize(zip_tmp) if os.path.isfile(zip_tmp) else 0
                    log(f"     ⚠ attempt {attempt}/{max_attempts} failed: {_dl_err}"
                        f" — retrying in {wait}s (have {have // 1_048_576} MB)")
                    time.sleep(wait)
                # keep the .part file so the next attempt can resume

        # all attempts exhausted (or stopped)
        _write_error(cfg, name, "download_cdse", f"{last_err}")
        log(f"     ✗ ERROR after {max_attempts} attempts: {last_err}")
        with _lock:
            failed.append(scene)
            dl_done += 1; _c = dl_done
        progress_cb("download", _c, n_dl, f"{date} {dirn}  ✗")
        return None

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_dl_one, (i, s)): i
                   for i, s in enumerate(all_items, 1)}
        for fut in _as_completed(futures):
            fut.result()   # surface any unexpected exception to the log

    _unzip_zips(safe_dir, log, progress_cb, _stop_ev, cfg)
    return failed


def _download_cdse_s3(cfg, safe_dir, log, progress_cb=None):
    """Download .SAFE products straight from CDSE's S3 object store (eodata bucket).

    Bypasses the OData /$value session throttle and delivers the already-extracted
    .SAFE folder, so there is NO unzip step. Each S3 credential is capped by CDSE at
    ~20 MB/s (160 Mbit/s) and 12 TB/month. Needs S3 keys (section 6c) generated at the
    CDSE S3 keys manager. Scenes are fetched one at a time (graceful Stop finishes the
    current scene, a 2nd Stop aborts it); the many files inside each .SAFE are pulled
    in parallel — that is where the speed comes from."""
    if progress_cb is None:
        progress_cb = lambda *a, **kw: None
    try:
        import boto3
        from botocore.config import Config as _BotoCfg
    except ImportError:
        raise ImportError("boto3 needed for CDSE S3. Run: pip install boto3")
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed2

    ak = cfg.get("s3_access", "").strip()
    sk = cfg.get("s3_secret", "").strip()
    if not (ak and sk):
        raise RuntimeError(
            "No CDSE S3 keys configured.\n"
            "Generate an access key + secret at\n"
            "https://eodata-s3keysmanager.dataspace.copernicus.eu/\n"
            "and enter them in section 6c, or switch 'Download source' to ASF/CDSE.")

    all_items = _cdse_search(cfg, log)
    if not all_items:
        return []

    _stop_ev  = cfg.get("stop_event")
    _force_ev = cfg.get("force_event")
    N_OBJ     = max(1, min(int(cfg.get("s3_workers", 8)), 16))      # parallel files per scene
    n_scenes  = max(1, min(int(cfg.get("max_dl_workers", 1)), 5))   # scenes in parallel (section 7)
    n_dl      = len(all_items)
    failed    = []
    _done     = [0]
    _lock     = threading.Lock()

    s3 = boto3.client(
        "s3", endpoint_url="https://eodata.dataspace.copernicus.eu",
        aws_access_key_id=ak, aws_secret_access_key=sk, region_name="default",
        config=_BotoCfg(signature_version="s3v4", s3={"addressing_style": "path"},
                        max_pool_connections=min(n_scenes * N_OBJ + 4, 64),
                        retries={"max_attempts": 4, "mode": "standard"}))

    _chk_zip, chk_safe = _load_integrity_checker()
    log(f"  Download mode: CDSE S3 ({n_scenes} scene(s) × {N_OBJ} files at once)"
        f"  —  no unzip, ~20 MB/s cap per key")

    def _list_objects(prefix):
        objs, tok = [], None
        while True:
            kw = {"Bucket": "eodata", "Prefix": prefix}
            if tok:
                kw["ContinuationToken"] = tok
            r = s3.list_objects_v2(**kw)
            objs += r.get("Contents", [])
            if not r.get("IsTruncated"):
                break
            tok = r.get("NextContinuationToken")
        return objs

    def _dl_scene(args):
        i, scene = args
        if _stop_ev and _stop_ev.is_set():
            return   # graceful stop: don't start new scenes

        name = scene.get("Name", "")
        base = name[:-5] if name.endswith(".SAFE") else name
        date = scene.get("ContentDate", {}).get("Start", "")[:10]
        dirn = "?"
        for a in scene.get("Attributes", []):
            if a.get("Name") == "orbitDirection":
                dirn = "ASC" if a.get("Value", "").upper().startswith("ASC") else "DSC"
                break

        safe_path = os.path.join(safe_dir, base + ".SAFE")
        zip_path  = os.path.join(safe_dir, base + ".zip")
        if os.path.isdir(safe_path) or os.path.isfile(zip_path):
            with _lock: _done[0] += 1; _c = _done[0]
            log(f"  [{i}/{n_dl}] SKIP {date} {dirn}  (exists)")
            progress_cb("download", _c, n_dl, f"{date} {dirn}  skip")
            return

        s3path = scene.get("S3Path", "")
        if not s3path:
            _write_error(cfg, name, "download_s3", "product has no S3Path")
            with _lock: failed.append(scene); _done[0] += 1; _c = _done[0]
            log(f"  [{i}/{n_dl}] ✗ {date} {dirn}  (no S3Path)")
            progress_cb("download", _c, n_dl, f"{date} {dirn}  ✗")
            return
        prefix = s3path.lstrip("/").split("/", 1)[1]   # key prefix inside 'eodata' bucket

        log(f"  [{i}/{n_dl}] ↓ {date} {dirn}  {base[:38]}…")
        t0 = time.time()
        try:
            objs = [o for o in _list_objects(prefix)
                    if os.path.relpath(o["Key"], prefix) not in (".", "")]
            if not objs:
                raise RuntimeError("no objects under S3 prefix")
            total_sz = sum(o["Size"] for o in objs) or 1
            part_dir = safe_path + ".partdir"

            got  = [0]; _blk = threading.Lock(); _last = [time.time()]
            def _fetch(o):
                if _force_ev and _force_ev.is_set():
                    return
                rel = os.path.relpath(o["Key"], prefix).replace("/", os.sep)
                dst = os.path.join(part_dir, rel)
                # resume: skip a file already fully downloaded (same size)
                if os.path.isfile(dst) and os.path.getsize(dst) == o["Size"]:
                    with _blk: got[0] += o["Size"]
                    return
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                s3.download_file("eodata", o["Key"], dst)
                with _blk:
                    got[0] += o["Size"]
                    _now = time.time()
                    if _now - _last[0] >= 1.0:
                        _el = _now - t0
                        _mbps = got[0] * 8 / _el / 1e6 if _el > 0 else 0
                        with _lock: _c = _done[0]
                        progress_cb("download", _c, n_dl,
                                    f"↓ {date} {got[0]/total_sz*100:.0f}% "
                                    f"{got[0]/1_048_576:.0f}MB {_mbps:.0f}Mbps",
                                    speed=f"{_mbps:.0f} Mbps")
                        _last[0] = _now

            with ThreadPoolExecutor(max_workers=N_OBJ) as fpool:
                list(fpool.map(_fetch, objs))

            if _force_ev and _force_ev.is_set():
                log(f"     [Force-stopped mid-scene {base[:28]} — partial .SAFE kept for resume]")
                return

            # partdir now holds the .SAFE contents → move it into place atomically
            if os.path.isdir(safe_path):
                shutil.rmtree(safe_path, ignore_errors=True)
            os.replace(part_dir, safe_path)

            if chk_safe is not None:
                try:
                    _st, _why = chk_safe(safe_path, deep=True)
                except Exception:
                    _st, _why = "OK", []
                if _st == "CORRUPT":
                    log(f"     ✗ {base}.SAFE failed integrity check — {'; '.join(_why)}")
                    _write_error(cfg, name, "download_s3",
                                 "Integrity check failed:\n" + "\n".join(_why))
                    shutil.rmtree(safe_path, ignore_errors=True)
                    with _lock: failed.append(scene); _done[0] += 1; _c = _done[0]
                    progress_cb("download", _c, n_dl, f"{date} {dirn}  ✗")
                    return

            elapsed = time.time() - t0
            mb  = total_sz / 1_048_576
            spd = f"{mb*8/elapsed:.0f} Mbps" if elapsed > 0 else ""
            with _lock: _done[0] += 1; _c = _done[0]
            log(f"     ✓  {base[:28]}  {elapsed:.0f}s  {mb:.0f} MB  {spd}")
            _clear_error(cfg, name, "download_s3")
            progress_cb("download", _c, n_dl, f"{date} {dirn} ✓ {mb:.0f}MB {spd}", speed=spd)

        except Exception as e:
            _write_error(cfg, name, "download_s3", f"{e}")
            log(f"     ✗ ERROR {base[:28]}: {e}")
            with _lock: failed.append(scene); _done[0] += 1; _c = _done[0]
            progress_cb("download", _c, n_dl, f"{date} {dirn}  ✗")

    # Scenes in parallel (section 7 spinner); each scene's files in parallel (N_OBJ).
    with ThreadPoolExecutor(max_workers=n_scenes) as pool:
        futs = [pool.submit(_dl_scene, (i, s)) for i, s in enumerate(all_items, 1)]
        for f in _as_completed2(futs):
            try:
                f.result()
            except Exception as e:
                log(f"  [UNEXPECTED S3 error] {e}")

    # S3 scenes arrive already extracted, but a previous ASF/CDSE run may have left
    # loose .zip archives (which we skipped above). Extract those so SNAP finds them
    # too — _unzip_zips only touches .zip files, so S3 .SAFE folders are ignored.
    _unzip_zips(safe_dir, log, progress_cb, _stop_ev, cfg)
    return failed


def _has_s3_creds(cfg):
    return bool(cfg.get("s3_access", "").strip() and cfg.get("s3_secret", "").strip())


def _has_asf_creds(cfg):
    return bool(cfg.get("asf_token", "").strip())


def _has_cdse_creds(cfg):
    return bool(cfg.get("cdse_user", "").strip() and cfg.get("cdse_pass", ""))


def _download_dispatch(cfg, safe_dir, log, progress_cb=None):
    """Route step 1 to ASF and/or CDSE. 'auto' tries whichever source(s) have
    credentials configured — CDSE first (no parallel-download throttling) —
    and falls back to the other only if the first raises before any per-scene
    result is collected (bad creds, unreachable server, missing deps).
    Per-scene failures are returned normally by each downloader and never
    trigger a fallback."""
    src = cfg.get("dl_source", "asf")
    if src == "asf":
        log("\n── STEP 1: Downloading .SAFE from ASF ──")
        return _download(cfg, safe_dir, log, progress_cb) or []
    if src == "cdse":
        log("\n── STEP 1: Downloading .SAFE from Copernicus CDSE ──")
        return _download_cdse(cfg, safe_dir, log, progress_cb) or []
    if src == "cdse_s3":
        log("\n── STEP 1: Downloading .SAFE from CDSE S3 ──")
        return _download_cdse_s3(cfg, safe_dir, log, progress_cb) or []

    # auto: fastest source first (S3 → CDSE OData → ASF), fall back on hard failure
    candidates = []
    if _has_s3_creds(cfg):
        candidates.append(("CDSE S3", _download_cdse_s3))
    if _has_cdse_creds(cfg):
        candidates.append(("Copernicus CDSE", _download_cdse))
    if _has_asf_creds(cfg):
        candidates.append(("ASF", _download))
    if not candidates:
        raise RuntimeError(
            "No download credentials configured.\n"
            "Enter an ASF Earthdata token (section 6), a CDSE"
            " username/password (section 6b), or CDSE S3 keys (section 6c).")

    last_err = None
    for i, (label, fn) in enumerate(candidates):
        log(f"\n── STEP 1: Downloading .SAFE from {label} ──")
        try:
            return fn(cfg, safe_dir, log, progress_cb) or []
        except Exception as e:
            last_err = e
            nxt = candidates[i+1:]
            if nxt:
                log(f"  ⚠ {label} failed ({e}) — falling back to {nxt[0][0]}...")
            else:
                raise
    raise last_err


import ast as _ast

# Nodes a band expression is allowed to contain. Passing {"__builtins__": {}} to
# eval is NOT a security sandbox (it's bypassable); real safety comes from
# validating the expression's AST against this allowlist first — which also turns
# a typo into a clear error instead of a silent no-output (S1).
_EXPR_NODES = (
    _ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.BoolOp, _ast.Compare,
    _ast.IfExp, _ast.Call, _ast.Attribute, _ast.Name, _ast.Load,
    _ast.Constant, _ast.Tuple, _ast.List, _ast.Subscript, _ast.Slice,
    _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.FloorDiv, _ast.Mod, _ast.Pow,
    _ast.USub, _ast.UAdd, _ast.And, _ast.Or, _ast.Not,
    _ast.Eq, _ast.NotEq, _ast.Lt, _ast.LtE, _ast.Gt, _ast.GtE,
    _ast.BitAnd, _ast.BitOr, _ast.BitXor,
)


def _validate_expr(expr, allowed_names):
    """Raise ValueError unless `expr` is a safe numpy band expression using only
    numbers, the given band names, `np`, and `np.<func>(...)` calls."""
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e}")
    allowed = set(allowed_names) | {"np"}
    for node in _ast.walk(tree):
        if not isinstance(node, _EXPR_NODES):
            raise ValueError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, _ast.Name) and node.id not in allowed:
            raise ValueError(f"unknown name '{node.id}' — allowed: {sorted(allowed)}")
        if isinstance(node, _ast.Attribute):
            if node.attr.startswith("_"):
                raise ValueError("dunder/private attribute access is not allowed")
            if not (isinstance(node.value, _ast.Name) and node.value.id == "np"):
                raise ValueError("only np.<function> attribute access is allowed")
    return tree


def _raster_calc_worker(input_folder, expr_str, band_name_out, output_folder,
                        log, progress_cb=None, stop_ev=None):
    """
    Apply a numpy expression to every scene group in input_folder.
    Band variables (VV, VH, CR, RVI, DIFF, …) are loaded by name.
    Results are written as float32 GeoTIFFs to output_folder.
    """
    try:
        import numpy as np
        import rasterio
    except ImportError as e:
        log(f"ERROR: Missing package: {e}.  Run: pip install numpy rasterio")
        return

    if progress_cb is None:
        progress_cb = lambda *a: None

    BAND_RE = re.compile(
        r'_(VV|VH|CR|RVI|DIFF(?:VV|VH|polar)?|DIFFpolar|dVV|dVH)'
        r'(?:_(lin|dB|linear|db))?\.tif$', re.IGNORECASE)

    # Collect all tifs
    tifs = sorted(
        glob.glob(os.path.join(input_folder, "**", "*.tif"), recursive=True) +
        glob.glob(os.path.join(input_folder, "*.tif")))

    if not tifs:
        log("  No .tif files found in input folder"); return

    # Group by scene prefix (strip band + scale suffix)
    groups: dict = {}
    for fpath in tifs:
        m = BAND_RE.search(os.path.basename(fpath))
        if m:
            band   = m.group(1).upper()
            prefix = fpath[:len(fpath) - len(m.group(0))]
            groups.setdefault(prefix, {})[band] = fpath

    if not groups:
        log("  No band-organised files found.")
        log("  Expected names like: *_VV_lin.tif, *_VH_lin.tif, …")
        return

    log(f"  Found {len(groups)} scene groups")
    log(f"  Expression : {expr_str}")
    log(f"  Output band: {band_name_out}")
    os.makedirs(output_folder, exist_ok=True)

    total, done = len(groups), 0
    _nodata = -9999.0

    # Validate against a name/node allowlist, then compile once. Both surface a
    # clear error instead of silently producing nothing (S1).
    _all_bands = set().union(*(g.keys() for g in groups.values())) if groups else set()
    try:
        _validate_expr(expr_str, _all_bands)
        _code = compile(expr_str, "<expr>", "eval")
    except (ValueError, SyntaxError) as se:
        log(f"  ✗ Invalid expression: {se}"); return

    for prefix, band_files in sorted(groups.items()):
        if stop_ev and stop_ev.is_set():
            log("  [Stopped by user]"); break
        scene_name = os.path.basename(prefix)
        try:
            ns      = {"np": np}
            profile = None
            for band, fpath in band_files.items():
                with rasterio.open(fpath) as src:
                    arr = src.read(1).astype("float64")
                    nd  = src.nodata
                    if nd is not None:
                        arr[arr == nd] = np.nan
                    ns[band] = arr
                    if profile is None:
                        profile = src.profile.copy()

            if profile is None:
                done += 1; continue

            with np.errstate(divide="ignore", invalid="ignore"):
                result = eval(_code, {"__builtins__": {}}, ns)  # type: ignore[arg-type]
            result = np.asarray(result, dtype="float32")
            result[~np.isfinite(result)] = _nodata

            # mirror subfolder structure
            subdir  = os.path.dirname(prefix)
            rel     = os.path.relpath(subdir, input_folder)
            out_sub = os.path.join(output_folder, rel) if rel != "." else output_folder
            os.makedirs(out_sub, exist_ok=True)
            out_name = f"{scene_name}_{band_name_out}.tif"
            out_path = os.path.join(out_sub, out_name)

            out_prof = {**profile, "count": 1, "dtype": "float32",
                        "nodata": _nodata, "compress": "lzw",
                        "tiled": True, "blockxsize": 512, "blockysize": 512}
            with rasterio.open(out_path, "w", **out_prof) as dst:
                dst.write(result, 1)

            done += 1
            log(f"  [{done}/{total}] ✓  {out_name}")
            progress_cb("process", done, total, f"{scene_name}  ✓")

        except NameError as ne:
            missing = str(ne).split("'")[1] if "'" in str(ne) else str(ne)
            log(f"  [{done+1}/{total}] ✗  '{missing}' not available — "
                f"have: {sorted(band_files)}")
            done += 1
        except Exception as e:
            log(f"  [{done+1}/{total}] ✗  {scene_name}: {e}")
            done += 1

    log(f"\n  Raster calc done — {done}/{total} scenes  →  {output_folder}")


def _load_integrity_checker():
    """Import check_zip / check_safe from check_safe.py in the script folder.
    Returns (check_zip, check_safe) or (None, None) if it can't be loaded —
    in which case the pipeline falls back to its plain unzip behaviour."""
    try:
        import importlib.util
        cs_path = os.path.join(_SCRIPT_DIR, "check_safe.py")
        if not os.path.isfile(cs_path):
            return None, None
        spec = importlib.util.spec_from_file_location("check_safe", cs_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "check_zip", None), getattr(mod, "check_safe", None)
    except Exception:
        return None, None


_FAST_EXTRACTOR = None   # cached (kind, exe) — probed once per process


def _find_fast_extractor():
    """Locate a native archive extractor. Python's zipfile is markedly slower
    than 7-Zip / bsdtar on large Sentinel products (many big DEFLATE members),
    so prefer a native tool when present. Returns (kind, exe):
      ("7z", path) | ("tar", path) | ("zipfile", None)."""
    global _FAST_EXTRACTOR
    if _FAST_EXTRACTOR is not None:
        return _FAST_EXTRACTOR
    for name in ("7z", "7za", "7zr"):
        p = shutil.which(name)
        if p:
            _FAST_EXTRACTOR = ("7z", p); return _FAST_EXTRACTOR
    for p in (r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if os.path.isfile(p):
            _FAST_EXTRACTOR = ("7z", p); return _FAST_EXTRACTOR
    # bsdtar (libarchive) reads .zip; GNU tar does NOT. shutil.which may resolve
    # GNU tar first (e.g. Git's on Windows), so probe candidates and keep the
    # first that actually reports bsdtar/libarchive — a platform check alone
    # would wrongly accept Git's GNU tar and then silently fall back to zipfile.
    _tar_cands = [shutil.which("tar")]
    if os.name == "nt":
        _tar_cands.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                       "System32", "tar.exe"))
    for tp in _tar_cands:
        if not tp or not os.path.isfile(tp):
            continue
        try:
            v = subprocess.run([tp, "--version"], capture_output=True, text=True, timeout=5)
            if any(k in (v.stdout + v.stderr).lower() for k in ("bsdtar", "libarchive")):
                _FAST_EXTRACTOR = ("tar", tp); return _FAST_EXTRACTOR
        except Exception:
            pass
    _FAST_EXTRACTOR = ("zipfile", None)
    return _FAST_EXTRACTOR


class _ExtractStopped(Exception):
    """Raised when extraction was aborted because the user hit Stop, so callers
    can clean up the half-extracted product instead of treating it as an error."""


def _run_extractor(cmd, stop_ev):
    """Run an extractor subprocess, but poll `stop_ev` while it works so Stop
    actually kills the in-flight 7z/tar (subprocess.run would block until it
    finished on its own — that's why closing the app left extractions running).
    Returns (rc, out, err); rc is None when killed by Stop."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True)
    while True:
        try:
            out, err = p.communicate(timeout=0.5)
            return p.returncode, out, err
        except subprocess.TimeoutExpired:
            if stop_ev and stop_ev.is_set():
                p.kill()
                try: out, err = p.communicate(timeout=5)
                except Exception: out, err = "", ""
                return None, out, err


def _extract_archive(zp, dest, log, stop_ev=None):
    """Extract zip `zp` into directory `dest`, using the fastest available tool
    and falling back to Python zipfile on any failure. Raises on total failure,
    or _ExtractStopped if the user hit Stop mid-extraction."""
    import zipfile
    kind, exe = _find_fast_extractor()

    def _emsg(out, err):
        # 7-Zip prints errors on stdout, not stderr; include both.
        return ((out or "") + " " + (err or "")).strip().replace("\n", " ")[:200]

    try:
        if kind == "7z":
            # -bd: no progress line; put the archive right after `x` so 7-Zip
            # never mistakes a later path for a switch (that caused rc=2).
            rc, out, err = _run_extractor([exe, "x", zp, "-y", "-bd", "-o" + dest],
                                          stop_ev)
            if rc is None: raise _ExtractStopped()
            if rc == 0:
                return
            log(f"    (7z rc={rc}: {_emsg(out, err)} — falling back to zipfile)")
        elif kind == "tar":
            rc, out, err = _run_extractor([exe, "-xf", zp, "-C", dest], stop_ev)
            if rc is None: raise _ExtractStopped()
            if rc == 0:
                return
            log(f"    (tar rc={rc}: {_emsg(out, err)} — falling back to zipfile)")
    except _ExtractStopped:
        raise
    except Exception as e:
        log(f"    (fast extractor error: {e} — falling back to zipfile)")
    if stop_ev and stop_ev.is_set():
        raise _ExtractStopped()
    with zipfile.ZipFile(zp, 'r') as z:
        z.extractall(_long_path(dest))


def _same_volume(a, b):
    return (os.path.splitdrive(os.path.abspath(a))[0].lower()
            == os.path.splitdrive(os.path.abspath(b))[0].lower())


def _unzip_stage_root(cfg, safe_dir):
    """Where to extract archives before moving them into `safe_dir`. Exploding the
    thousands of tiny files in a .SAFE straight onto a slow/external drive is
    latency-bound (small-file IOPS + the zip read fighting the writes on one USB
    bus). Extracting on a fast local scratch (internal NVMe) and then bulk-moving
    the finished product is markedly faster. cfg["unzip_stage_dir"]: "auto" (system
    temp), "off"/"" (extract in place), or an explicit dir. Returns a scratch dir
    on a *different* volume than safe_dir, or safe_dir itself when off / same volume
    (staging to the same drive would just be a pointless extra copy)."""
    val = str(cfg.get("unzip_stage_dir", "auto")).strip()
    if not val or val.lower() == "off":
        return safe_dir
    root = tempfile.gettempdir() if val.lower() == "auto" else val
    try:
        if _same_volume(root, safe_dir):
            return safe_dir
        stage = os.path.join(root, "sentinel_unzip_stage")
        os.makedirs(stage, exist_ok=True)
        return stage
    except Exception:
        return safe_dir


# Only one cross-volume move runs at a time. With >1 unzip worker, extractions
# proceed in parallel on the fast scratch volume, but two robocopies to the same
# (often slow/external) output drive just thrash one head and throttle each other
# — serialising the moves lets each product write sequentially at the drive's
# real speed while the next scene extracts ahead.
# ponytail: single global lock; if two output drives are ever in play, key the
# lock by destination volume instead.
_MOVE_LOCK = threading.Lock()


def _move_tree_cross_volume(src, dst_final, dst_dir, stem, log):
    """Move extracted product dir `src` (on a fast scratch volume) to `dst_final`
    on another volume. On Windows, thousands of small .SAFE files copy far faster
    with multithreaded robocopy than a single-threaded shutil.move; copy into a
    staging dir on the destination volume, then an atomic same-volume rename so
    SNAP never sees a half-copied .SAFE. Falls back to shutil.move otherwise.
    Serialised via _MOVE_LOCK so concurrent unzip workers don't thrash one drive."""
    with _MOVE_LOCK:
        if os.name == "nt":
            mv = os.path.join(dst_dir, f".__mv_{stem}")
            try:
                if os.path.isdir(mv):
                    _rmtree(mv)
                r = subprocess.run(
                    ["robocopy", src, mv, "/E", "/MT:4", "/R:2", "/W:2",
                     "/NFL", "/NDL", "/NP", "/NJH", "/NJS"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if r.returncode < 8:                 # robocopy: rc 0-7 = success
                    os.replace(mv, dst_final)        # same volume → instant rename
                    return
                log(f"    (robocopy rc={r.returncode} — falling back to shutil.move)")
            except Exception as e:
                log(f"    (robocopy error: {e} — falling back to shutil.move)")
            _rmtree(mv)
        shutil.move(src, dst_final)


def _long_path(p):
    # ponytail: Windows MAX_PATH(260) escape. Deep Sentinel names (temp .__ext_ +
    # nested .SAFE\...SAFE-report.pdf) blow past 260 and zipfile's open() then
    # raises FileNotFoundError. The \\?\ prefix lifts the limit; noop off Windows.
    if os.name != "nt":
        return p
    ap = os.path.abspath(p)
    return ap if ap.startswith("\\\\?\\") else "\\\\?\\" + ap


def _rmtree(p):
    # rmtree that survives Windows MAX_PATH on deep Sentinel .SAFE trees — a plain
    # shutil.rmtree(ignore_errors=True) silently fails on >260-char paths, which
    # would leave multi-GB half-extracted products on the scratch/output drive.
    shutil.rmtree(_long_path(p), ignore_errors=True)


def _is_drive_gone(e):
    # A surprise-removed / disconnected drive (external USB, USB selective-suspend,
    # loose cable) raises WinError 433 "device does not exist" on first touch, then
    # EINVAL(22) on every later open() of a path on that dead drive. Treat both as
    # "the output drive vanished" so callers can abort once instead of failing N times.
    if os.name != "nt" or not isinstance(e, OSError):
        return False
    return getattr(e, "winerror", None) == 433 or e.errno == 22


def _wait_for_drive(path, log, stop_ev=None, poll=120):
    """Block until `path` is writable again (its drive reconnected), or the user
    asks to stop. Returns True once available, False if stopped while waiting.
    Lets an unattended run survive an external drive that drops out and returns."""
    def _ok():
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".drive_check")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return True
        except OSError:
            return False
    if _ok():
        return True
    drive = os.path.splitdrive(os.path.abspath(path))[0] or path
    log(f"  ⏸ Output drive {drive} disconnected — waiting for it to reconnect "
        f"(re-checking every {poll // 60} min; press Stop to abort)…")
    waited = 0
    while not (stop_ev and stop_ev.is_set()):
        time.sleep(poll)
        waited += poll
        if _ok():
            log(f"  ▶ Drive {drive} back after {waited // 60} min — resuming.")
            return True
        log(f"  … still waiting for {drive} ({waited // 60} min)")
    log(f"  [Stopped while waiting for {drive}]")
    return False


def _locate_safe_root(root):
    """Find the Sentinel product directory inside `root` regardless of naming:
    the folder that directly contains manifest.safe (or a *.SAFE subdir).
    Returns the product dir path, or None if not found."""
    if os.path.isfile(os.path.join(root, "manifest.safe")):
        return root
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    # prefer an explicit *.SAFE child
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p) and e.upper().endswith(".SAFE"):
            return p
    # otherwise any child holding a manifest.safe
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "manifest.safe")):
            return p
    return None


def _normalize_safe_dirs(safe_dir, log=lambda *a: None):
    """Rename any product folder that holds a manifest.safe but isn't named
    *.SAFE (e.g. extracted manually with 7-Zip, which drops the suffix) so SNAP
    discovery — which globs *.SAFE — can find it. Safe to call repeatedly."""
    try:
        entries = os.listdir(safe_dir)
    except OSError:
        return
    for e in entries:
        p = os.path.join(safe_dir, e)
        if (os.path.isdir(p) and not e.upper().endswith(".SAFE")
                and not e.startswith(".__ext_")
                and os.path.isfile(os.path.join(p, "manifest.safe"))):
            target = os.path.join(safe_dir, e + ".SAFE")
            if os.path.exists(target):
                continue
            try:
                os.replace(p, target)
                log(f"  Renamed {e} → {e}.SAFE (added missing suffix)")
            except OSError:
                pass


def _prune_broken_safes(safe_dir, log=lambda *a: None):
    """Delete .SAFE dirs with no manifest.safe (empty/incomplete products from an
    interrupted download or extraction) and return the removed folder names.
    Leaving one in place both fails SNAP with an opaque rc=1 AND blocks recovery —
    the download loop and _unzip_zips both skip when a .SAFE of that name exists."""
    removed = []
    try:
        entries = os.listdir(safe_dir)
    except OSError:
        return removed
    for e in entries:
        p = os.path.join(safe_dir, e)
        if (os.path.isdir(p) and e.upper().endswith(".SAFE")
                and not os.path.isfile(os.path.join(p, "manifest.safe"))):
            try:
                shutil.rmtree(p)
                removed.append(e)
                log(f"  Removed broken .SAFE (no manifest): {e}")
            except Exception as _re:
                log(f"  ⚠ could not remove broken .SAFE {e}: {_re}")
    return removed


def _redownload_missing(cfg, safe_dir, removed_names, log, progress_cb):
    """Re-fetch products that were found broken and removed — used in existing-
    .SAFE-folder mode so one corrupt download doesn't silently drop a scene.
    Reuses the normal download path (retry_dates filter); needs credentials and
    an AOI. Warns and returns if it can't."""
    dates = sorted({f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
                    for n in removed_names
                    if (m := re.search(r'_(\d{8})T\d{6}_', n))})
    if not dates:
        return
    has_asf, has_cdse = _has_asf_creds(cfg), _has_cdse_creds(cfg)
    if not (has_asf or has_cdse):
        log("  ⚠ Cannot re-download the broken .SAFE(s): no download credentials "
            "entered (ASF token in section 6, or CDSE login in section 6b). "
            "Those scene(s) will be missing from the output.")
        return
    if not (cfg.get("aoi_path") and os.path.isfile(cfg.get("aoi_path", ""))):
        log("  ⚠ Cannot re-download the broken .SAFE(s): no AOI file set.")
        return
    log(f"  ⤓ RE-DOWNLOADING {len(dates)} broken .SAFE date(s): "
        f"{', '.join(dates)}")
    # Kick the Download bar so it's obvious a re-download is underway before the
    # normal per-scene download progress takes over.
    progress_cb("download", 0, len(dates),
                f"re-downloading {len(dates)} broken scene(s)…")
    tmp = dict(cfg)
    tmp["retry_dates"] = set(dates)
    tmp["dl_source"]   = "asf" if has_asf else "cdse"
    tmp["start_date"]  = min(cfg.get("start_date") or dates[0], dates[0])
    tmp["end_date"]    = max(cfg.get("end_date") or dates[-1], dates[-1])
    try:
        _download_dispatch(tmp, safe_dir, log, progress_cb)
        _unzip_zips(safe_dir, log, progress_cb, cfg.get("stop_event"), cfg)
        _normalize_safe_dirs((cfg.get("safe_out_dir") or "").strip() or safe_dir, log)
    except Exception as _de:
        log(f"  ⚠ Re-download failed: {_de} — scene(s) will be missing.")


def _resolve_batch_gb(cfg, safe_out):
    """Parse cfg['safe_scratch_gb'] → GB budget for .SAFE held on disk at once.
    '' / 0 / 'off' → 0.0 (disabled: extract everything at once, old behaviour);
    'auto' → 80% of the .SAFE drive's current free space; a number → that many GB."""
    v = str(cfg.get("safe_scratch_gb", "")).strip().lower()
    if not v or v in ("0", "off", "none"):
        return 0.0
    if v == "auto":
        try:
            return max(5.0, (shutil.disk_usage(safe_out).free * 0.8) / (1024**3))
        except Exception:
            return 0.0
    try:
        return max(0.0, float(v))
    except ValueError:
        return 0.0


def _fmt_dur(sec):
    """Human duration: '42.0 min' under an hour, '2h 15m' at or above."""
    m = sec / 60.0
    return f"{m:.1f} min" if m < 60 else f"{int(m // 60)}h {int(round(m % 60))}m"


def _process_in_batches(cfg, safe_dir, safe_out, snap_dir, budget_gb, log, progress_cb, stop_ev):
    """Extract → SNAP → delete .SAFE in chunks so no more than ~budget_gb of .SAFE
    exist at once — keeps a small/slow output drive from filling. .SAFE ≈ 1.7× its
    zip (measured on GRDH). Each chunk's .SAFE is deleted after SNAP, before the next."""
    _EST = 1.7
    zips = sorted(glob.glob(os.path.join(safe_dir, "*.zip")))
    if not zips:
        log("  No .zip to batch — running SNAP over any existing .SAFE.")
        if cfg.get("do_snap"):
            _snap_process(cfg, safe_out, snap_dir, log, progress_cb)
        return
    budget = budget_gb * (1024**3)
    chunks, cur, cursz = [], [], 0.0
    for zp in zips:
        try:
            s = os.path.getsize(zp) * _EST
        except OSError:
            s = 0
        if cur and cursz + s > budget:
            chunks.append(cur); cur, cursz = [], 0.0
        cur.append(zp); cursz += s
    if cur:
        chunks.append(cur)
    log(f"  Batch mode: {len(zips)} scene(s) → {len(chunks)} chunk(s) of "
        f"≤ {budget_gb:g} GB .SAFE, output {safe_out}")
    _total = len(chunks)
    _t0 = time.time()
    # Stop is checked only at batch boundaries (top + end of each iteration): a
    # batch that has started runs to completion (SNAP + finals), it just won't
    # start the next one — so Stop never leaves a half-processed batch.
    for i, chunk in enumerate(chunks, 1):
        if stop_ev and stop_ev.is_set():
            log(f"  [Stopped at batch boundary — halted before batch {i}/{_total}]"); return
        _cgb = sum(os.path.getsize(z) for z in chunk if os.path.isfile(z)) * _EST / (1024**3)
        log(f"\n── Batch {i}/{_total}: {len(chunk)} scene(s), ~{_cgb:.0f} GB .SAFE ──")
        _unzip_zips(safe_dir, log, progress_cb, stop_ev, cfg, only_zips=chunk)
        if cfg.get("do_snap"):
            _snap_process(cfg, safe_out, snap_dir, log, progress_cb)
            # Finals per batch: turn this batch's SNAP GeoTIFFs into the final
            # product now. _compute_indices scans the WHOLE snap_dir, so it also
            # sweeps up any GeoTIFFs left over from an earlier interrupted run —
            # not just this batch's. Runs before _clean_safe so the .SAFE delete
            # check sees either the D: tile or the final in out_dir.
            if cfg.get("do_indices"):
                _compute_indices(cfg, snap_dir, cfg.get("out_dir", ""), log, progress_cb)
            # free this chunk's .SAFE — only the ones actually converted, so a
            # stop/failure never drops a date.
            _clean_safe(safe_out, log, snap_dir, cfg)
        _el = time.time() - _t0
        if i < _total:
            _eta = _el / i * (_total - i)
            log(f"  ⏱ Batch {i}/{_total} done — {_fmt_dur(_el)} elapsed, "
                f"est. {_fmt_dur(_eta)} left ({_total - i} batch(es) to go)")
        if stop_ev and stop_ev.is_set():
            log(f"  [Stopped — finished batch {i}/{_total}; halting before next]"); return


def _rename_with_retry(src, dst, tries=6, delay=0.5):
    """os.replace, retrying on Windows AccessDenied. Antivirus / Search indexer
    briefly locks freshly-extracted files, so an immediate same-volume rename can
    fail with WinError 5 even though nothing is wrong — a short backoff clears it.
    ponytail: fixed retry schedule; if AV locks persist longer, raise `tries`."""
    import time as _t
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except (PermissionError, OSError):
            if i == tries - 1:
                raise
            _t.sleep(delay * (i + 1))


def _unzip_zips(safe_dir, log, progress_cb, stop_ev, cfg, only_zips=None):
    """Extract any .zip archives in safe_dir that don't yet have a .SAFE counterpart.

    Integrity is checked at two points so bad data never reaches SNAP and no
    time is wasted:
      • BEFORE unzip — a fast structural check of the .zip (valid archive,
        manifest present, VV/VH bands present and sensibly sized). This reads
        only the zip's index, NOT every byte, because extractall already
        verifies each member's CRC while extracting.
      • AFTER unzip — check_safe() on the extracted .SAFE (valid manifest, both
        bands present/sized, GeoTIFFs readable) to confirm extraction worked.
    Corrupt products are deleted and logged so SNAP skips them; re-running the
    pipeline will re-download whatever is missing.
    """
    import zipfile
    if cfg.get("_batch_defer"):
        return   # batch mode extracts in chunks itself (see _process_in_batches)
    zips = only_zips if only_zips is not None else glob.glob(os.path.join(safe_dir, "*.zip"))
    if not zips:
        return
    chk_zip, chk_safe = _load_integrity_checker()
    if chk_zip is None:
        log("  (integrity checker not found — proceeding without pre/post checks)")

    n_zip = len(zips)
    # Extraction is I/O-latency bound (single-threaded zipfile leaves the disk
    # mostly idle), so extract several archives concurrently. Each goes to its
    # own private temp dir → no collisions. Prefer a native extractor (7-Zip /
    # bsdtar) — markedly faster than Python zipfile on big Sentinel products —
    # and fall back to zipfile. Tune with cfg["unzip_workers"].
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _uw = max(1, min(int(cfg.get("unzip_workers", 3)), n_zip))
    _ek, _ex = _find_fast_extractor()
    _etool = {"7z": f"7-Zip ({_ex})", "tar": f"bsdtar ({_ex})",
              "zipfile": "Python zipfile (slow — install 7-Zip to speed this up)"}[_ek]
    # .SAFE output may live on a different (faster/healthier) drive than the zips.
    # Zips are still read from — and deleted in — safe_dir; blank → old behaviour.
    _safe_out = (cfg.get("safe_out_dir") or "").strip() or safe_dir
    if _safe_out != safe_dir and cfg.get("dl_source") == "cdse_s3":
        _safe_out = safe_dir
    os.makedirs(_safe_out, exist_ok=True)
    _stage_root = _unzip_stage_root(cfg, _safe_out)
    _staging = not _same_volume(_stage_root, _safe_out)
    _stagemsg = (f", scratch: {_stage_root} → move to output" if _staging
                 else "")
    log(f"  Unzipping {n_zip} archive(s)  (parallel: {_uw} worker(s), "
        f"extractor: {_etool}{_stagemsg})...")
    _ulock = threading.Lock()
    _udone = [0]

    def _unzip_one(zp):
        if stop_ev and stop_ev.is_set():
            return
        stem = Path(zp).stem
        safe_path = os.path.join(_safe_out, stem + ".SAFE")

        def _tick(msg):
            with _ulock:
                _udone[0] += 1
                _d = _udone[0]
            progress_cb("unzip", _d, n_zip, msg)

        if os.path.isdir(safe_path):
            try: os.remove(zp)
            except Exception: pass
            _tick(f"{stem[:28]}  skip"); return

        # ── pre-unzip check (fast — reads only the zip's central directory) ──
        if chk_zip is not None:
            try:
                _st, _why = chk_zip(zp, do_crc=False)
            except Exception:
                _st, _why = "OK", []
            if _st == "CORRUPT":
                log(f"    ✗ {stem}: corrupt download — {'; '.join(_why)}")
                _write_error(cfg, stem, "download_check",
                             "Pre-unzip integrity check failed:\n" + "\n".join(_why))
                try: os.remove(zp); log(f"    Deleted corrupt zip: {Path(zp).name}")
                except Exception: pass
                _tick(f"{stem[:28]}  ✗ corrupt"); return
            elif _st == "SUSPECT":
                log(f"    ⚠ {stem}: {'; '.join(_why)} — extracting anyway, inspect later")

        # Extract into a private temp dir with the fastest tool available, then
        # normalise the product folder to <stem>.SAFE (some tools drop the
        # suffix). The rename is a same-volume atomic move, so SNAP never sees a
        # half-extracted .SAFE and naming is guaranteed regardless of the tool.
        tmp = os.path.join(_stage_root, f".__ext_{stem}")
        _extracted_ok = False   # True once bytes are safely extracted; a failure
                                # after this is placement (lock), NOT corruption
        try:
            if os.path.isdir(tmp): _rmtree(tmp)
            os.makedirs(tmp, exist_ok=True)
            _extract_archive(zp, tmp, log, stop_ev)
            prod = _locate_safe_root(tmp)
            if prod is None:
                raise RuntimeError("no manifest.safe found in extracted archive")
            _extracted_ok = True
            if os.path.exists(safe_path):
                _rmtree(safe_path)
            if _same_volume(prod, safe_path):
                _rename_with_retry(prod, safe_path)   # AV/indexer briefly locks fresh files
            else:                                # scratch → output: bulk copy+rename
                _move_tree_cross_volume(prod, safe_path, _safe_out, stem, log)
            log(f"    Unzipped: {stem}.SAFE")
        except _ExtractStopped:
            # user hit Stop — the half-extracted product is junk; the finally
            # below deletes it. Keep the zip (it's fine) so the next run resumes.
            log(f"    ⏹ stopped mid-unzip: {stem} — deleted partial extraction")
            _tick(f"{stem[:28]}  ⏹ stopped"); return
        except Exception as e:
            import traceback as _tb
            log(f"    ERROR: could not unzip {Path(zp).name}: {e}")
            _write_error(cfg, stem, "unzip", _tb.format_exc())
            if _extracted_ok:
                # Extraction succeeded; only the move/rename failed (transient
                # lock, dest busy). The zip is fine — KEEP it so the next run
                # retries instead of silently dropping this date.
                log(f"    Kept zip for retry (extraction OK, placement failed): {Path(zp).name}")
                _tick(f"{stem[:28]}  ✗ move-failed (zip kept)"); return
            try: os.remove(zp); log(f"    Deleted corrupted zip: {Path(zp).name}")
            except Exception: pass
            _tick(f"{stem[:28]}  ✗ error"); return
        finally:
            # always delete the half-extracted temp and any partial cross-volume
            # move staging, so Stop never leaves a half-unzipped product behind.
            _rmtree(tmp)
            _mv = os.path.join(_safe_out, f".__mv_{stem}")
            if os.path.isdir(_mv):
                _rmtree(_mv)

        # ── post-unzip check of the extracted .SAFE ──
        if chk_safe is not None and os.path.isdir(safe_path):
            try:
                _st2, _why2 = chk_safe(safe_path, deep=True)
            except Exception:
                _st2, _why2 = "OK", []
            if _st2 == "CORRUPT":
                log(f"    ✗ {stem}.SAFE failed post-unzip check — {'; '.join(_why2)}")
                _write_error(cfg, stem, "unzip_check",
                             "Post-unzip integrity check failed:\n" + "\n".join(_why2))
                try: shutil.rmtree(safe_path)
                except Exception: pass
                try: os.remove(zp)
                except Exception: pass
                _tick(f"{stem[:28]}  ✗ corrupt"); return
            elif _st2 == "SUSPECT":
                log(f"    ⚠ {stem}.SAFE: {'; '.join(_why2)} — kept, inspect later")

        try:
            os.remove(zp); log(f"    Deleted:  {Path(zp).name}")
        except Exception:
            pass
        _tick(f"{stem[:28]}  ✓")

    # Seed the progress history at current=0 so the ETA has a time base as soon
    # as the first archive finishes.
    progress_cb("unzip", 0, n_zip, "starting…")
    with ThreadPoolExecutor(max_workers=_uw) as pool:
        futs = {pool.submit(_unzip_one, zp): zp for zp in zips}
        for f in as_completed(futs):
            if stop_ev and stop_ev.is_set():
                for x in futs: x.cancel()
                log("  [Stopped by user — unzip cancelled]")
                break
            try: f.result()
            except Exception as e:
                log(f"  [UNEXPECTED unzip error] {e}")


# ── step 2: SNAP processing ───────────────────────────────────────────────────

def _parse_safe_name(path):
    stem = Path(path).stem
    m = re.search(r'_(\d{8})T(\d{6})_', stem)
    date = m.group(1) if m else None
    hour = int(m.group(2)[:2]) if m else -1
    m2 = re.match(r'S1([ABC])', stem)
    sat = m2.group(1) if m2 else "X"
    orbit = "ASC" if hour >= 12 else "DSC"
    return date, sat, orbit



# ── Speckle presets ──────────────────────────────────────────────────────────
SPECKLE_PARAMS = {
    "lee": {
        "filter": "Lee Sigma", "filterSizeX": "3", "filterSizeY": "3",
        "dampingFactor": "2", "estimateENL": "true", "enl": "4.4",
        "numLooksStr": "4", "windowSize": "7x7",
        "targetWindowSizeStr": "3x3", "sigmaStr": "0.9", "anSize": "50",
    },
    "gamma": {
        "filter": "Gamma Map", "filterSizeX": "7", "filterSizeY": "7",
        "estimateENL": "true", "enl": "4.4",
    },
}

FILTER_SCHEMAS = {
    "None": [],
    "Boxcar":      [("filterSizeX","int","Filter size X",3,15,3),("filterSizeY","int","Filter size Y",3,15,3)],
    "Median":      [("filterSizeX","int","Filter size X",3,15,3),("filterSizeY","int","Filter size Y",3,15,3)],
    "Frost":       [("filterSizeX","int","Filter size X",3,15,3),("filterSizeY","int","Filter size Y",3,15,3),
                    ("dampingFactor","int","Damping factor",1,10,2)],
    "Lee":         [("filterSizeX","int","Filter size X",3,15,5),("filterSizeY","int","Filter size Y",3,15,5),
                    ("estimateENL","bool","Estimate ENL",True),("enl","float","ENL",1.0,16.0,4.4)],
    "Lee Sigma":   [("windowSize","choice","Analysis window",["7x7","9x9","11x11"],"7x7"),
                    ("targetWindowSizeStr","choice","Target window",["3x3","5x5"],"3x3"),
                    ("sigmaStr","choice","Sigma",["0.5","0.6","0.7","0.8","0.9"],"0.9"),
                    ("numLooksStr","choice","Num looks",["1","2","3","4"],"4"),
                    ("estimateENL","bool","Estimate ENL",True),("enl","float","ENL",1.0,16.0,4.4)],
    "Gamma Map":   [("filterSizeX","int","Filter size X",3,15,7),("filterSizeY","int","Filter size Y",3,15,7),
                    ("estimateENL","bool","Estimate ENL",True),("enl","float","ENL",1.0,16.0,4.4)],
    "Refined Lee": [("filterSizeX","int","Filter size X",7,11,7),("filterSizeY","int","Filter size Y",7,11,7)],
    "IDAN":        [("anSize","int","Adaptive neighborhood size",10,100,50),
                    ("estimateENL","bool","Estimate ENL",True),("enl","float","ENL",1.0,16.0,4.4)],
}

FILTER_DEFAULTS = {
    "None":        {"filter":"None"},
    "Boxcar":      {"filter":"Boxcar","filterSizeX":"3","filterSizeY":"3"},
    "Median":      {"filter":"Median","filterSizeX":"3","filterSizeY":"3"},
    "Frost":       {"filter":"Frost","filterSizeX":"3","filterSizeY":"3","dampingFactor":"2"},
    "Lee":         {"filter":"Lee","filterSizeX":"5","filterSizeY":"5","estimateENL":"true","enl":"4.4"},
    "Lee Sigma":   {"filter":"Lee Sigma","filterSizeX":"3","filterSizeY":"3","dampingFactor":"2",
                    "estimateENL":"true","enl":"4.4","numLooksStr":"4","windowSize":"7x7",
                    "targetWindowSizeStr":"3x3","sigmaStr":"0.9","anSize":"50"},
    "Gamma Map":   {"filter":"Gamma Map","filterSizeX":"7","filterSizeY":"7","estimateENL":"true","enl":"4.4"},
    "Refined Lee": {"filter":"Refined Lee","filterSizeX":"7","filterSizeY":"7"},
    "IDAN":        {"filter":"IDAN","anSize":"50","estimateENL":"true","enl":"4.4"},
}

# Speckle filters offered in the Batch popup (all schema filters except "None",
# which is handled as a separate checkbox). Defaults come from FILTER_DEFAULTS.
SPECKLE_FILTER_NAMES = ["Boxcar", "Median", "Frost", "Lee", "Lee Sigma",
                        "Gamma Map", "Refined Lee", "IDAN"]

# ── DEM options ───────────────────────────────────────────────────────────────
SNAP_DEMS = {
    "Copernicus 30m Global DEM": "Copernicus 30m  (recommended, GLO-30)",
    "Copernicus 90m Global DEM": "Copernicus 90m",
    "SRTM 1Sec HGT":             "SRTM 1 arc-sec (~30 m)",
    "SRTM 3Sec":                 "SRTM 3 arc-sec (~90 m)",
    "SRTM 1Sec Grid":            "SRTM 1 arc-sec Grid",
    "ASTER 1sec GDEM":           "ASTER GDEM (~30 m)",
    "ACE30":                     "ACE30 (~30 m)",
    "GETASSE30":                 "GETASSE30 (~30 m)",
}
SNAP_DEM_DEFAULT = "Copernicus 30m Global DEM"

SNAP_DEM_TAGS = {
    "Copernicus 30m Global DEM": "cop30",
    "Copernicus 90m Global DEM": "cop90",
    "SRTM 1Sec HGT":            "srtm1",
    "SRTM 3Sec":                "srtm3",
    "SRTM 1Sec Grid":           "srtm1g",
    "ASTER 1sec GDEM":          "aster",
    "ACE30":                    "ace30",
    "GETASSE30":                "getasse",
}
# short tag -> SNAP DEM name (for the compact Batch DEM pickers)
SNAP_DEM_BY_TAG = {v: k for k, v in SNAP_DEM_TAGS.items()}
DEM_TAG_LIST    = list(SNAP_DEM_TAGS.values())


def _safe_float(text, default):
    """float(text) or default — never raises on blank/garbage input (S4)."""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


def _check_internet(timeout=4):
    # Use a per-connection timeout, NOT socket.setdefaulttimeout — the latter is
    # process-global and was never restored, causing spurious download timeouts
    # later (S8).
    import socket
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        return True
    except Exception:
        return False


def _patch_speckle_filter(root, speckle, custom_params=None):
    params_map = custom_params if (speckle == "custom" and custom_params)                  else SPECKLE_PARAMS.get(speckle, SPECKLE_PARAMS["lee"])
    for node in root.findall("node"):
        op = node.find("operator")
        if op is None or op.text != "Speckle-Filter":
            continue
        params = node.find("parameters")
        if params is None:
            continue
        for child in list(params):
            params.remove(child)
        for key, val in params_map.items():
            el = ET.SubElement(params, key)
            el.text = val
        return


def _patch_dem(root, dem_name):
    for node in root.findall("node"):
        op = node.find("operator")
        if op is None or op.text not in {"Terrain-Flattening", "Terrain-Correction"}:
            continue
        params = node.find("parameters")
        if params is None:
            continue
        el = params.find("demName")
        if el is not None:
            el.text = dem_name
        else:
            e2 = ET.SubElement(params, "demName")
            e2.text = dem_name

def _patch_graph(graph_xml, safe_path, snap_out, aoi_wkt,
                 speckle="lee", speckle_params=None, dem_name=None):
    tree = ET.parse(graph_xml)
    root = tree.getroot()
    # If path ends with .SAFE (directory), use manifest.safe inside it;
    # otherwise (e.g. BEAM-DIMAP .dim) use the path directly.
    if safe_path.upper().endswith(".SAFE"):
        manifest = os.path.join(safe_path, "manifest.safe")
    else:
        manifest = safe_path

    _patch_speckle_filter(root, speckle, custom_params=speckle_params)
    if dem_name:
        _patch_dem(root, dem_name)

    write_node = None
    write_source_id = "Terrain-Correction"
    for node in root.findall("node"):
        op = node.find("operator")
        if op is not None and op.text == "Write":
            write_node = node
            sources = node.find("sources")
            if sources is not None:
                first = list(sources)
                if first:
                    write_source_id = first[0].get("refid", write_source_id)

    for node in root.findall("node"):
        op = node.find("operator")
        params = node.find("parameters")
        if op is None or params is None:
            continue
        if op.text == "Read":
            f = params.find("file")
            if f is not None:
                f.text = manifest

    # inject Subset before Write
    subset_id = "Subset_AOI"
    sub_node = ET.SubElement(root, "node", id=subset_id)
    ET.SubElement(sub_node, "operator").text = "Subset"
    src_el_parent = ET.SubElement(sub_node, "sources")
    src_el = ET.SubElement(src_el_parent, "sourceProduct")
    src_el.set("refid", write_source_id)
    params_el = ET.SubElement(sub_node, "parameters")
    ET.SubElement(params_el, "geoRegion").text = aoi_wkt
    ET.SubElement(params_el, "copyMetadata").text = "true"

    if write_node is not None:
        sources = write_node.find("sources")
        if sources is not None:
            for child in list(sources):
                child.set("refid", subset_id)
        params_w = write_node.find("parameters")
        if params_w is not None:
            f = params_w.find("file")
            if f is not None:
                f.text = snap_out
            fmt = params_w.find("formatName")
            if fmt is not None:
                fmt.text = "GeoTIFF-BigTIFF"
            else:
                el = ET.SubElement(params_w, "formatName")
                el.text = "GeoTIFF-BigTIFF"

    patched = snap_out.replace(".tif", "_graph.xml")
    tree.write(patched, encoding="unicode", xml_declaration=False)
    return patched


def _valid_raster(path, min_bands=1):
    """True only if `path` is a COMPLETE, decodable raster. Reads small blocks
    sampled across the image (corners + centre, every band) so a truncated or
    partially-written GeoTIFF — including corrupt interior blocks — is detected,
    not just a bad header.
    NOTE: this cannot catch a file that decodes fine but holds garbage values
    (e.g. the striped partial output SNAP can produce under memory pressure);
    that is a resource problem — lower the SNAP workers / JVM heap to avoid it."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 1024:
            return False
        import rasterio
        from rasterio.windows import Window
        with rasterio.open(path) as ds:
            if ds.count < min_bands or ds.width <= 0 or ds.height <= 0:
                return False
            W, H = ds.width, ds.height
            bw, bh = min(W, 16), min(H, 16)
            xs = sorted({0, max(0, W - bw), max(0, (W - bw) // 2)})
            ys = sorted({0, max(0, H - bh), max(0, (H - bh) // 2)})
            for b in range(1, ds.count + 1):       # force-decode blocks across the file
                for x in xs:
                    for y in ys:
                        ds.read(indexes=b, window=Window(x, y, bw, bh))
        return True
    except Exception:
        return False


def _run_snap_single(safe_path, output_path, gpt, graph, aoi, jvm,
                     speckle, speckle_params, dem_name, log, cfg,
                     date, sat, orbit, sc_label=""):
    """Run SNAP GPT on one SAFE file → output_path."""
    snap_log = output_path.replace(".tif", "_snap.log")
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmpdir:
        snap_out = os.path.join(tmpdir, "snap_full.tif")
        try:
            patched = _patch_graph(graph, safe_path, snap_out, aoi,
                                   speckle=speckle, speckle_params=speckle_params,
                                   dem_name=dem_name)
        except Exception as e:
            import traceback as _tb
            log(f"    Graph patch failed: {e}")
            _write_error(cfg, f"S1_{date}_{sat}_{orbit}{sc_label}", "snap_graph",
                         _tb.format_exc())
            return False

        # SNAP tile cache (-c): sized to ~70% of THIS process's heap, so it stays
        # inside the -Jmx allocation (safe with parallel workers — each gets its
        # own jvm MB). Left at SNAP's small default before, which starved the
        # tile-heavy Calibration/Terrain-Correction steps. Biggest single win.
        tile_mb = max(512, min(int(jvm * 0.7), jvm - 512))
        cmd = [gpt, patched, f"-Jmx{jvm}m", "-c", f"{tile_mb}M", "-q", "4"]
        _set_proc = cfg.get("set_proc_cb", lambda p: None)
        _stop_ev  = cfg.get("stop_event")
        _timed_out = False
        with open(snap_log, "w") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
            _set_proc(proc)
            try:
                proc.wait(timeout=5400)
            except subprocess.TimeoutExpired:
                _timed_out = True
                proc.kill(); proc.wait()
                log("    SNAP TIMEOUT — process killed")
            finally:
                _set_proc(None)

        # Graceful stop: if the user asked to stop but this SNAP run finished
        # cleanly on its own, publish it (below) so the scene counts as done and
        # gets its .done sentinel — don't throw away completed work. Only bail
        # when the process was force-killed / produced nothing.
        if (_stop_ev and _stop_ev.is_set()
                and (proc.returncode != 0 or not os.path.isfile(snap_out))):
            log("    [Stopped by user — scene not finished]")
            return False

        if proc.returncode != 0 or not os.path.isfile(snap_out):
            _snap_err = f"SNAP GPT returncode={proc.returncode}\n\n"
            try:
                with open(snap_log, encoding="utf-8", errors="replace") as _lf:
                    _snap_err += _lf.read()[-3000:]
            except Exception:
                pass
            stem = f"S1_{date}_{sat}_{orbit}{sc_label}"
            _write_error(cfg, stem, "snap", _snap_err)
            if _timed_out:
                log(f"    SNAP TIMEOUT after 90 min (rc={proc.returncode}) — the "
                    "scene didn't finish. Try more JVM heap (-Jmx) or fewer "
                    "parallel SNAP workers.")
            else:
                log(f"    SNAP FAILED (rc={proc.returncode})")
            log(f"    Error log → pipeline_errors/{stem}__snap.error.txt")
            try:
                _elog = os.path.join(_error_dir(cfg), f"{stem}__snap.log")
                os.makedirs(_error_dir(cfg), exist_ok=True)
                shutil.move(snap_log, _elog)
            except Exception:
                try: os.remove(snap_log)
                except Exception: pass
            return False

        # Atomic publish: copy into a temp sibling on the SAME filesystem,
        # then os.replace() into place. A Stop or crash can leave only the
        # .part file (cleaned up next run), never a half-written final that
        # skip-existing would wrongly treat as complete.
        _part = output_path + ".part"
        try:
            shutil.copy2(snap_out, _part)
            os.replace(_part, output_path)
        except Exception:
            try: os.remove(_part)
            except Exception: pass
            raise

    elapsed = time.time() - t0
    log(f"    OK  ({os.path.getsize(output_path) // 1024 // 1024} MB, {elapsed:.0f}s)")
    if os.path.isfile(snap_log):
        try: os.remove(snap_log)
        except Exception: pass
    # success -> drop any stale error files from a previous failed/interrupted try
    try:
        _stem = f"S1_{date}_{sat}_{orbit}{sc_label}"
        for _ef in glob.glob(os.path.join(_error_dir(cfg), _stem + "__*")):
            try: os.remove(_ef)
            except Exception: pass
    except Exception:
        pass
    return True


def _mask_raster_to_fields(tif_path, fields_path, bbox_wkt=""):
    """Set pixels outside the field polygons to nodata, in place.

    Only the fields intersecting the cluster bbox are used. The raster keeps
    its full bounding box but everything outside the field shapes becomes
    nodata, which DEFLATE compresses to almost nothing — so storage tracks the
    field area, not the box. Returns the number of field polygons applied, or
    None if there was nothing to mask.
    """
    import rasterio
    from rasterio.features import geometry_mask
    import geopandas as gpd
    from shapely import wkt as _wktmod

    fl = _read_fields(fields_path)
    if fl.crs is None:
        fl = fl.set_crs("EPSG:4326")
    fl = fl.to_crs("EPSG:4326")
    if bbox_wkt:
        try:
            fl = fl[fl.intersects(_wktmod.loads(bbox_wkt))]
        except Exception:
            pass
    fl = fl[fl.geometry.notna() & ~fl.geometry.is_empty]
    if len(fl) == 0:
        return None

    with rasterio.open(tif_path) as ds:
        prof = ds.profile.copy()
        data = ds.read()
        nd = ds.nodata if ds.nodata is not None else 0
        geoms = list(fl.to_crs(ds.crs).geometry.values)
        inside = geometry_mask(geoms, out_shape=(ds.height, ds.width),
                               transform=ds.transform, invert=True)

    outside = ~inside
    for b in range(data.shape[0]):
        data[b][outside] = nd
    _tiled = data.shape[1] >= 256 and data.shape[2] >= 256
    prof.update(nodata=nd, compress="deflate", predictor=2, bigtiff="IF_SAFER")
    if _tiled:
        prof.update(tiled=True, blockxsize=256, blockysize=256)
    else:
        prof.update(tiled=False)
        prof.pop("blockxsize", None); prof.pop("blockysize", None)
    tmp = tif_path + ".__mask.tif"
    with rasterio.open(tmp, "w", **prof) as dst:
        dst.write(data)
    os.replace(tmp, tif_path)
    return int(len(fl))


def _read_fields(path):
    """Read a vector file robustly. A GeoPackage on a read-only location makes
    GDAL fail with 'attempt to write a readonly database' (it tries to create
    metadata tables / a SQLite journal). In that case copy it to a writable
    temp file and read that instead."""
    import geopandas as gpd
    try:
        return gpd.read_file(path)
    except Exception as e:
        if "readonly" in str(e).lower() or path.lower().endswith((".gpkg", ".sqlite")):
            import tempfile, shutil, uuid
            tmp = os.path.join(tempfile.gettempdir(),
                               f"sf_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}")
            shutil.copy2(path, tmp)
            try:
                return gpd.read_file(tmp)
            finally:
                try: os.remove(tmp)
                except Exception: pass
        raise


def _union_bbox_wkt(sub_aois):
    """Bounding box (WKT, EPSG:4326) enclosing all cluster bboxes — used as the
    single SNAP subset region so one SNAP run covers every cluster."""
    from shapely import wkt as _wktmod
    from shapely.geometry import box
    from shapely.ops import unary_union
    geoms = []
    for s in sub_aois:
        try:
            geoms.append(_wktmod.loads(s["wkt"]))
        except Exception:
            pass
    if not geoms:
        return ""
    minx, miny, maxx, maxy = unary_union(geoms).bounds
    return box(minx, miny, maxx, maxy).wkt


def _cut_and_mask_clusters(proc_path, sub_aois, fields_path, name_fn, log,
                           skip_done=None):
    """From ONE geocoded SNAP raster, cut each cluster's window and mask it to
    the field polygons. No SNAP re-run and no re-projection of the data, so the
    geolocation is exactly the full-scene result (no shift). Clusters not
    covered by the scene (all-nodata window) are skipped. Returns written paths.
    """
    import rasterio
    from rasterio.windows import from_bounds as _wfb, Window
    from shapely import wkt as _wktmod
    import geopandas as gpd
    import numpy as np

    written = []
    with rasterio.open(proc_path) as ds:
        full = Window(0, 0, ds.width, ds.height)
        nd = ds.nodata if ds.nodata is not None else 0
        for s in sub_aois:
            tag = s.get("tag", "")
            if skip_done and skip_done(tag):
                continue
            try:
                bb = _wktmod.loads(s["wkt"])
            except Exception:
                continue
            bbm = gpd.GeoSeries([bb], crs="EPSG:4326").to_crs(ds.crs).total_bounds
            win = _wfb(*bbm, transform=ds.transform).round_offsets().round_lengths()
            try:
                win = win.intersection(full)
            except Exception:
                continue
            if win.width <= 0 or win.height <= 0:
                continue
            data = ds.read(window=win)
            if not np.any(data != nd):
                continue   # cluster outside this scene's footprint
            prof = ds.profile.copy()
            prof.update(height=int(win.height), width=int(win.width),
                        transform=ds.window_transform(win),
                        compress="deflate", predictor=2, bigtiff="IF_SAFER")
            if win.width >= 256 and win.height >= 256:
                prof.update(tiled=True, blockxsize=256, blockysize=256)
            else:
                prof.update(tiled=False)
                prof.pop("blockxsize", None); prof.pop("blockysize", None)
            outp = name_fn(tag)
            tmp = outp + ".__cut.tif"
            with rasterio.open(tmp, "w", **prof) as dst:
                dst.write(data)
            os.replace(tmp, outp)
            if fields_path:
                try:
                    _mask_raster_to_fields(outp, fields_path, s["wkt"])
                except Exception as _e:
                    log(f"    [mask {tag}] skipped: {_e}")
            written.append(outp)
    return written


def _cluster_polygons(gdf_wgs84, max_gap_km=5.0, edge_buffer_m=200.0):
    """
    Group nearby polygons into spatial clusters so a scattered AOI (many fields
    far apart) is processed as several tight bounding boxes instead of one giant
    envelope full of empty space.

    Two polygons end up in the same cluster when the gap between them is <=
    max_gap_km.  Each cluster's bounding box is padded by edge_buffer_m so edge
    fields are not clipped.

    Returns (clusters, coverage):
        clusters : list of {"tag": "c01", "wkt": <bbox WKT, EPSG:4326>,
                            "n": <n polygons>, "area_km2": <bbox area>}
                   ordered north->south, west->east for stable, readable tags.
        coverage : fraction of the overall envelope actually covered by the
                   field polygons (used to auto-suggest this mode).

    Distances are measured in the data's local UTM zone (good for regional
    AOIs); for AOIs spanning many UTM zones the metric is approximate but the
    grouping threshold still holds well enough for splitting.
    """
    import geopandas as gpd
    from shapely.ops import unary_union
    from shapely.geometry import box

    try:
        metric_crs = gdf_wgs84.estimate_utm_crs()
    except Exception:
        metric_crs = "EPSG:3857"
    gm = gdf_wgs84.to_crs(metric_crs)
    gm = gm[gm.geometry.notna() & ~gm.geometry.is_empty]
    if len(gm) == 0:
        return [], 1.0

    half = max(1.0, max_gap_km * 1000.0 / 2.0)
    buffered = gm.geometry.buffer(half)
    dissolved = unary_union(list(buffered.values))
    parts = list(dissolved.geoms) if dissolved.geom_type == "MultiPolygon" else [dissolved]

    parts_gdf = gpd.GeoDataFrame({"cid": list(range(len(parts)))},
                                 geometry=parts, crs=gm.crs)
    reps = gm.copy()
    reps["geometry"] = gm.geometry.representative_point()
    joined = gpd.sjoin(reps, parts_gdf, how="left", predicate="within")
    # a representative point may sit exactly on a shared boundary -> dedupe
    cid_per_poly = joined["cid"].groupby(joined.index).first()

    boxes_m = {}
    counts  = {}
    for idx, geom in gm.geometry.items():
        cid = cid_per_poly.get(idx, -1)
        minx, miny, maxx, maxy = geom.bounds
        if cid in boxes_m:
            bx0, by0, bx1, by1 = boxes_m[cid]
            boxes_m[cid] = (min(bx0, minx), min(by0, miny),
                            max(bx1, maxx), max(by1, maxy))
            counts[cid] += 1
        else:
            boxes_m[cid] = (minx, miny, maxx, maxy)
            counts[cid] = 1

    rows = []
    for cid, (bx0, by0, bx1, by1) in boxes_m.items():
        b = box(bx0 - edge_buffer_m, by0 - edge_buffer_m,
                bx1 + edge_buffer_m, by1 + edge_buffer_m)
        rows.append({"geom_m": b, "n": counts[cid]})
    # stable ordering by location
    rows.sort(key=lambda r: (-r["geom_m"].centroid.y, r["geom_m"].centroid.x))

    bxs_wgs = gpd.GeoSeries([r["geom_m"] for r in rows],
                            crs=metric_crs).to_crs("EPSG:4326")
    clusters = []
    for i, (r, gw) in enumerate(zip(rows, bxs_wgs), 1):
        clusters.append({"tag": f"c{i:02d}", "wkt": gw.wkt,
                         "n": r["n"], "area_km2": r["geom_m"].area / 1e6})

    total_poly = float(gm.geometry.area.sum())
    env = box(*gm.total_bounds).area
    coverage = (total_poly / env) if env > 0 else 1.0
    return clusters, coverage


def _snap_process(cfg, safe_dir, snap_dir, log, progress_cb=None):
    if progress_cb is None:
        progress_cb = lambda *a, **kw: None
    gpt            = cfg.get("gpt_path", DEFAULT_GPT)
    graph          = cfg.get("graph_path", DEFAULT_GRAPH)
    aoi            = cfg.get("aoi_wkt") or ""
    jvm            = cfg.get("jvm_mb", 10240)
    speckle        = cfg.get("speckle", "lee")
    speckle_params = cfg.get("speckle_params", None)
    dem_name       = cfg.get("dem_name", SNAP_DEM_DEFAULT)

    # Build a filename-safe speckle tag.
    # For "custom", use the actual SNAP filter name from speckle_params["filter"]
    # (e.g. "Lee Sigma" → "lee_sigma", "Refined Lee" → "refined_lee").
    if speckle == "custom" and speckle_params and "filter" in speckle_params:
        speckle_tag = speckle_params["filter"].lower().replace(" ", "_")
    else:
        speckle_tag = (speckle or "none").strip().lower()
    dem_tag = SNAP_DEM_TAGS.get(dem_name, "dem")

    # Remove leftovers from a previously interrupted run (temp publish files
    # and cluster intermediates) so nothing stale is picked up or skipped.
    try:
        import glob as _glob
        for _lf in (_glob.glob(os.path.join(snap_dir, "*.part")) +
                    _glob.glob(os.path.join(snap_dir, "*__full.tif"))):
            try: os.remove(_lf)
            except Exception: pass
    except Exception:
        pass

    # Build a filename-safe graph tag recording which preprocessing chain ran.
    # Built-in presets -> "sigma0" / "gamma0"; a custom graph -> its XML file
    # name reduced to alphanumerics (so the output name shows the graph used).
    _gpreset = (cfg.get("graph_preset") or "").strip().lower()
    if _gpreset in ("sigma0", "gamma0"):
        graph_tag = _gpreset
    else:
        _gstem = Path(cfg.get("graph_path", "") or "graph").stem
        graph_tag = (re.sub(r"[^A-Za-z0-9]+", "", _gstem).lower() or "graph")[:24]

    if not os.path.isfile(gpt):
        raise FileNotFoundError(f"GPT not found: {gpt}")
    if not os.path.isfile(graph):
        raise FileNotFoundError(f"Graph not found: {graph}")

    # Products extracted outside this app (e.g. manually with 7-Zip) may lack
    # the .SAFE suffix — add it so the glob below finds them.
    _normalize_safe_dirs(safe_dir, log)
    safes = sorted(set(
        glob.glob(os.path.join(safe_dir, "*.SAFE")) +
        glob.glob(os.path.join(safe_dir, "**", "*.SAFE"), recursive=True)
    ))
    # A .SAFE dir without a manifest.safe is a broken/empty product (interrupted
    # download or extraction). Feeding it to SNAP fails the whole scene with an
    # opaque rc=1; skip it here and tell the user to re-download that date.
    safes = [s for s in safes if os.path.isdir(s)]
    _broken = [s for s in safes if not os.path.isfile(os.path.join(s, "manifest.safe"))]
    for s in _broken:
        log(f"  ⚠ SKIP {os.path.basename(s)} — no manifest.safe "
            f"(empty/incomplete .SAFE; re-download this date)")
    safes = [s for s in safes if s not in _broken]

    # orbit filter
    orbit_filter = cfg["orbit_dir"]
    if orbit_filter != "Both":
        safes = [s for s in safes
                 if _parse_safe_name(s)[2] == orbit_filter]

    log(f"Processing {len(safes)} .SAFE files")
    if not safes:
        log("  Nothing to process")
        return

    aoi_label = cfg.get("aoi_label", "AOI")

    # Sub-AOIs: for a scattered field AOI the run builder splits the polygons
    # into spatial clusters (each a tight bounding box) so SNAP processes only
    # the field areas, not the empty space between them. Default = one sub-AOI
    # covering the whole AOI (identical to the legacy single-AOI behaviour).
    sub_aois = cfg.get("sub_aois") or [{"tag": "", "wkt": aoi}]
    if len(sub_aois) > 1:
        log(f"  Scattered-AOI mode: {len(sub_aois)} clusters — each cropped to "
            f"its own tight bounding box (less compute & storage)")

    # Group SAFEs by (date, orbit). NOTE: adjacent frames are mosaicked
    # post-hoc, NOT assembled with S1-Slice-Assembly (that path is disabled — see
    # README "Known limitations"), so a faint frame-boundary seam can remain.
    from collections import defaultdict as _dd
    _scene_groups = _dd(list)
    for _s in safes:
        _d, _sa, _orb = _parse_safe_name(_s)
        if _d:
            _scene_groups[(_d, _orb)].append(_s)

    total_groups    = len(_scene_groups)
    max_snap_workers = max(1, int(cfg.get("max_snap_workers", 1)))
    _stop_ev  = cfg.get("stop_event")
    _set_proc = cfg.get("set_proc_cb", lambda p: None)
    _lock_g   = threading.Lock()
    _g_done   = [0]   # mutable counter shared across workers

    # Count groups that actually need work (cheap existence check) so the
    # progress bar reflects real work on re-runs, instead of racing to 100%
    # over the skipped (already-done) scenes. The authoritative skip still
    # happens inside _do_group; this is only the bar's denominator.
    def _exists_ok(p):
        try: return os.path.isfile(p) and os.path.getsize(p) > 1024
        except Exception: return False

    def _grp_sentinel(date, orbit, sat0):
        # Written once SNAP+cut finishes for a cluster group. Authoritative
        # "STEP 2 done" marker: clusters outside the scene footprint never get
        # a .tif, so an all-clusters-present check re-runs the group forever.
        return os.path.join(snap_dir,
            f"S1_{date}_SNAP_{aoi_label}_{speckle_tag}_{dem_tag}"
            f"_{graph_tag}_{sat0}_{orbit}.done")

    def _grp_done_cheap(date, orbit, sat0):
        def _cog(tag):
            return (cfg.get("out_dir") and not _pending_index_error(cfg, date, orbit)
                    and glob.glob(os.path.join(cfg.get("out_dir", ""), orbit,
                        f"S1_{date}_SNAP_{aoi_label}_{tag}_*_{orbit}_*.tif")))
        if len(sub_aois) > 1:
            if os.path.isfile(_grp_sentinel(date, orbit, sat0)):
                return True
            for s in sub_aois:
                tag = s.get("tag", "")
                fp = os.path.join(snap_dir,
                    f"S1_{date}_SNAP_{aoi_label}_{tag}_{speckle_tag}_{dem_tag}"
                    f"_{graph_tag}_{sat0}_{orbit}.tif")
                if _exists_ok(fp) or _cog(tag):
                    continue
                return False
            return True
        fp = os.path.join(snap_dir,
            f"S1_{date}_SNAP_{aoi_label}_{speckle_tag}_{dem_tag}"
            f"_{graph_tag}_{sat0}_{orbit}.tif")
        return bool(_exists_ok(fp) or _cog(""))

    _to_process = 0
    for (_d, _o), _sl in _scene_groups.items():
        try:
            _, _sa0, _ = _parse_safe_name(_sl[0])
            if not _grp_done_cheap(_d, _o, _sa0):
                _to_process += 1
        except Exception:
            _to_process += 1
    _to_process = max(1, _to_process)
    _work_done  = [0]
    if _to_process < total_groups:
        log(f"  {_to_process} scene group(s) to process, "
            f"{total_groups - _to_process} already done (skipped)")

    log(f"  Parallel SNAP workers: {max_snap_workers}  "
        f"(~{max_snap_workers * jvm // 1024:.0f} GB JVM total)")

    def _do_group_once(key_slist):
        (date, orbit), slist = key_slist
        if _stop_ev and _stop_ev.is_set():
            return

        _, sat0, _ = _parse_safe_name(slist[0])
        n_tiles = len(slist)
        base = (f"S1_{date}_SNAP_{aoi_label}_{speckle_tag}_{dem_tag}"
                f"_{graph_tag}_{sat0}_{orbit}")
        multi = len(sub_aois) > 1          # cluster mode: 1 SNAP run, cut after

        with _lock_g:
            _g_done[0] += 1
            _g = _g_done[0]

        def _mark_skip(lbl):
            progress_cb("process", _work_done[0], _to_process, lbl)

        def _mark_done(lbl):
            with _lock_g:
                _work_done[0] += 1; _wn = _work_done[0]
            progress_cb("process", _wn, _to_process, lbl)

        def _clpath(tag):
            return os.path.join(
                snap_dir,
                f"S1_{date}_SNAP_{aoi_label}_{tag}_{speckle_tag}_{dem_tag}"
                f"_{graph_tag}_{sat0}_{orbit}.tif")

        def _cluster_done(tag):
            if _valid_raster(_clpath(tag)):
                return True
            if cfg.get("out_dir") and not _pending_index_error(cfg, date, orbit):
                pat = os.path.join(cfg["out_dir"], orbit,
                    f"S1_{date}_SNAP_{aoi_label}_{tag}_*_{orbit}_*.tif")
                if glob.glob(pat):
                    return True
            return False

        # ── skip logic + choose the single SNAP subset region ──────────────
        if multi:
            _sentinel = _grp_sentinel(date, orbit, sat0)
            if os.path.isfile(_sentinel):
                log(f"  [{_g}/{total_groups}] SKIP {date} [{orbit}]  (group done)")
                _mark_skip(f"{date} [{orbit}]  skip")
                return
            if all(_cluster_done(s.get("tag", "")) for s in sub_aois):
                log(f"  [{_g}/{total_groups}] SKIP {date} [{orbit}]  "
                    f"(all {len(sub_aois)} clusters done)")
                _mark_skip(f"{date} [{orbit}]  skip")
                return
            proc_path = os.path.join(snap_dir, base + "__full.tif")
            snap_wkt  = _union_bbox_wkt(sub_aois)
        else:
            final_path = os.path.join(snap_dir, base + ".tif")
            if _valid_raster(final_path):
                log(f"  [{_g}/{total_groups}] SKIP {date} [{orbit}]  (SNAP GeoTIFF exists)")
                _mark_skip(f"{date} [{orbit}]  skip")
                return
            _cog_pattern = os.path.join(cfg.get("out_dir", ""), orbit,
                                        f"S1_{date}_SNAP_{aoi_label}_*_{orbit}_*.tif")
            if (cfg.get("out_dir") and not _pending_index_error(cfg, date, orbit)
                    and glob.glob(_cog_pattern)):
                log(f"  [{_g}/{total_groups}] SKIP {date} [{orbit}]  (COG indices already exist)")
                _mark_skip(f"{date} [{orbit}]  skip")
                return
            proc_path = final_path
            snap_wkt  = sub_aois[0]["wkt"]

        _mark_skip(f"{date} [{orbit}]  SNAP…")

        # ── run SNAP ONCE into proc_path (subset applied after TC, no shift) ─
        ok = False
        if n_tiles == 1:
            _cl = f" -> {len(sub_aois)} clusters" if multi else ""
            log(f"  [{_g}/{total_groups}] Processing {date} [{orbit}] (1 frame){_cl}...")
            ok = _run_snap_single(
                slist[0], proc_path, gpt, graph, snap_wkt, jvm,
                speckle, speckle_params, dem_name, log, cfg,
                date, sat0, orbit)
        else:
            slist_sorted  = sorted(slist)
            mosaic_method = cfg.get("mosaic_method", "both")
            _hm = mosaic_method in ("histmatch", "both")
            log(f"  [{_g}/{total_groups}] {date} [{orbit}]: "
                f"per-tile ({n_tiles} frames - seam interpolation)...")
            tiles = []
            for si, sp in enumerate(slist_sorted, 1):
                if _stop_ev and _stop_ev.is_set():
                    log("    [Stopped by user]"); return
                _, sa_i, _ = _parse_safe_name(sp)
                int_path = os.path.join(snap_dir, f"{base}_sc{si:02d}.tif")
                log(f"    Tile {si}/{n_tiles}: {Path(sp).name}")
                if not os.path.isfile(int_path):
                    _run_snap_single(sp, int_path, gpt, graph, snap_wkt, jvm,
                                     speckle, speckle_params, dem_name, log, cfg,
                                     date, sa_i, orbit, sc_label=f"_sc{si:02d}")
                if os.path.isfile(int_path):
                    tiles.append(int_path)
            if tiles:
                try:
                    import rasterio as _rio
                    with _rio.open(tiles[0]) as _ds:
                        _nd = _ds.nodata if _ds.nodata is not None else 0
                except Exception:
                    _nd = 0
                log(f"    Mosaicking {len(tiles)} tiles (histmatch={'yes' if _hm else 'no'})...")
                try:
                    if _hm:
                        _mosaic_interpolate(tiles, proc_path, _nd, log, edge_trim_rows=60)
                    else:
                        _mosaic_feathered(tiles, proc_path, _nd, log, edge_trim_rows=100)
                    for tp in tiles:
                        try: os.remove(tp)
                        except Exception: pass
                    ok = os.path.isfile(proc_path)
                except Exception as _me:
                    log(f"    Mosaic failed: {_me}")

        if not ok or not _valid_raster(proc_path):
            _mark_done(f"{date} [{orbit}]  ✗")
            return

        # ── derive deliverables ────────────────────────────────────────────
        if multi:
            # One SNAP run done; cut each cluster window + mask, in Python.
            try:
                written = _cut_and_mask_clusters(
                    proc_path, sub_aois, cfg.get("fields_path", ""),
                    _clpath, log, skip_done=_cluster_done)
                log(f"    {len(written)} cluster raster(s) cut from one SNAP run"
                    + (" (masked to fields)" if cfg.get("fields_path") else ""))
                # Mark the group finished so a later restart skips it — clusters
                # outside this scene's footprint never produce a .tif, so an
                # all-clusters-present check would re-run this date forever.
                try:
                    with open(_grp_sentinel(date, orbit, sat0), "w",
                              encoding="utf-8") as _sf:
                        _sf.write("\n".join(os.path.basename(w) for w in written))
                except Exception:
                    pass
            except Exception as _ce:
                log(f"    [cut] failed: {_ce}")
            finally:
                try: os.remove(proc_path)
                except Exception: pass
            _mark_done(f"{date} [{orbit}]  ✓ ({len(sub_aois)} clusters)")
        else:
            if cfg.get("fields_path") and os.path.isfile(final_path):
                try:
                    _nf = _mask_raster_to_fields(final_path, cfg["fields_path"], snap_wkt)
                    if _nf is not None:
                        log(f"    masked to {_nf} field polygons (nodata outside)")
                except Exception as _mk:
                    log(f"    [mask] skipped: {_mk}")
            _mark_done(f"{date} [{orbit}]  ✓")

    def _do_group(key_slist):
        # Survive an output-drive disconnect mid-run: on a drive-gone OSError,
        # wait for the drive to come back, then retry the same group. Groups are
        # idempotent (skip-existing checks + atomic publish), so a retry is safe.
        # ponytail: re-checks per worker; a retry may re-print the [g/total] index
        # — cosmetic, only happens on an actual disconnect.
        while True:
            try:
                return _do_group_once(key_slist)
            except OSError as e:
                if not _is_drive_gone(e):
                    raise
                log(f"  [drive lost during {key_slist[0]}] {e}")
                if not _wait_for_drive(snap_dir, log, _stop_ev):
                    return

    _tasks = sorted(_scene_groups.items())
    with ThreadPoolExecutor(max_workers=max_snap_workers) as pool:
        futures = {pool.submit(_do_group, t): t for t in _tasks}
        for fut in as_completed(futures):
            if _stop_ev and _stop_ev.is_set():
                for f in futures: f.cancel()
                log("  [Stopped by user]")
                break
            try:
                fut.result()
            except Exception as e:
                log(f"  [UNEXPECTED SNAP error] {e}")

# ── step 3: compute indices ───────────────────────────────────────────────────


def _write_raster_atomic(final_path, profile, data):
    """Write a raster atomically: temp sibling + os.replace, so an interrupted
    write (Stop/crash) never leaves a half-written final file that skip-existing
    would treat as complete."""
    import rasterio
    part = final_path + ".part"
    try:
        with rasterio.open(part, "w", **profile) as dst:
            dst.write(data)
        os.replace(part, final_path)
    except BaseException:
        try: os.remove(part)
        except Exception: pass
        raise


def _mosaic_interpolate(tiles, final_path, nodata_val, log, edge_trim_rows=50):
    """
    Merge per-tile outputs with seam-only edge trim + vertical bilinear fill.

    Steps:
    1. Reproject each tile onto merged grid.
    2. Trim the last `edge_trim_rows` of VALID DATA from the bottom of each
       tile only — this removes the SNAP TC edge artefact at the frame
       boundary without creating a large gap (binary_erosion would eat all
       edges, creating a 2*N-row gap).
    3. First-valid-pixel merge → leaves a narrow nodata strip at the seam.
    4. Vertical bilinear fill: for each nodata pixel blend the nearest valid
       row above (tile 1) and below (tile 2) by distance.  Smooth, no blocks.
    """
    import rasterio
    from rasterio.merge import merge as _rio_merge
    from rasterio.warp import reproject, Resampling
    import numpy as np

    srcs = [rasterio.open(t) for t in tiles]
    try:
        merged_preview, transform = _rio_merge(srcs, nodata=nodata_val,
                                               method="first")
        n_bands  = srcs[0].count
        out_h, out_w = merged_preview.shape[1], merged_preview.shape[2]
        profile = srcs[0].profile.copy()
        profile.update(width=out_w, height=out_h, transform=transform,
                       compress="deflate", predictor=2,
                       tiled=True, bigtiff="IF_SAFER", dtype="float32")

        nd = float(nodata_val) if nodata_val is not None else 0.0

        # Reproject tiles
        tile_data = []
        for src in srcs:
            data = np.full((n_bands, out_h, out_w), nd, dtype=np.float32)
            for b in range(1, n_bands + 1):
                reproject(
                    source=rasterio.band(src, b),
                    destination=data[b - 1],
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=srcs[0].crs,
                    resampling=Resampling.bilinear,
                    src_nodata=nd, dst_nodata=nd,
                )
            tile_data.append(data)

        # Trim bottom edge of each tile only (TC artefact at bottom of each frame).
        # "bottom" = the last row that has valid data in that tile.
        for data in tile_data:
            valid_rows = np.where(
                np.any(data != nd, axis=(0, 2))   # rows that have any valid pixel
            )[0]
            if len(valid_rows) == 0:
                continue
            last_row = valid_rows[-1]
            trim_start = max(0, last_row - edge_trim_rows + 1)
            data[:, trim_start:last_row + 1, :] = nd

        # First-valid-pixel merge
        output = np.full((n_bands, out_h, out_w), nd, dtype=np.float32)
        for data in tile_data:
            valid = np.any(data != nd, axis=0)      # (H, W) bool
            for b in range(n_bands):
                take = valid & (output[b] == nd)
                output[b] = np.where(take, data[b], output[b])

        # Vertical bilinear fill for remaining nodata pixels
        filled_any = False
        for b in range(n_bands):
            band = output[b].copy()
            nodata_mask = band == nd
            if not nodata_mask.any():
                continue
            filled_any = True

            # Propagate last valid value downward (above neighbour)
            fill_down = band.copy()
            for r in range(1, out_h):
                fill_down[r] = np.where(nodata_mask[r],
                                        fill_down[r - 1], fill_down[r])

            # Propagate first valid value upward (below neighbour)
            fill_up = band.copy()
            for r in range(out_h - 2, -1, -1):
                fill_up[r] = np.where(nodata_mask[r],
                                      fill_up[r + 1], fill_up[r])

            # Row distance to nearest valid pixel above / below
            above_dist = np.zeros((out_h, out_w), dtype=np.float32)
            for r in range(1, out_h):
                above_dist[r] = np.where(nodata_mask[r],
                                         above_dist[r - 1] + 1, 0)

            below_dist = np.zeros((out_h, out_w), dtype=np.float32)
            for r in range(out_h - 2, -1, -1):
                below_dist[r] = np.where(nodata_mask[r],
                                         below_dist[r + 1] + 1, 0)

            total = above_dist + below_dist
            total = np.where(total == 0, 1.0, total)
            w_above = below_dist / total
            w_below = above_dist / total
            blended = w_above * fill_down + w_below * fill_up
            output[b] = np.where(nodata_mask, blended, band)

        residual_rows = int(np.sum(np.all(output == nd, axis=(0, 2))))
        _write_raster_atomic(final_path, profile, output)
        if filled_any:
            msg = (f"bilinear fill applied (trim={edge_trim_rows} rows, "
                   f"residual nodata={residual_rows} rows)")
        else:
            msg = "no seam gap (tiles overlap — no fill needed)"
        log(f"    interpolated mosaic OK  ({os.path.getsize(final_path)//1024//1024} MB, {msg})")

    finally:
        for src in srcs:
            try: src.close()
            except Exception: pass



def _mosaic_feathered(tiles, final_path, nodata_val, log, edge_trim_rows=100,
                      histmatch=False):
    """
    Merge tiles with distance-weighted feathering in the overlap zone.
    Each pixel in the overlap gets a weight proportional to its distance
    from the edge of the valid area of each tile — so the seam dissolves
    into a gradual transition rather than a hard cut.
    Falls back to simple rasterio.merge if scipy is not available.
    """
    import rasterio
    from rasterio.merge import merge as _rio_merge
    from rasterio.warp import reproject, Resampling
    import numpy as np

    try:
        from scipy.ndimage import distance_transform_edt
        has_scipy = True
    except ImportError:
        has_scipy = False

    srcs = [rasterio.open(t) for t in tiles]
    try:
        # Compute merged extent / transform using rasterio
        merged_preview, transform = _rio_merge(srcs, nodata=nodata_val,
                                               method="first")
        n_bands   = srcs[0].count
        out_h, out_w = merged_preview.shape[1], merged_preview.shape[2]
        profile   = srcs[0].profile.copy()
        profile.update(width=out_w, height=out_h, transform=transform,
                       compress="deflate", predictor=2,
                       tiled=True, bigtiff="IF_SAFER", dtype="float32")

        if not has_scipy:
            # Simple merge — first valid pixel wins (no feathering)
            log("    [mosaic] scipy not found — using simple merge (no feathering)")
            _write_raster_atomic(final_path, profile, merged_preview.astype("float32"))
            return

        # Reproject each tile onto the merged grid and build weight arrays
        nd = float(nodata_val) if nodata_val is not None else 0.0
        tile_data    = []   # list of (n_bands, H, W) float32 arrays
        tile_weights = []   # list of (H, W) float32 weight arrays

        for src in srcs:
            data = np.full((n_bands, out_h, out_w), nd, dtype=np.float32)
            for b in range(1, n_bands + 1):
                reproject(
                    source=rasterio.band(src, b),
                    destination=data[b - 1],
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform,    dst_crs=srcs[0].crs,
                    resampling=Resampling.bilinear,
                    src_nodata=nd, dst_nodata=nd,
                )
            # Valid mask: pixel is valid if ALL bands are non-nodata
            valid = np.all(data != nd, axis=0).astype(np.float32)
            # Distance from the nearest nodata/edge pixel — gives feather weight
            weight = distance_transform_edt(valid).astype(np.float32)
            # Trim edge zone to eliminate SNAP border artifacts
            if edge_trim_rows > 0:
                weight = np.maximum(0.0, weight - float(edge_trim_rows)).astype(np.float32)
            tile_data.append(data)
            tile_weights.append(weight)

        # ── Hard join (histmatch=True path) ─────────────────────────────
        # S1 consecutive frames from same orbit have identical radiometry —
        # no brightness correction needed.  Just pick the tile whose pixel
        # is farthest from its own edge (= highest distance-weight), giving
        # a seamless hard cut without any blending zone.
        if histmatch and len(tile_data) > 1:
            log("    [mosaic] hard join (edge-priority, no histmatch)")
            output = np.full((n_bands, out_h, out_w), nd, dtype=np.float32)
            winner = np.full((out_h, out_w), -1, dtype=np.int32)
            best_w = np.full((out_h, out_w), -1.0, dtype=np.float32)
            for ti, (data, weight) in enumerate(zip(tile_data, tile_weights)):
                has_val = np.all(data != nd, axis=0)
                update  = has_val & (weight > best_w)
                winner  = np.where(update, ti, winner)
                best_w  = np.where(update, weight, best_w)
            for b in range(n_bands):
                for ti, data in enumerate(tile_data):
                    output[b] = np.where(winner == ti, data[b], output[b])
            _write_raster_atomic(final_path, profile, output)
            log(f"    hard-join mosaic OK  ({os.path.getsize(final_path)//1024//1024} MB)")
            return

        # Feather-only blend (no histmatch)
        weight_sum = np.sum(tile_weights, axis=0)
        weight_sum = np.where(weight_sum > 0, weight_sum, 1.0)

        output = np.full((n_bands, out_h, out_w), nd, dtype=np.float32)
        for b in range(n_bands):
            blended   = np.zeros((out_h, out_w), dtype=np.float32)
            any_valid = np.zeros((out_h, out_w), dtype=bool)
            for data, weight in zip(tile_data, tile_weights):
                valid_b    = data[b] != nd
                blended   += np.where(valid_b, data[b] * weight, 0.0)
                any_valid |= valid_b
            output[b] = np.where(any_valid, blended / weight_sum, nd)

        _write_raster_atomic(final_path, profile, output)

        log(f"    feathered mosaic OK  ({os.path.getsize(final_path)//1024//1024} MB)")

    finally:
        for src in srcs:
            try: src.close()
            except Exception: pass


def _find_gdal_translate(user_path=None):
    if user_path and os.path.isfile(user_path):
        return user_path
    exe = shutil.which("gdal_translate")
    if exe:
        return exe
    # scan Program Files for any QGIS
    pf = r"C:\Program Files"
    candidates = [
        r"C:\OSGeo4W\bin\gdal_translate.exe",
        r"C:\OSGeo4W64\bin\gdal_translate.exe",
    ]
    if os.path.isdir(pf):
        for entry in sorted(os.listdir(pf), reverse=True):
            if entry.lower().startswith("qgis"):
                candidates.insert(0, os.path.join(pf, entry, "bin", "gdal_translate.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _to_cog(src, dst, gdal_tr):
    cmd = [gdal_tr, "-of", "COG",
           "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
           "-co", "ZLEVEL=9", "-co", "BIGTIFF=IF_SAFER",
           "-co", "RESAMPLING=NEAREST", src, dst + ".part.tif"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        try: os.remove(dst + ".part.tif")
        except Exception: pass
        raise RuntimeError("gdal_translate timed out (>900s)")
    if r.returncode != 0:
        try: os.remove(dst + ".part.tif")
        except Exception: pass
        raise RuntimeError(f"gdal_translate failed: {r.stderr[:300]}")
    os.replace(dst + ".part.tif", dst)   # atomic: no half-written COG left behind


def _write_tmp(arr, profile, path):
    import rasterio
    import numpy as np
    prof = profile.copy()
    prof.update({"count":1,"dtype":"float32","nodata":NODATA,"driver":"GTiff"})
    for k in ["blockxsize","blockysize","tiled","compress","predictor","overview_level"]:
        prof.pop(k, None)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype("float32")[np.newaxis,:,:])


def _compute_indices(cfg, snap_dir, out_dir, log, progress_cb=None):
    if progress_cb is None:
        progress_cb = lambda *a, **kw: None
    try:
        import numpy as np
        import rasterio
    except ImportError as e:
        raise ImportError(
            f"Missing Python package: {e}\n"
            "HOW TO FIX:\n"
            "  1. Open a terminal in the project folder\n"
            "  2. Activate venv:  .venv\\Scripts\\activate\n"
            "  3. Run: pip install rasterio numpy")

    gdal_tr = _find_gdal_translate(cfg.get("gdal_path", ""))
    if not gdal_tr:
        raise FileNotFoundError(
            "gdal_translate not found. Set the path in section 9 Tool Paths of the UI.")
    log(f"  gdal_translate: {gdal_tr}")

    selected_bands = [b for b in ALL_BANDS if cfg["bands"].get(b, True)]
    scales = cfg["scales"]  # list of "linear", "db", or both
    log(f"  Bands: {selected_bands}  Scales: {scales}")

    aoi_label = cfg.get("aoi_label", "")
    _snap_pattern = f"S1_*_SNAP_{aoi_label}_*.tif" if aoi_label else "S1_*_SNAP_*.tif"
    tifs = sorted(t for t in glob.glob(os.path.join(snap_dir, _snap_pattern))
                  if not re.search(r"_sc\d+\.tif$", t))
    if not tifs:
        log("  No SNAP tiles found")
        return


    _stop_ev_idx    = cfg.get("stop_event")
    max_idx_workers = max(1, int(cfg.get("max_idx_workers", 4)))
    total           = len(tifs)
    _lock           = threading.Lock()
    completed       = 0
    _seen_idx       = set()
    _failed_idx     = set()

    log(f"  Parallel index workers: {max_idx_workers}")

    def _process_one(snap_path):
        nonlocal completed

        if _stop_ev_idx and _stop_ev_idx.is_set():
            return

        stem = Path(snap_path).stem
        m = re.search(r'S1_(\d{8})_SNAP_.*?_(ASC|DSC)', stem)
        if not m:
            return
        date, orbit = m.group(1), m.group(2)

        orbit_outdir = os.path.join(out_dir, orbit)
        os.makedirs(orbit_outdir, exist_ok=True)

        try:
            with rasterio.open(snap_path) as src:
                if src.count < 2:
                    log(f"  WARNING: only {src.count} bands in {stem} — skipping")
                    with _lock:
                        completed += 1; _c = completed
                    progress_cb("process", _c, total, f"{date} [{orbit}]  skip")
                    return
                # Map VH/VV by band description — a custom SNAP graph may emit
                # VV first. Fall back to the historical VH=1 / VV=2 order.
                _descs = [str(d).upper() if d else "" for d in (src.descriptions or ())]
                _vh_b = next((i + 1 for i, d in enumerate(_descs) if "VH" in d), None)
                _vv_b = next((i + 1 for i, d in enumerate(_descs) if "VV" in d), None)
                if _vh_b is None or _vv_b is None or _vh_b == _vv_b:
                    if any(_descs):
                        log(f"  [note] {stem}: band names {src.descriptions} are not "
                            f"VH/VV — assuming band1=VH, band2=VV")
                    _vh_b, _vv_b = 1, 2
                vh_raw = src.read(_vh_b).astype("float64")
                vv_raw = src.read(_vv_b).astype("float64")
                profile = src.profile.copy()

            vv    = np.where((vv_raw > 0) & np.isfinite(vv_raw), vv_raw, np.nan)
            vh    = np.where((vh_raw > 0) & np.isfinite(vh_raw), vh_raw, np.nan)
            valid = np.isfinite(vv) & np.isfinite(vh)

            def fill(arr):
                out = np.full_like(arr, NODATA, dtype="float32")
                out[valid] = arr[valid].astype("float32")
                return out

            cr   = np.where(valid & (vv!=0), vh/vv, NODATA).astype("float32")
            den  = vv + vh
            rvi  = np.where(valid & (den!=0), 4.0*vh/den, NODATA).astype("float32")
            diff = np.where(valid, vh - vv, NODATA).astype("float32")


            bands_lin = {
                "VV": fill(vv), "VH": fill(vh), "CR": cr, "RVI": rvi, "DIFF": diff
            }

            with tempfile.TemporaryDirectory() as tmpdir:
                for band in selected_bands:
                    if _stop_ev_idx and _stop_ev_idx.is_set():
                        return
                    arr_lin = bands_lin[band]
                    for scale in scales:
                        if scale == "db" and band in ("CR","RVI","DIFF"):
                            continue
                        if scale == "db":
                            with np.errstate(divide="ignore", invalid="ignore"):
                                arr_out = np.where(arr_lin > 0,
                                                   10.0*np.log10(arr_lin),
                                                   NODATA).astype("float32")
                            arr_out[~np.isfinite(arr_out)] = NODATA
                            suffix = "_dB"
                        else:
                            arr_out = arr_lin
                            suffix = "_lin" if len(scales) > 1 else ""

                        tag     = f"{stem}_{band}{suffix}"
                        dst_tif = os.path.join(orbit_outdir, f"{tag}.tif")
                        if os.path.isfile(dst_tif):
                            continue
                        tmp_tif = os.path.join(tmpdir, f"{tag}_tmp.tif")
                        _write_tmp(arr_out, profile, tmp_tif)
                        _to_cog(tmp_tif, dst_tif, gdal_tr)

                # ── custom bands ─────────────────────────────────────────────
                _cust_ns = {
                    "VV": fill(vv), "VH": fill(vh),
                    "CR": cr, "RVI": rvi, "DIFF": diff,
                    "np": np, "NODATA": NODATA,
                }
                for ci in cfg.get("custom_bands", []):
                    cname = ci.get("name", "").strip()
                    cexpr = ci.get("expr", "").strip()
                    if not cname or not cexpr:
                        continue
                    dst_tif = os.path.join(orbit_outdir, f"{stem}_{cname}_lin.tif")
                    if os.path.isfile(dst_tif):
                        continue
                    try:
                        _validate_expr(cexpr, [k for k in _cust_ns if k != "np"])
                        arr_out = eval(cexpr, {"__builtins__": {}}, _cust_ns)
                        arr_out = np.where(np.isfinite(arr_out),
                                           arr_out, NODATA).astype("float32")
                        tmp_tif = os.path.join(tmpdir, f"{stem}_{cname}_lin_tmp.tif")
                        _write_tmp(arr_out, profile, tmp_tif)
                        _to_cog(tmp_tif, dst_tif, gdal_tr)
                    except Exception as _ce:
                        log(f"    [custom band '{cname}'] ERROR: {_ce}")

            # ── per-scene SNAP cleanup (only on full success) ─────────────
            if cfg.get("clean_snap"):
                try:
                    os.remove(snap_path)
                except Exception:
                    pass

            with _lock:
                completed += 1; _c = completed
                _seen_idx.add((date, orbit))
            log(f"  [{_c}/{total}] {date} [{orbit}]  "
                f"{len(selected_bands)*len(scales)} bands ✓")
            progress_cb("process", _c, total, f"{date} [{orbit}]  ✓")

        except Exception as _idx_err:
            import traceback as _tb
            _stem_err = f"S1_{date}_{orbit}"
            _err_msg  = str(_idx_err)
            # Detect disk-full explicitly so the user knows what to do
            _is_diskfull = any(s in _err_msg.lower() for s in
                               ("no space left", "enospc", "write error", "write failed"))
            if _is_diskfull:
                log(f"  ✗ DISK FULL — {date} [{orbit}]: no space left on device")
                log(f"    SNAP GeoTIFF kept: {snap_path}")
                log(f"    Free up disk space, then re-run — skip-existing will resume.")
            else:
                log(f"  ERROR {date} [{orbit}]: {_idx_err}")
                log(f"    Error log → pipeline_errors/{_stem_err}__indices.error.txt")
            _write_error(cfg, _stem_err, "indices", _tb.format_exc())
            with _lock:
                completed += 1; _c = completed
                _seen_idx.add((date, orbit)); _failed_idx.add((date, orbit))
            progress_cb("process", _c, total, f"{date} [{orbit}]  ✗ disk full" if _is_diskfull else f"{date} [{orbit}]  ✗")

    with ThreadPoolExecutor(max_workers=max_idx_workers) as pool:
        futures = {pool.submit(_process_one, p): p for p in tifs}
        for fut in as_completed(futures):
            if _stop_ev_idx and _stop_ev_idx.is_set():
                for f in futures: f.cancel()
                log("  [Stopped by user]")
                break
            try:
                fut.result()
            except Exception as e:
                log(f"  [UNEXPECTED] {e}")

    # Clear stale indices error files for dates that fully succeeded this run
    # (every cluster produced its COGs), so they are not re-flagged next time.
    try:
        for _d, _o in (_seen_idx - _failed_idx):
            _ef = os.path.join(_error_dir(cfg), f"S1_{_d}_{_o}__indices.error.txt")
            if os.path.isfile(_ef):
                os.remove(_ef)
    except Exception:
        pass


def _clean_safe(safe_dir, log, snap_dir=None, cfg=None):
    """Delete extracted .SAFE dirs in safe_dir.

    When snap_dir is given, a .SAFE is deleted ONLY once a GeoTIFF (or .done
    marker) for its (date, orbit) exists — so a run stopped or failed mid-SNAP
    keeps the not-yet-converted .SAFE for the next start instead of losing that
    date (the zip is already gone by this point). snap_dir=None → legacy
    unconditional delete."""
    safes = sorted(set(
        glob.glob(os.path.join(safe_dir, "*.SAFE")) +
        glob.glob(os.path.join(safe_dir, "**", "*.SAFE"), recursive=True)
    ))
    safes = [s for s in safes if os.path.isdir(s)]

    def _converted(s):
        if snap_dir is None:
            return True
        date, _sat, orbit = _parse_safe_name(s)
        if not date:
            return False   # unparseable name — keep it, never risk deleting good data
        pats = [os.path.join(snap_dir, f"S1_{date}_*_{orbit}*.tif"),
                os.path.join(snap_dir, f"S1_{date}_*_{orbit}*.done")]
        if cfg and cfg.get("out_dir"):
            pats.append(os.path.join(cfg["out_dir"], orbit, f"S1_{date}_*_{orbit}_*.tif"))
        return any(glob.glob(p) for p in pats)

    removed = kept = 0
    for s in safes:
        if _converted(s):
            try: shutil.rmtree(s); removed += 1
            except Exception: pass
        else:
            kept += 1
    if kept:
        log(f"  Removed {removed} converted .SAFE; KEPT {kept} not-yet-converted "
            f".SAFE for next run (stopped/failed mid-SNAP)")
    else:
        log(f"  Removed {removed} .SAFE directories from {safe_dir}")


# ═══════════════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        # Distinct AppUserModelID so the Windows taskbar gives this window its own
        # icon instead of grouping it under pythonw.exe (the default feather).
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "SentinelFoundry.Sar")
            except Exception:
                pass
        super().__init__()
        self.title("SAR Foundry")
        self.configure(bg=BG)

        # Icon: the multi-res .ico MUST be set first — it carries the large sizes
        # the Windows taskbar uses; the .png iconphoto then covers the title bar
        # and non-Windows. (base64 64px alone left the taskbar on pythonw's icon.)
        try:
            _ico = os.path.join(_SCRIPT_DIR, "sentinel_foundry.ico")
            _png = os.path.join(_SCRIPT_DIR, "sentinel_foundry.png")
            if os.name == "nt" and os.path.isfile(_ico):
                self.iconbitmap(default=_ico)
            if os.path.isfile(_png):
                self._iconimg = tk.PhotoImage(file=_png)
                self.iconphoto(True, self._iconimg)
        except Exception:
            pass
        # crisp multi-res title-bar + taskbar icon from the real .ico (Windows)
        try:
            _ico = os.path.join(_SCRIPT_DIR, "sentinel_foundry.ico")
            if os.name == "nt" and os.path.isfile(_ico):
                self.iconbitmap(default=_ico)
        except Exception:
            pass
        self.resizable(True, True)
        self.minsize(1100, 720)

        self._running    = False
        self._thread     = None
        self._stop_event = threading.Event()
        self._force_event = threading.Event()  # set on 2nd Stop press → abort in-flight download
        self._cur_procs  = set()              # live SNAP/GPT subprocesses
        self._proc_lock  = threading.Lock()   # guards _cur_procs
        self._dep_results = {}  # filled by dep check; used to block START if critical missing
        self._saved_cfg = _load_config()  # load once here, used throughout _build_form

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._start_gdal_log_tail()
        self.update_idletasks()
        # centre on screen
        w, h = 1100, 860
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        w = max(w, 1280); h = max(h, 720)  # extra width goes to the right panel (stretch='always')
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(1100, 720)
        # run dependency check after window is shown
        self.after(300, self._run_dep_check)

    # ── build UI ─────────────────────────────────────────────────────────────

    def _update_idx_workers_label(self):
        """Refresh the live CPU label under the index workers slider."""
        if not hasattr(self, "_lbl_idx_cpu"):
            return
        try:
            workers = int(self.v_idx_workers.get())
            # physical cores (best indicator of true parallelism)
            try:
                import psutil
                phys  = psutil.cpu_count(logical=False) or 1
                logic = psutil.cpu_count(logical=True)  or phys
            except ImportError:
                import os
                logic = os.cpu_count() or 4
                phys  = max(1, logic // 2)

            if workers <= phys:
                color = GREEN
                note  = "✓ within physical cores"
            elif workers <= logic:
                color = GOLD
                note  = "⚠ above physical cores — hyperthreading territory"
            else:
                color = RED
                note  = "✗ exceeds logical CPU count — will slow down"

            msg = (f"  {workers} thread{'s' if workers>1 else ''} / "
                   f"{phys} physical cores  ({logic} logical)  →  {note}")
            self._lbl_idx_cpu.configure(text=msg, fg=color)
        except Exception:
            pass

    def _unzip_stage_cfg(self):
        """Value for cfg["unzip_stage_dir"] from the checkbox: "off" when unchecked,
        else the previously-saved explicit path if a power-user set one, else "auto"."""
        if not self.v_unzip_stage.get():
            return "off"
        prev = str(self._saved_cfg.get("unzip_stage_dir", "auto")).strip()
        return prev if prev.lower() not in ("", "off", "auto") else "auto"

    def _update_unzip_cpu_label(self):
        """Refresh the live PC-load warning under the unzip workers spinbox."""
        if not hasattr(self, "_lbl_unzip_cpu"):
            return
        try:
            workers = int(self.v_unzip_workers.get())
            try:
                import psutil
                phys  = psutil.cpu_count(logical=False) or 1
                logic = psutil.cpu_count(logical=True)  or phys
            except ImportError:
                logic = os.cpu_count() or 4
                phys  = max(1, logic // 2)

            if workers <= phys:
                color = GREEN
                note  = "✓ within physical cores"
            elif workers <= logic:
                color = GOLD
                note  = "⚠ above physical cores — decompression will compete for CPU"
            else:
                color = RED
                note  = "✗ exceeds logical CPU count — will thrash disk + CPU"

            msg = (f"  {workers} unzip{'s' if workers>1 else ''} / "
                   f"{phys} physical cores  ({logic} logical)  →  {note}")
            self._lbl_unzip_cpu.configure(text=msg, fg=color)
        except Exception:
            pass

    def _update_snap_ram_label(self):
        """Refresh the live RAM warning label under the SNAP workers slider."""
        if not hasattr(self, "_lbl_snap_ram"):
            return
        try:
            workers  = int(self.v_snap_workers.get())
            jvm_mb   = int(self.v_jvm_mb.get())
            needed   = workers * jvm_mb / 1024          # GB
            total    = getattr(self, "_snap_total_ram", 16.0)
            overhead = 4.0                               # OS + other
            safe     = needed <= (total - overhead)
            pct      = needed / total * 100 if total > 0 else 0
            color    = GREEN if safe else GOLD if pct < 90 else RED
            msg = (f"  ~{needed:.0f} GB JVM total  |  "
                   f"System: {total:.0f} GB  →  "
                   f"{'✓ safe' if safe else '⚠ tight — close other apps'}")
            self._lbl_snap_ram.configure(text=msg, fg=color)
        except Exception:
            pass

    def _start_gdal_log_tail(self):
        """Tail CPL_LOG into a hidden buffer — not shown in the UI panel,
        but appended to the exported .txt so it's available for debugging."""
        self._gdal_log_lines = []
        _offset = [0]

        # clear stale content from a previous session
        try:
            open(_GDAL_LOG, "w").close()
        except Exception:
            pass

        def _tail():
            while True:
                time.sleep(1)
                try:
                    with open(_GDAL_LOG, encoding="utf-8", errors="replace") as f:
                        f.seek(_offset[0])
                        chunk = f.read()
                        if chunk:
                            _offset[0] += len(chunk)
                            for line in chunk.splitlines():
                                line = line.strip()
                                if line:
                                    self._gdal_log_lines.append(line)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass

        threading.Thread(target=_tail, daemon=True).start()

    def _draw_banner(self, event=None):
        c = self._banner
        w = c.winfo_width() or 1200
        h = 80
        c.delete("all")
        steps = 200
        for i in range(steps):
            t = i / steps
            r = int(0xC6 + (0x0F - 0xC6) * t)
            g = int(0x28 + (0x0F - 0x28) * t)
            b = int(0x28 + (0x1F - 0x28) * t)
            x0 = int(w * i / steps); x1 = int(w * (i + 1) / steps) + 1
            c.create_rectangle(x0, 0, x1, h, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
        c.create_rectangle(0, h - 3, w, h, fill=ACCENT, outline="")
        c.create_text(24, 26, text="📡  SAR Foundry",
                      font=(_FONT_FAM, 21, "bold"), fill=WHITE, anchor="w")
        c.create_text(26, 56, text="Sentinel-1 GRD  ·  SNAP ARD processing  ·  COG Indices",
                      font=(_FONT_FAM, 9), fill=ACCENT_L, anchor="w")

    def _build(self):
        self.configure(bg=SURFACE)
        self._banner = tk.Canvas(self, height=80, highlightthickness=0, bd=0)
        self._banner.pack(fill=tk.X)
        self._banner.bind("<Configure>", self._draw_banner)
        self.after(50, self._draw_banner)

        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL,
                               bg=BG, sashwidth=5, sashrelief='flat',
                               bd=0, handlesize=0)
        paned.pack(fill=tk.BOTH, expand=True)
        left  = tk.Frame(paned, bg=BG)
        right = tk.Frame(paned, bg=SURFACE)
        paned.add(left,  minsize=700, width=820, stretch='never')
        paned.add(right, minsize=300, stretch='always')
        try:
            self._build_form(left)
        except Exception as _be:
            import traceback; traceback.print_exc()
        self._build_log(right)
        self.after(300, lambda: paned.sash_place(0, 820, 0))

    def _section(self, parent, text):
        outer = tk.Frame(parent, bg=BG2); outer.pack(fill=tk.X, padx=8, pady=(12, 2))
        tk.Frame(outer, bg=ACCENT, width=4).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(outer, text=f"  {text}", font=FONT_BOLD,
                 fg=ACCENT_L, bg=BG2, pady=7).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _btn(self, parent, text, cmd, color=None, **kw):
        c = color or ACCENT
        hover = {ACCENT: ACCENT2, RED: "#B71C1C", LOG_GREEN: "#2E7D32"}.get(c, ACCENT2)
        kw.setdefault("font", FONT_BOLD)
        # Mouse-click only: never take keyboard focus, else Tk auto-moves focus
        # to the STOP button when START is disabled at run start, and the next
        # Space/Enter fires STOP — a "stops by itself" phantom cancel.
        kw.setdefault("takefocus", 0)
        # macOS (Aqua) ignores a tk.Button 'bg', so white text on the default
        # light button is unreadable. There, colour the TEXT instead; elsewhere
        # keep the filled look with hover.
        if sys.platform == "darwin":
            b = tk.Button(parent, text=text, command=cmd,
                          fg=c, highlightbackground=c, relief="flat",
                          activeforeground=c, cursor="hand2", bd=0, **kw)
        else:
            b = tk.Button(parent, text=text, command=cmd,
                          bg=c, fg=WHITE, relief="flat",
                          activebackground=hover, activeforeground=WHITE,
                          cursor="hand2", bd=0, **kw)
            b.bind("<Enter>", lambda e: b.configure(bg=hover))
            b.bind("<Leave>", lambda e: b.configure(bg=c))
        return b

    def _entry(self, parent, var, **kw):
        return tk.Entry(parent, textvariable=var, font=FONT,
                        bg=BG2, fg=FG, insertbackground=FG,
                        relief="flat", bd=4, **kw)

    def _row(self, parent, label, widget_factory, **kw):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row, text=label, font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        w = widget_factory(row, **kw)
        w.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return w

    def _open_aoi_map(self):
        """Open an interactive Leaflet map in a native app window (via pywebview).
        Falls back to browser if pywebview is not available.
        pywebview is launched in a subprocess to avoid tkinter main-thread conflict."""
        import threading, json, tempfile, os, sys

        # ── Check / install pywebview ─────────────────────────────────────
        try:
            import webview as _wv  # noqa
            has_webview = True
        except ImportError:
            has_webview = False

        if not has_webview:
            ans = messagebox.askyesno(
                "Install pywebview?",
                "pywebview is needed to open the map in an app window.\n"
                "Install it now? (requires internet, ~5 MB)\n\n"
                "Click No to open the map in your browser instead.")
            if ans:
                self._log("Installing pywebview...")
                import subprocess as _sp
                r = _sp.run([sys.executable, "-m", "pip", "install", "pywebview",
                             "--quiet"], capture_output=True)
                if r.returncode == 0:
                    self._log("pywebview installed. Opening map...")
                    has_webview = True
                else:
                    self._log("Install failed — falling back to browser.")

        # ── Build the HTML map ────────────────────────────────────────────
        html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>SAR Foundry — Draw AOI</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css"/>
<style>
  body{margin:0;font-family:sans-serif;background:#1a1a1a}
  #map{height:85vh}
  #bar{display:flex;gap:10px;align-items:center;padding:8px 12px;background:#222;color:#eee}
  button{padding:7px 16px;border:none;border-radius:4px;cursor:pointer;font-size:13px;font-weight:bold}
  #btn-use{background:#4CAF50;color:#fff} #btn-clr{background:#e53935;color:#fff}
  #status{color:#aaa;font-size:12px}
  .leaflet-control-layers{background:#222!important;color:#eee!important;border:1px solid #444!important;border-radius:6px!important;padding:4px 8px!important}
  .leaflet-control-layers-list{color:#eee!important}
  .leaflet-control-layers label{color:#eee!important;font-size:12px!important}
  .leaflet-control-layers::before{content:"\2B9D  Basemap";display:block;font-size:11px;font-weight:bold;color:#aaa;margin-bottom:4px;border-bottom:1px solid #444;padding-bottom:4px}
</style></head><body>
<div id="bar">
  <button id="btn-use" onclick="sendAOI()">&#10003; Use this AOI</button>
  <button id="btn-clr" onclick="clearAll()">&#10005; Clear</button>
  <span id="status">Draw a polygon or rectangle on the map, then click Use this AOI</span>
</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
<script>
var map=L.map("map").setView([54.0,15.0],4);
var googleSat=L.tileLayer("https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
  {attribution:"&copy; Google",maxZoom:20});
var googleHybrid=L.tileLayer("https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
  {attribution:"&copy; Google",maxZoom:20});
var osm=L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  {attribution:"&copy; OpenStreetMap"});
googleHybrid.addTo(map);
L.control.layers({"Google Satellite":googleSat,"Google Hybrid":googleHybrid,
  "OpenStreetMap":osm},{},{position:"topright",collapsed:false}).addTo(map);
var drawn=new L.FeatureGroup(); map.addLayer(drawn);
map.addControl(new L.Control.Draw({
  edit:{featureGroup:drawn},
  draw:{polygon:true,rectangle:true,circle:false,marker:false,
        polyline:false,circlemarker:false}
}));
map.on(L.Draw.Event.CREATED,function(e){
  drawn.clearLayers(); drawn.addLayer(e.layer);
  st("Shape ready — click Use this AOI","#4CAF50");
});
function st(msg,col){var s=document.getElementById("status");s.textContent=msg;s.style.color=col||"#aaa"}
function sendAOI(){
  var layers=drawn.getLayers();
  if(!layers.length){st("No shape drawn yet!","#e53935");return;}
  var geo=JSON.stringify(layers[0].toGeoJSON());
  if(window.pywebview){
    window.pywebview.api.send_aoi(geo);
    st("AOI sent to SAR Foundry! You can close this window.","#4CAF50");
  } else {
    fetch("__FALLBACK_URL__",{method:"POST",
      headers:{"Content-Type":"application/json"},body:geo})
      .then(()=>st("AOI sent!","#4CAF50"))
      .catch(e=>st("Error: "+e,"#e53935"));
  }
}
function clearAll(){drawn.clearLayers();st("Cleared — draw a new shape.");}
</script></body></html>"""

        result = {"geojson": None}

        if has_webview:
            # ── pywebview path: native OS window ─────────────────────────
            # Must run in subprocess (webview.start() needs the main thread)
            import subprocess as _sp
            with tempfile.NamedTemporaryFile(mode="w", suffix="_aoi_out.json",
                                             delete=False, encoding="utf-8") as f:
                out_path = f.name
            script = (
                "import webview, json, sys\n"
                f"out_path = {out_path!r}\n"
                "result = {}\n"
                "class Api:\n"
                "  def send_aoi(self, data):\n"
                "    result['data'] = data\n"
                "    window.destroy()\n"
                "api = Api()\n"
                f"html = {html!r}\n"
                "window = webview.create_window('Draw AOI — SAR Foundry', html=html,\n"
                "  js_api=api, width=1000, height=700)\n"
                "webview.start()\n"
                "if 'data' in result:\n"
                "  open(out_path,'w').write(result['data'])\n"
            )
            def _run_webview():
                try:
                    _sp.run([sys.executable, "-c", script], timeout=600)
                    if os.path.isfile(out_path) and os.path.getsize(out_path) > 2:
                        with open(out_path, encoding="utf-8") as f:
                            geo = json.load(f)
                        fc = json.dumps({"type":"FeatureCollection","features":[geo]}, indent=2)
                        geo_path = _drawn_aoi_path()
                        with open(geo_path, "w", encoding="utf-8") as f:
                            f.write(fc)
                        os.remove(out_path)
                        # Tk vars are not thread-safe — set on the main thread,
                        # or the field silently keeps the previous AOI path
                        self.after(0, lambda p=geo_path: (
                            self.v_aoi.set(p),
                            self._log(f"AOI set from map: {p}")))
                except Exception as e:
                    self.after(0, lambda: self._log(f"Map window error: {e}"))
            threading.Thread(target=_run_webview, daemon=True).start()
            self._log("AOI map opening in app window...")

        else:
            # ── Browser fallback with local HTTP server ───────────────────
            import socket, webbrowser
            from http.server import HTTPServer, BaseHTTPRequestHandler
            _s = socket.socket(); _s.bind(("",0)); port = _s.getsockname()[1]; _s.close()
            fb_html = html.replace("__FALLBACK_URL__", f"http://localhost:{port}/aoi")

            class _H(BaseHTTPRequestHandler):
                def do_POST(self):
                    if self.path=="/aoi":
                        data=self.rfile.read(int(self.headers.get("Content-Length",0)))
                        result["geojson"]=json.loads(data)
                        self.send_response(200)
                        self.send_header("Access-Control-Allow-Origin","*")
                        self.end_headers(); self.wfile.write(b"OK")
                        threading.Thread(target=self.server.shutdown,daemon=True).start()
                def do_OPTIONS(self):
                    self.send_response(204)
                    for h,v in [("Access-Control-Allow-Origin","*"),
                                 ("Access-Control-Allow-Methods","POST, OPTIONS"),
                                 ("Access-Control-Allow-Headers","Content-Type")]:
                        self.send_header(h,v)
                    self.end_headers()
                def log_message(self,*a): pass

            with tempfile.NamedTemporaryFile(mode="w",suffix="_aoi_map.html",
                                              delete=False,encoding="utf-8") as f:
                f.write(fb_html); html_path=f.name
            server=HTTPServer(("localhost",port),_H)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            webbrowser.open(f"file:///{html_path.replace(os.sep,'/')}")
            self._log("AOI map opened in browser (install pywebview for in-app window)")

            def _wait():
                import time
                try:
                    for _ in range(120):          # ~2 min, then give up (S5)
                        if result["geojson"]: break
                        time.sleep(1)
                    if not result["geojson"]:
                        self.after(0, lambda: self._log(
                            "Map closed with no AOI selected (timed out)."))
                        return
                    fc=json.dumps({"type":"FeatureCollection","features":[result["geojson"]]},indent=2)
                    geo_path = _drawn_aoi_path()
                    with open(geo_path, "w", encoding="utf-8") as f:
                        f.write(fc)
                    # Tk vars are not thread-safe — set on the main thread
                    self.after(0, lambda p=geo_path: (
                        self.v_aoi.set(p),
                        self._log(f"AOI set from map: {p}")))
                finally:
                    # always free the HTTP server + its thread/socket (S5 leak)
                    try: server.shutdown()
                    except Exception: pass
                    try: server.server_close()
                    except Exception: pass
            threading.Thread(target=_wait,daemon=True).start()


    def _browse_file(self, var, types):
        path = filedialog.askopenfilename(filetypes=types)
        if path:
            var.set(path)

    def _browse_dir(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    # ─────────────────────────────────────────────────────────────────────
    def _make_tab_scroll(self, parent):
        """Scrollable frame inside a tab with independent mouse-wheel binding."""
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg=BG)
        frame.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e, w=win_id: canvas.itemconfig(w, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        def _scroll(event, c=canvas):
            c.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind("<Enter>", lambda e, c=canvas, f=_scroll: c.bind_all("<MouseWheel>", f))
        canvas.bind("<Leave>", lambda e, c=canvas: c.unbind_all("<MouseWheel>"))
        return frame

    def _build_form(self, parent):
        # ── Notebook style ────────────────────────────────────────────────
        style = ttk.Style()
        style.configure("App.TNotebook", background=BG, borderwidth=0,
                         tabmargins=[0,0,0,0])
        style.configure("App.TNotebook.Tab",
                         background=BG2, foreground=FG2,
                         padding=[16, 8], font=(_FONT_FAM, 10, "bold"))
        style.map("App.TNotebook.Tab",
                  background=[("selected", BG), ("active", SURFACE)],
                  foreground=[("selected", ACCENT_L), ("active", FG)])

        nb = ttk.Notebook(parent, style="App.TNotebook")
        nb.pack(fill=tk.BOTH, expand=True)

        t_dl   = tk.Frame(nb, bg=BG)
        t_proc = tk.Frame(nb, bg=BG)
        t_out  = tk.Frame(nb, bg=BG)
        t_calc = tk.Frame(nb, bg=BG)
        t_batch = tk.Frame(nb, bg=BG)
        nb.add(t_dl,   text="  ⬇  Download  ")
        nb.add(t_proc, text="  ⚙  Processing  ")
        nb.add(t_out,  text="  📁  Output  ")
        nb.add(t_batch, text="  🗂  Batch  ")
        nb.add(t_calc, text="  🧮  Raster Calc  ")

        # ── DOWNLOAD TAB ─────────────────────────────────────────────────
        p = self._make_tab_scroll(t_dl)

        # ── S1 Source ────────────────────────────────────────────────────
        self._section(p, "1. Sentinel-1 Source")
        self.v_safe_source = tk.StringVar(value="auto")
        src_fr = tk.Frame(p, bg=BG); src_fr.pack(padx=10, anchor="w")
        rb_auto = tk.Radiobutton(src_fr, text="Download — auto  (use ASF or CDSE, whichever is"
                                              " configured; fall back if one fails)",
                               variable=self.v_safe_source, value="auto",
                               bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                               activebackground=BG, activeforeground=ACCENT_L,
                               command=self._on_source_change)
        rb_dl = tk.Radiobutton(src_fr, text="Download from ASF only  (NASA Earthdata)",
                               variable=self.v_safe_source, value="download",
                               bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                               activebackground=BG, activeforeground=ACCENT_L,
                               command=self._on_source_change)
        rb_cdse = tk.Radiobutton(src_fr, text="Download from Copernicus CDSE only",
                               variable=self.v_safe_source, value="cdse",
                               bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                               activebackground=BG, activeforeground=ACCENT_L,
                               command=self._on_source_change)
        rb_cdse_s3 = tk.Radiobutton(src_fr, text="Download from CDSE S3  (fastest, no unzip; needs S3 keys — section 6c)",
                               variable=self.v_safe_source, value="cdse_s3",
                               bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                               activebackground=BG, activeforeground=ACCENT_L,
                               command=self._on_source_change)
        rb_ex_zip = tk.Radiobutton(src_fr, text="Use existing .zip folder  (skip download; unzip → SNAP → indices)",
                               variable=self.v_safe_source, value="existing_zip",
                               bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                               activebackground=BG, activeforeground=ACCENT_L,
                               command=self._on_source_change)
        rb_ex = tk.Radiobutton(src_fr, text="Use existing .SAFE folder  (skip download + unzip → SNAP → indices)",
                               variable=self.v_safe_source, value="existing",
                               bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                               activebackground=BG, activeforeground=ACCENT_L,
                               command=self._on_source_change)
        rb_ex_snap = tk.Radiobutton(src_fr, text="Use existing GeoTIFF folder  (skip download + SNAP)",
                               variable=self.v_safe_source, value="existing_snap",
                               bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                               activebackground=BG, activeforeground=ACCENT_L,
                               command=self._on_source_change)
        rb_auto.pack(anchor="w")
        rb_dl.pack(anchor="w")
        rb_cdse.pack(anchor="w")
        rb_cdse_s3.pack(anchor="w")
        rb_ex_zip.pack(anchor="w")
        rb_ex.pack(anchor="w")
        rb_ex_snap.pack(anchor="w")

        # existing .SAFE folder picker (shown only when "existing" selected)
        self.existing_fr = tk.Frame(p, bg=BG2, bd=1, relief="flat")
        row_ex = tk.Frame(self.existing_fr, bg=BG2)
        row_ex.pack(fill=tk.X, padx=8, pady=6)
        self._existing_lbl = tk.Label(row_ex, text="Existing .SAFE folder:", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT_L)
        self._existing_lbl.pack(side=tk.LEFT)
        self.v_existing_safe = tk.StringVar(value="")
        tk.Entry(row_ex, textvariable=self.v_existing_safe,
                 font=FONT).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6,0))
        self._btn(row_ex, "Browse", lambda: self._browse_dir(self.v_existing_safe)
                  ).pack(side=tk.LEFT, padx=(4,0))
        self._existing_help = tk.Label(self.existing_fr,
                 text="  Download step will be skipped automatically.",
                 font=(_FONT_FAM,9,"italic"), bg=BG2, fg=ACCENT_L)
        self._existing_help.pack(anchor="w", padx=8, pady=(0,4))
        self.existing_fr.pack_forget()

        # existing GeoTIFF folder picker (shown only when "existing_snap" selected)
        self.existing_snap_fr = tk.Frame(p, bg=BG2, bd=1, relief="flat")
        row_exs = tk.Frame(self.existing_snap_fr, bg=BG2)
        row_exs.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(row_exs, text="Existing GeoTIFF folder:", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT_L).pack(side=tk.LEFT)
        self.v_existing_snap = tk.StringVar(value=self._saved_cfg.get("snap_dir", ""))
        tk.Entry(row_exs, textvariable=self.v_existing_snap,
                 font=FONT).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6,0))
        self._btn(row_exs, "Browse", lambda: self._browse_dir(self.v_existing_snap)
                  ).pack(side=tk.LEFT, padx=(4,0))
        tk.Label(self.existing_snap_fr,
                 text="  Download + SNAP steps skipped. Goes straight to step 3: compute indices.",
                 font=(_FONT_FAM,9,"italic"), bg=BG2, fg=ACCENT_L).pack(anchor="w", padx=8, pady=(0,4))
        self.existing_snap_fr.pack_forget()

        # ── AOI ──────────────────────────────────────────────────────────
        self._section(p, "3. Area of Interest")
        self.v_aoi = tk.StringVar(value=self._saved_cfg.get("aoi_path", ""))
        row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row, text="AOI file", font=FONT, bg=BG, fg=FG, width=18, anchor="w").pack(side=tk.LEFT)
        self._entry(row, self.v_aoi).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row, "Browse", lambda: self._browse_file(self.v_aoi,
                    [("Spatial files","*.shp *.gpkg *.geojson"),("All","*")]
                  )).pack(side=tk.LEFT, padx=(4,0))
        self._btn(row, "🗺 Draw on map", self._open_aoi_map,
                  color=BG2).pack(side=tk.LEFT, padx=(4,0))

        tk.Label(p, text="  Accepted: .shp (Shapefile)  ·  .gpkg (GeoPackage)  ·  .geojson — any CRS, auto-reprojected",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,2))

        # ── Scattered fields (AOI clustering + field mask) ────────────────
        # Region AOI (section 3) is used for the download footprint. An optional
        # per-field file restricts SNAP to tight clusters around the fields and
        # masks the output to the field shapes (nodata outside) to save storage.
        self._section(p, "3b. Scattered fields")
        self.v_cluster_aoi = tk.BooleanVar(
            value=self._saved_cfg.get("cluster_aoi", False))
        self.v_cluster_gap = tk.StringVar(
            value=str(self._saved_cfg.get("cluster_gap_km", 5.0)))
        self.v_fields_path = tk.StringVar(
            value=self._saved_cfg.get("fields_path", ""))
        _cl_row = tk.Frame(p, bg=BG); _cl_row.pack(fill=tk.X, padx=14, pady=(2,0))
        tk.Checkbutton(_cl_row,
                       text="Process scattered field polygons as separate clusters",
                       variable=self.v_cluster_aoi, bg=BG, font=FONT_BOLD, fg=FG,
                       selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L
                       ).pack(side=tk.LEFT)
        _ff_row = tk.Frame(p, bg=BG); _ff_row.pack(fill=tk.X, padx=14, pady=(4,0))
        tk.Label(_ff_row, text="Fields file (optional)", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        self._entry(_ff_row, self.v_fields_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(_ff_row, "Browse", lambda: self._browse_file(self.v_fields_path,
                    [("Spatial files","*.shp *.gpkg *.geojson"),("All","*")]
                  )).pack(side=tk.LEFT, padx=(4,0))
        tk.Label(p, text="  Per-field polygons to extract (e.g. fields_unique.geojson). "
                         "Leave empty to use the AOI polygons.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,2))
        _gap_row = tk.Frame(p, bg=BG); _gap_row.pack(fill=tk.X, padx=14, pady=(2,0))
        tk.Label(_gap_row, text="    Merge fields within", font=FONT, bg=BG, fg=FG2
                 ).pack(side=tk.LEFT)
        tk.Entry(_gap_row, textvariable=self.v_cluster_gap, width=5, font=FONT,
                 bg=BG2, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(side=tk.LEFT, padx=(4,4))
        tk.Label(_gap_row, text="km into one cluster", font=FONT, bg=BG, fg=FG2
                 ).pack(side=tk.LEFT)
        tk.Label(p,
                 text="  For AOIs with many fields far apart: each cluster is cropped to its own\n"
                      "  tight bounding box, so SNAP skips the empty space between fields (less\n"
                      "  compute & storage). Compact AOIs are unaffected. If left off, the pipeline\n"
                      "  still suggests this when it detects a very scattered AOI.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2, justify=tk.LEFT
                 ).pack(anchor="w", padx=14, pady=(2,2))

        # ── Dates ─────────────────────────────────────────────────────────
        self._section(p, "4. Date Range")
        self.v_start = tk.StringVar(value=self._saved_cfg.get("start_date", "2023-08-01"))
        self.v_end   = tk.StringVar(value=self._saved_cfg.get("end_date",   "2023-12-31"))

        for label, var in [("Start date", self.v_start), ("End date", self.v_end)]:
            row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=3)
            tk.Label(row, text=label, font=FONT, bg=BG, fg=FG, width=18, anchor="w").pack(side=tk.LEFT)
            entry = tk.Entry(row, textvariable=var, font=FONT, width=13)
            entry.pack(side=tk.LEFT)
            if HAS_CALENDAR:
                def _open_cal(v=var):
                    self._show_calendar_popup(v)
                self._btn(row, "📅", _open_cal, padx=4, font=(_FONT_FAM,11)
                          ).pack(side=tk.LEFT, padx=(3,0))
            else:
                tk.Label(row, text="  YYYY-MM-DD", font=(_FONT_FAM,8),
                         bg=BG, fg=FG2).pack(side=tk.LEFT)

        # ── Orbit ─────────────────────────────────────────────────────────
        self._section(p, "5. Orbit Direction")
        self.v_orbit = tk.StringVar(value="Both")
        orb_fr = tk.Frame(p, bg=BG); orb_fr.pack(padx=14, anchor="w")
        for txt in ["Both", "ASC", "DSC"]:
            tk.Radiobutton(orb_fr, text=txt, variable=self.v_orbit, value=txt,
                           bg=BG, font=FONT, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L).pack(side=tk.LEFT, padx=6)

        # ── Satellites ─────────────────────────────────────────────────────
        self._section(p, "5b. Satellites")
        _saved_sats = set(self._saved_cfg.get("satellites", ["S1A", "S1B", "S1C"]))
        self.v_sat = {s: tk.BooleanVar(value=(s in _saved_sats)) for s in ("S1A", "S1B", "S1C")}
        sat_fr = tk.Frame(p, bg=BG); sat_fr.pack(padx=14, anchor="w")
        for s in ("S1A", "S1B", "S1C"):
            tk.Checkbutton(sat_fr, text=s, variable=self.v_sat[s], bg=BG, font=FONT, fg=FG,
                           selectcolor=BG2, activebackground=BG,
                           activeforeground=ACCENT_L).pack(side=tk.LEFT, padx=6)
        tk.Label(p, text="  Pick which satellites to fetch. Tip: select only S1C to backfill AOIs downloaded\n"
                         "  before S1C existed. All sources now return S1C (asf_search ≥ 8.1.3); CDSE / CDSE S3\n"
                         "  tend to be slightly more complete and faster for it.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2, justify="left").pack(anchor="w", padx=14, pady=(2, 0))

        # ── Credentials ───────────────────────────────────────────────────
        self._section(p, "6. ASF Credentials  (NASA Earthdata)")
        cfg_s = self._saved_cfg
        self.v_token  = tk.StringVar(value=cfg_s.get("asf_token", ""))
        self.v_remember = tk.BooleanVar(value=cfg_s.get("remember_creds", False))

        tk.Label(p, text="  Earthdata Bearer token (or set up a ~/.netrc entry) —"
                        " username/password is no longer supported",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(6,0))
        row_tok = tk.Frame(p, bg=BG); row_tok.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row_tok, text="Token", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_tok, textvariable=self.v_token, font=FONT, show="*",
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        def _open_tokens():
            import webbrowser
            webbrowser.open("https://urs.earthdata.nasa.gov/documentation/for_users/user_token")
        tk.Button(row_tok, text="Get token →", font=(_FONT_FAM,8,"underline"),
                  bg=BG, fg=ACCENT, relief="flat", cursor="hand2",
                  command=_open_tokens).pack(side=tk.LEFT, padx=(6,0))

        reg_fr = tk.Frame(p, bg=BG); reg_fr.pack(fill=tk.X, padx=14, pady=(2,4))
        tk.Label(reg_fr, text="No account?", font=(_FONT_FAM,9), bg=BG, fg=FG2).pack(side=tk.LEFT)
        def _open_register():
            import webbrowser
            webbrowser.open("https://urs.earthdata.nasa.gov/users/new")
        tk.Button(reg_fr, text="Register free at NASA Earthdata →",
                  font=(_FONT_FAM,9,"underline"), bg=BG, fg=ACCENT,
                  relief="flat", cursor="hand2",
                  command=_open_register).pack(side=tk.LEFT, padx=(4,0))

        rem_fr = tk.Frame(p, bg=BG); rem_fr.pack(fill=tk.X, padx=14, pady=(0,2))
        tk.Checkbutton(rem_fr, text="Remember ASF token + CDSE login on this computer",
                       variable=self.v_remember, bg=BG,
                       font=(_FONT_FAM,9), fg=FG2, selectcolor=BG2,
                       activebackground=BG, activeforeground=ACCENT_L,
                       command=self._on_remember_change).pack(anchor="w")
        tk.Label(rem_fr, text="  (revocable tokens only, stored in sar_foundry_config.json — plain text)",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w")

        # ── Copernicus CDSE Credentials ───────────────────────────────────
        self._section(p, "6b. Copernicus CDSE Credentials")
        cfg_s2 = self._saved_cfg
        self.v_cdse_user = tk.StringVar(value=cfg_s2.get("cdse_user", ""))
        self.v_cdse_pass = tk.StringVar(value=cfg_s2.get("cdse_pass", ""))

        tk.Label(p, text="  Login with your Copernicus Data Space Ecosystem username/password."
                         " Won't work for MFA-protected accounts (Keycloak limitation) — use"
                         " ASF instead in that case.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(6,0))
        row_cu = tk.Frame(p, bg=BG); row_cu.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row_cu, text="CDSE username", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_cu, textvariable=self.v_cdse_user, font=FONT,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row_cp = tk.Frame(p, bg=BG); row_cp.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row_cp, text="CDSE password", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_cp, textvariable=self.v_cdse_pass, font=FONT, show="*",
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(p, text="  (saved to sar_foundry_config.json in plain text only if"
                         " 'Remember' is ticked below — otherwise re-enter each session)",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,4))

        cdse_reg_fr = tk.Frame(p, bg=BG); cdse_reg_fr.pack(fill=tk.X, padx=14, pady=(2,4))
        tk.Label(cdse_reg_fr, text="No account?", font=(_FONT_FAM,9), bg=BG, fg=FG2
                 ).pack(side=tk.LEFT)
        def _open_cdse():
            import webbrowser
            webbrowser.open("https://dataspace.copernicus.eu/")
        tk.Button(cdse_reg_fr, text="Register free at Copernicus Data Space →",
                  font=(_FONT_FAM,9,"underline"), bg=BG, fg=ACCENT,
                  relief="flat", cursor="hand2",
                  command=_open_cdse).pack(side=tk.LEFT, padx=(4,0))
        cdse_rem_fr = tk.Frame(p, bg=BG); cdse_rem_fr.pack(fill=tk.X, padx=14, pady=(0,4))
        tk.Checkbutton(cdse_rem_fr, text="Remember Copernicus username on this computer",
                       variable=self.v_remember, bg=BG, font=(_FONT_FAM,9), fg=FG2,
                       selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L,
                       command=self._on_remember_change).pack(anchor="w")
        tk.Label(cdse_rem_fr,
                 text="  (username + password stored in sar_foundry_config.json — plain text)",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w")

        # ── CDSE S3 keys (fastest download) ───────────────────────────────
        self._section(p, "6c. CDSE S3 Keys  (fastest download)")
        self.v_s3_access = tk.StringVar(value=cfg_s2.get("s3_access", ""))
        self.v_s3_secret = tk.StringVar(value=cfg_s2.get("s3_secret", ""))
        try:    _s3w = int(cfg_s2.get("s3_workers", 8))
        except Exception: _s3w = 8
        self.v_s3_workers = tk.IntVar(value=max(1, min(_s3w, 16)))
        tk.Label(p, text="  Direct S3 download from the CDSE object store — bypasses the OData"
                         " throttle and needs no unzip.\n  Capped at ~20 MB/s (160 Mbit/s) and"
                         " 12 TB/month per key. Uses the same AOI/dates/orbit as CDSE.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(6,0))
        row_sa = tk.Frame(p, bg=BG); row_sa.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row_sa, text="S3 access key", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_sa, textvariable=self.v_s3_access, font=FONT,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row_ss = tk.Frame(p, bg=BG); row_ss.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row_ss, text="S3 secret key", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_ss, textvariable=self.v_s3_secret, font=FONT, show="*",
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        s3_reg_fr = tk.Frame(p, bg=BG); s3_reg_fr.pack(fill=tk.X, padx=14, pady=(2,4))
        tk.Label(s3_reg_fr, text="No keys?", font=(_FONT_FAM,9), bg=BG, fg=FG2).pack(side=tk.LEFT)
        def _open_s3keys():
            import webbrowser
            webbrowser.open("https://eodata-s3keysmanager.dataspace.copernicus.eu/")
        tk.Button(s3_reg_fr, text="Generate S3 keys at CDSE →",
                  font=(_FONT_FAM,9,"underline"), bg=BG, fg=ACCENT,
                  relief="flat", cursor="hand2",
                  command=_open_s3keys).pack(side=tk.LEFT, padx=(4,0))
        tk.Label(p, text="  (stored in sar_foundry_config.json — plain text, only if 'Remember' is ticked)",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,4))
        row_s3w = tk.Frame(p, bg=BG); row_s3w.pack(fill=tk.X, padx=14, pady=(2,0))
        tk.Label(row_s3w, text="Parallel files / scene", font=FONT, bg=BG, fg=FG,
                 width=22, anchor="w").pack(side=tk.LEFT)
        tk.Spinbox(row_s3w, from_=1, to=16, width=4, textvariable=self.v_s3_workers,
                   font=FONT, bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                   justify="center").pack(side=tk.LEFT)
        tk.Label(p, text="  How many files within each .SAFE download at once (S3 only). More is not"
                         " always faster — the 20 MB/s per-key cap means ~8 usually saturates it;"
                         " lower it if the link feels congested.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,4))

        # ── Parallel download ─────────────────────────────────────────────
        self._section(p, "7. Parallel Downloads (CDSE & CDSE S3)")
        self.v_dl_workers = tk.IntVar(
            value=max(1, min(int(self._saved_cfg.get("max_dl_workers", 1)), 5)))
        row_dw = tk.Frame(p, bg=BG); row_dw.pack(fill=tk.X, padx=14, pady=(4, 0))
        tk.Label(row_dw, text="Simultaneous downloads", font=FONT, bg=BG, fg=FG,
                 width=22, anchor="w").pack(side=tk.LEFT)
        tk.Spinbox(row_dw, from_=1, to=5, width=4, textvariable=self.v_dl_workers,
                   font=FONT, bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                   justify="center").pack(side=tk.LEFT)
        tk.Label(p, text="  How many scenes download at once. CDSE object storage handles 3–5 safely.\n"
                         "  For CDSE S3 this stacks with 'Parallel files / scene' (6c) toward the 20 MB/s\n"
                         "  per-key cap — a single scene rarely saturates it, so 3–5 scenes helps.\n"
                         "  Ignored for ASF: it throttles parallel connections and corrupts VV bands,\n"
                         "  so ASF always downloads sequentially (1 at a time) regardless of this setting.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(2, 4))

        # ── PROCESSING TAB ───────────────────────────────────────────────
        p = self._make_tab_scroll(t_proc)

        # ── Preprocessing Graph ──────────────────────────────────────────
        self._section(p, "2. Preprocessing Graph")
        self.v_graph_preset = tk.StringVar(
            value=self._saved_cfg.get("graph_preset", "sigma0"))
        self.v_graph = tk.StringVar(
            value=self._saved_cfg.get("graph_custom", ""))

        _presets = [
            ("sigma0",
             "σ⁰ Standard  (Filipponi 2019 — no Terrain-Flattening)",
             "Orbit → TNR → GBN → Cal(σ⁰) → [Speckle] → TC  |  flat terrain, simpler, matches most literature"),
            ("gamma0",
             "γ⁰ RTC  (Small 2011 — with Terrain-Flattening)",
             "Orbit → TNR → GBN → Cal(β⁰) → [Speckle] → TF → TC  |  more rigorous, mountainous or mixed terrain"),
            ("custom",
             "Custom XML  (upload your own SNAP graph)",
             ""),
        ]
        for _val, _lbl, _desc in _presets:
            _rf = tk.Frame(p, bg=BG); _rf.pack(fill=tk.X, padx=14, pady=(3,0))
            tk.Radiobutton(_rf, text=_lbl, variable=self.v_graph_preset, value=_val,
                           bg=BG, font=FONT_BOLD, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L,
                           command=self._on_graph_preset_change).pack(anchor="w")
            if _desc:
                tk.Label(_rf, text=f"    {_desc}", font=(_FONT_FAM, 8),
                         bg=BG, fg=FG2).pack(anchor="w")
            if _val == "custom":
                self.custom_graph_fr = tk.Frame(_rf, bg=BG2)
                _cg_row = tk.Frame(self.custom_graph_fr, bg=BG2)
                _cg_row.pack(fill=tk.X, padx=8, pady=6)
                tk.Label(_cg_row, text="Graph XML:", font=FONT_BOLD,
                         bg=BG2, fg=ACCENT_L).pack(side=tk.LEFT)
                tk.Entry(_cg_row, textvariable=self.v_graph,
                         font=FONT, bg=BG, fg=FG, insertbackground=FG,
                         relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6,0))
                self._btn(_cg_row, "Browse",
                          lambda: self._browse_file(self.v_graph,
                              [("XML files","*.xml"),("All","*")])
                          ).pack(side=tk.LEFT, padx=(4,0))
                tk.Label(self.custom_graph_fr,
                         text="  Note: the pipeline patches Read/Write/Subset nodes automatically.",
                         font=(_FONT_FAM,8,"italic"), bg=BG2, fg=FG2).pack(anchor="w", padx=8, pady=(0,4))
                if self.v_graph_preset.get() == "custom":
                    self.custom_graph_fr.pack(fill=tk.X, padx=4, pady=(2,4))
                else:
                    self.custom_graph_fr.pack_forget()

        # ── Speckle Filter ───────────────────────────────────────────────
        self._section(p, "2b. Speckle Filter")
        self.v_speckle = tk.StringVar(value=self._saved_cfg.get("speckle", "lee"))
        self._speckle_custom_params = self._saved_cfg.get("speckle_params", None)
        _spk_opts = [
            ("lee",    "Lee Sigma 7x7  (sigma=0.9, target 3x3)"),
            ("gamma",  "Gamma Map 7x7  (Bayesian, edge-preserving)"),
            ("none",   "None  (skip speckle filtering)"),
            ("custom", "Custom"),
        ]
        for _sval, _slbl in _spk_opts:
            _srow = tk.Frame(p, bg=BG); _srow.pack(fill=tk.X, padx=14, pady=(2,0))
            tk.Radiobutton(_srow, text=_slbl, variable=self.v_speckle, value=_sval,
                           bg=BG, font=FONT, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L).pack(side=tk.LEFT)
            if _sval == "custom":
                self._btn(_srow, "Configure", self._open_speckle_dialog,
                          color=BG2, font=(_FONT_FAM, 9)).pack(side=tk.LEFT, padx=(8,0))
                self._lbl_speckle_custom = tk.Label(
                    _srow, text=self._speckle_custom_summary(),
                    font=(_FONT_FAM, 8, "italic"), bg=BG, fg=FG2)
                self._lbl_speckle_custom.pack(side=tk.LEFT, padx=(6,0))

        # ── DEM ───────────────────────────────────────────────────────────
        self._section(p, "2c. DEM for Terrain Correction")
        _dem_fr = tk.Frame(p, bg=BG); _dem_fr.pack(fill=tk.X, padx=14, pady=(4, 2))
        tk.Label(_dem_fr, text="DEM:", font=FONT_BOLD, bg=BG, fg=FG,
                 width=6, anchor="w").pack(side=tk.LEFT)
        self.v_dem = tk.StringVar(value=self._saved_cfg.get("dem_name", SNAP_DEM_DEFAULT))
        _dem_labels  = list(SNAP_DEMS.values())
        _dem_key_of  = {v: k for k, v in SNAP_DEMS.items()}
        from tkinter import ttk as _ttkD
        _dem_cb = _ttkD.Combobox(_dem_fr, values=_dem_labels,
                                  state="readonly", width=36, font=FONT)
        _dem_cb.set(SNAP_DEMS.get(self.v_dem.get(), _dem_labels[0]))
        def _on_dem_sel(event=None, _cb=_dem_cb):
            self.v_dem.set(_dem_key_of.get(_cb.get(), SNAP_DEM_DEFAULT))
        _dem_cb.bind("<<ComboboxSelected>>", _on_dem_sel)
        _dem_cb.pack(side=tk.LEFT, padx=(6, 0))
        self._lbl_dem_net = tk.Label(_dem_fr, text="", font=(_FONT_FAM, 8), bg=BG, fg=FG2)
        self._lbl_dem_net.pack(side=tk.LEFT, padx=(10, 0))
        def _chk_net():
            ok = _check_internet()
            # ponytail: marshal Tk update back to main loop; worker thread can't touch widgets
            def _apply(_lbl=self._lbl_dem_net):
                if _lbl.winfo_exists():
                    _lbl.configure(
                        text="online" if ok else "offline - use cached DEM only",
                        fg="#69F0AE" if ok else RED)
            self._lbl_dem_net.after(0, _apply)
        threading.Thread(target=_chk_net, daemon=True).start()
        tk.Label(p,
                 text="  DEM auto-downloaded by SNAP on first use and cached locally.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0, 2))

        # ── Multi-tile scenes ─────────────────────────────────────────────────
        self._section(p, "2d. Multi-tile scenes")
        # v_mosaic_method kept for cfg compatibility; hardcoded to 'both' (interpolation)
        self.v_mosaic_method = tk.StringVar(value="both")
        tk.Label(p,
                 text="  When two adjacent slices are detected for the same date and orbit,\n"
                      "  seam interpolation is applied automatically (TC edge trim + vertical\n"
                      "  bilinear fill). S1-SliceAssembly (ESA recommended) is in development.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2, justify=tk.LEFT
                 ).pack(anchor="w", padx=14, pady=(2, 6))

        # SliceAssembly is kept in code for future development but not exposed in UI.
        # To enable: set use_slice_assembly=True in cfg manually.

        # ── Parallel unzip ────────────────────────────────────────────────
        self._section(p, "2e. Parallel Unzip  (step 1b)")
        self.v_unzip_workers = tk.IntVar(
            value=max(1, min(int(self._saved_cfg.get("unzip_workers", 3)), 8)))
        row_uw = tk.Frame(p, bg=BG); row_uw.pack(fill=tk.X, padx=14, pady=(4, 0))
        tk.Label(row_uw, text="Simultaneous unzips", font=FONT, bg=BG, fg=FG,
                 width=22, anchor="w").pack(side=tk.LEFT)
        tk.Spinbox(row_uw, from_=1, to=8, width=4, textvariable=self.v_unzip_workers,
                   font=FONT, bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                   justify="center",
                   command=lambda: self._update_unzip_cpu_label()).pack(side=tk.LEFT)
        _extk, _extp = _find_fast_extractor()
        _exlbl = {"7z": f"7-Zip detected ({_extp})", "tar": f"bsdtar detected ({_extp})",
                  "zipfile": "No 7-Zip/bsdtar found — using Python zipfile (slower; install 7-Zip to speed up)"}[_extk]
        tk.Label(p, text="  Extraction is I/O-bound, so unzipping several archives at once is much faster.\n"
                         f"  Extractor: {_exlbl}",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(2, 0))
        # live PC-load warning, same idea as the SNAP/index worker labels
        self._lbl_unzip_cpu = tk.Label(p, text="", font=(_FONT_FAM, 8),
                                       bg=BG, anchor="w")
        self._lbl_unzip_cpu.pack(anchor="w", padx=14, pady=(0, 4))
        self.v_unzip_workers.trace_add(
            "write", lambda *_: self._update_unzip_cpu_label())
        self._update_unzip_cpu_label()

        # Extract on a fast internal scratch, then move the finished .SAFE to the
        # output drive. Big win when output is an external/slow drive: exploding
        # thousands of tiny .SAFE files onto USB is latency-bound; internal NVMe
        # eats the small-file IOPS, then one bulk robocopy move goes to USB.
        # Self-disables when output is already on the same volume as the scratch.
        self.v_unzip_stage = tk.BooleanVar(
            value=(str(self._saved_cfg.get("unzip_stage_dir", "auto")).strip().lower()
                   not in ("", "off")))
        row_us = tk.Frame(p, bg=BG); row_us.pack(fill=tk.X, padx=14, pady=(2, 4))
        tk.Checkbutton(
            row_us, variable=self.v_unzip_stage,
            text="Extract on internal SSD scratch, then move  (faster when output is an external/slow drive)",
            font=(_FONT_FAM, 8), bg=BG, fg=FG2, selectcolor=BG2,
            activebackground=BG, anchor="w").pack(side=tk.LEFT)

        # ── Parallel SNAP jobs ────────────────────────────────────────────
        self._section(p, "2f. Parallel SNAP Jobs")
        _total_ram = _get_total_ram_gb()
        _default_jvm = self._saved_cfg.get("jvm_mb", 10240)
        _default_workers = self._saved_cfg.get(
            "max_snap_workers", _safe_snap_workers(_default_jvm))

        # JVM heap per job
        jvm_row = tk.Frame(p, bg=BG); jvm_row.pack(fill=tk.X, padx=14, pady=(6, 2))
        tk.Label(jvm_row, text="JVM heap / job:", font=FONT, bg=BG, fg=FG,
                 width=16, anchor="w").pack(side=tk.LEFT)
        self.v_jvm_mb = tk.IntVar(value=_default_jvm)
        self.lbl_jvm = tk.Label(jvm_row,
                                 text=f"{_default_jvm // 1024} GB",
                                 font=FONT_BOLD, bg=BG, fg=ACCENT_L, width=6)
        self.lbl_jvm.pack(side=tk.RIGHT)
        ttk.Scale(jvm_row, from_=4096, to=16384, variable=self.v_jvm_mb,
                  orient=tk.HORIZONTAL,
                  command=lambda v: (
                      self.lbl_jvm.configure(text=f"{int(float(v))//1024} GB"),
                      self._update_snap_ram_label())
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Parallel jobs slider
        snap_wk_row = tk.Frame(p, bg=BG); snap_wk_row.pack(fill=tk.X, padx=14, pady=(4, 2))
        tk.Label(snap_wk_row, text="Parallel jobs:", font=FONT, bg=BG, fg=FG,
                 width=16, anchor="w").pack(side=tk.LEFT)
        self.v_snap_workers = tk.IntVar(value=_default_workers)
        self.lbl_snap_workers = tk.Label(snap_wk_row,
                                          text=f"{_default_workers} job{'s' if _default_workers>1 else ''}",
                                          font=FONT_BOLD, bg=BG, fg=ACCENT_L, width=8)
        self.lbl_snap_workers.pack(side=tk.RIGHT)
        ttk.Scale(snap_wk_row, from_=1, to=4, variable=self.v_snap_workers,
                  orient=tk.HORIZONTAL,
                  command=lambda v: (
                      self.lbl_snap_workers.configure(
                          text=f"{int(float(v))} job{'s' if int(float(v))>1 else ''}"),
                      self._update_snap_ram_label())
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Live RAM estimate label
        self._lbl_snap_ram = tk.Label(p, text="", font=(_FONT_FAM, 8),
                                       bg=BG, anchor="w")
        self._lbl_snap_ram.pack(anchor="w", padx=14, pady=(0, 6))
        self._snap_total_ram = _total_ram
        self._update_snap_ram_label()

        # ── Parallel index workers (step 3) ───────────────────────────────
        self._section(p, "2g. Parallel Index Workers  (step 3)")
        idx_wk_fr = tk.Frame(p, bg=BG); idx_wk_fr.pack(fill=tk.X, padx=14, pady=6)
        self.v_idx_workers = tk.IntVar(
            value=self._saved_cfg.get("max_idx_workers", 4))
        self.lbl_idx_workers = tk.Label(
            idx_wk_fr,
            text=f"{self.v_idx_workers.get()} thread{'s' if self.v_idx_workers.get()>1 else ''}",
            font=FONT_BOLD, bg=BG, fg=ACCENT_L, width=10)
        self.lbl_idx_workers.pack(side=tk.RIGHT)
        ttk.Scale(idx_wk_fr, from_=1, to=8, variable=self.v_idx_workers,
                  orient=tk.HORIZONTAL,
                  command=lambda v: (
                      self.lbl_idx_workers.configure(
                          text=f"{int(float(v))} thread{'s' if int(float(v))>1 else ''}"),
                      self._update_idx_workers_label())
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._lbl_idx_cpu = tk.Label(p, text="", font=(_FONT_FAM, 8), bg=BG, anchor="w")
        self._lbl_idx_cpu.pack(anchor="w", padx=14, pady=(0, 6))
        self._update_idx_workers_label()

        # ── Output bands ──────────────────────────────────────────────────
        self._section(p, "8. Output Bands")
        self.v_bands = {b: tk.BooleanVar(value=True) for b in ALL_BANDS}
        _tog_fr = tk.Frame(p, bg=BG); _tog_fr.pack(padx=14, anchor="w", pady=(2,0))
        def _toggle_bands():
            new_val = not all(self.v_bands[b].get() for b in ALL_BANDS)
            for b in ALL_BANDS: self.v_bands[b].set(new_val)
        self._btn(_tog_fr, "Select all / Deselect all", _toggle_bands,
                  color=BG2, font=(_FONT_FAM,9)).pack(side=tk.LEFT)
        bands_fr = tk.Frame(p, bg=BG); bands_fr.pack(padx=14, anchor="w")
        descs = {"VV":"γ⁰ VV linear","VH":"γ⁰ VH linear","CR":"γ⁰VH / γ⁰VV",
                 "RVI":"4·γ⁰VH / (γ⁰VV + γ⁰VH)  (Trudel et al. 2012, dual-pol)",
                 "DIFF":"γ⁰VH − γ⁰VV  (polarisation difference, linear)",
                }
        for b in ALL_BANDS:
            tk.Checkbutton(bands_fr, text=f"{b}   {descs[b]}",
                           variable=self.v_bands[b], bg=BG,
                           font=FONT, fg=FG, selectcolor=BG2,
                           wraplength=500, justify=tk.LEFT,
                           activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w")

        # ── Custom Bands ──────────────────────────────────────────────────
        self._section(p, "Custom Bands  (numpy expressions, saved as linear _lin.tif)")
        tk.Label(p, text="  Variables: VV  VH  CR  RVI  DIFF  np  NODATA — all linear intensity.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14)
        tk.Label(p, text="  Examples:  VH/VV   |   (VV-VH)/(VV+VH)   |   np.sqrt(VH/VV)   |   4*VH/(VV+VH+1e-15)",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=14)
        self.v_custom_bands = []
        for _i in range(3):
            _row = tk.Frame(p, bg=BG)
            _row.pack(padx=14, pady=3, fill=tk.X)
            _en   = tk.BooleanVar(value=False)
            _name = tk.StringVar(value="")
            _expr = tk.StringVar(value="")
            tk.Checkbutton(_row, text="", variable=_en,
                           bg=BG, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L)\
              .pack(side=tk.LEFT)
            tk.Label(_row, text="Name:", bg=BG, fg=FG2, font=FONT)\
              .pack(side=tk.LEFT, padx=(2, 2))
            tk.Entry(_row, textvariable=_name, width=12, bg=BG2, fg=FG,
                     insertbackground=FG, relief=tk.FLAT, font=FONT)\
              .pack(side=tk.LEFT, padx=(0, 8))
            tk.Label(_row, text="Expression:", bg=BG, fg=FG2, font=FONT)\
              .pack(side=tk.LEFT, padx=(0, 2))
            tk.Entry(_row, textvariable=_expr, width=46, bg=BG2, fg=FG,
                     insertbackground=FG, relief=tk.FLAT, font=FONT_MONO)\
              .pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.v_custom_bands.append((_en, _name, _expr))

        # ── Output scale ──────────────────────────────────────────────────
        self._section(p, "9. Output Scale")
        self.v_linear = tk.BooleanVar(value=True)
        self.v_db     = tk.BooleanVar(value=False)
        sc_fr = tk.Frame(p, bg=BG); sc_fr.pack(padx=14, anchor="w")
        tk.Checkbutton(sc_fr, text="Linear  (γ°, recommended for classifier features)",
                       variable=self.v_linear, bg=BG, font=FONT,
                       fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w")
        tk.Checkbutton(sc_fr, text="dB      (10·log10, for display)",
                       variable=self.v_db,     bg=BG, font=FONT,
                       fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w")

        # ── OUTPUT TAB ───────────────────────────────────────────────────
        p = self._make_tab_scroll(t_out)

        # ── Steps ─────────────────────────────────────────────────────────
        self._section(p, "7. Pipeline Steps")
        self.v_do_dl      = tk.BooleanVar(value=True)
        self.v_do_snap    = tk.BooleanVar(value=True)
        self.v_do_indices = tk.BooleanVar(value=True)
        self.v_clean_snap = tk.BooleanVar(value=True)
        self.v_clean_safe = tk.BooleanVar(value=False)
        steps_fr = tk.Frame(p, bg=BG); steps_fr.pack(padx=14, anchor="w")
        self.chk_dl = tk.Checkbutton(steps_fr, text="1. Download .SAFE",
                       variable=self.v_do_dl, bg=BG, font=FONT,
                       fg=FG, selectcolor=BG2, activebackground=BG,
                       activeforeground=ACCENT_L, disabledforeground=FG2)
        self.chk_dl.pack(anchor="w")
        self.chk_snap = tk.Checkbutton(steps_fr, text="2. SNAP processing",
                       variable=self.v_do_snap, bg=BG, font=FONT,
                       fg=FG, selectcolor=BG2, activebackground=BG,
                       activeforeground=ACCENT_L, disabledforeground=FG2)
        self.chk_snap.pack(anchor="w")
        self.v_retry_dl = tk.BooleanVar(value=self._saved_cfg.get("retry_download", True))
        tk.Checkbutton(steps_fr, text="  Retry failed downloads at end",
                       variable=self.v_retry_dl, bg=BG, font=(_FONT_FAM,9), fg=ACCENT_L,
                       selectcolor=BG2, activebackground=BG,
                       activeforeground=ACCENT_L).pack(anchor="w", padx=20)
        tk.Checkbutton(steps_fr, text="3. Compute indices", variable=self.v_do_indices, bg=BG, font=FONT,
                       fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w")
        tk.Checkbutton(steps_fr, text="   After step 3: delete step 2 GeoTIFFs (saves ~25 MB/scene)",
                       variable=self.v_clean_snap, bg=BG, font=(_FONT_FAM,9),
                       fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w", padx=20)
        tk.Checkbutton(steps_fr, text="   After step 2: delete .SAFE source files (saves ~4 GB/scene)",
                       variable=self.v_clean_safe, bg=BG, font=(_FONT_FAM,9),
                       fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w", padx=20)

        # ── Paths ─────────────────────────────────────────────────────────
        self._section(p, "10. Output Paths")
        self.v_safe_dir = tk.StringVar(value=self._saved_cfg.get("safe_dir", ""))
        self.v_safe_out_dir = tk.StringVar(value=self._saved_cfg.get("safe_out_dir", ""))
        self.v_snap_dir = tk.StringVar(value=self._saved_cfg.get("snap_dir", ""))
        self.v_out_dir  = tk.StringVar(value=self._saved_cfg.get("out_dir", ""))
        self.v_gpt      = tk.StringVar(value=DEFAULT_GPT)
        # v_graph is defined in section 2 (Preprocessing Graph)
        self.v_gdal     = tk.StringVar(value="")  # auto-detected; user can override
        # re-run dep check whenever GPT or GDAL path is edited
        self.v_gpt.trace_add("write",  lambda *_: self._schedule_dep_check())
        self.v_gdal.trace_add("write", lambda *_: self._schedule_dep_check())

        folder_defs = [
            ("Download / .zip folder", "Step 1 — .zip downloads land here; also holds .SAFE if the field below is blank", self.v_safe_dir),
            (".SAFE unzip folder",     "Step 1b — extracted .SAFE go here (blank = same as .zip folder). Use a fast/healthy drive; zips are still deleted after extraction", self.v_safe_out_dir),
            ("SNAP GeoTIFF folder",    "Step 2 output — 2-band VH+VV GeoTIFF per scene (can be kept or deleted)", self.v_snap_dir),
            ("COG indices folder",     "Step 3 final output — 7 separate COG files (VV, VH, CR, RVI, DIFFs)", self.v_out_dir),
        ]
        for label, desc, var in folder_defs:
            grp = tk.Frame(p, bg=BG); grp.pack(fill=tk.X, padx=14, pady=(4,0))
            row = tk.Frame(grp, bg=BG); row.pack(fill=tk.X)
            tk.Label(row, text=label, font=FONT_BOLD, bg=BG, fg=FG, anchor="w").pack(side=tk.LEFT)
            self._entry(row, var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6,0))
            self._btn(row, "…", lambda v=var: self._browse_dir(v), padx=8, pady=2
                      ).pack(side=tk.LEFT, padx=(2,0))
            tk.Label(grp, text=f"  {desc}", font=(_FONT_FAM,8), bg=BG, fg=FG2,
                     anchor="w").pack(anchor="w")

        # optional .SAFE scratch budget → chunked extract→SNAP→delete (keeps small/slow drives from filling)
        self.v_safe_scratch_gb = tk.StringVar(value=str(self._saved_cfg.get("safe_scratch_gb", "")))
        _sb = tk.Frame(p, bg=BG); _sb.pack(fill=tk.X, padx=14, pady=(6,0))
        tk.Label(_sb, text="Max .SAFE scratch (GB)", font=FONT_BOLD, bg=BG, fg=FG, anchor="w").pack(side=tk.LEFT)
        self._entry(_sb, self.v_safe_scratch_gb).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6,0))
        tk.Label(p, text="  Blank = extract everything at once (old behaviour).  A number = process in chunks so only that many GB of .SAFE exist at once (extract → SNAP → delete → repeat).  'auto' = 80% of the .SAFE drive's free space.  Stops a small/slow drive filling up.  Needs SNAP enabled.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2, anchor="w", justify="left", wraplength=760).pack(anchor="w", padx=14, pady=(0,4))

        # ── Output CRS ───────────────────────────────────────────
        self._section(p, "11. Output CRS  (reprojection applied after processing)")
        self.v_crs = tk.StringVar(value=self._saved_cfg.get("output_crs", "AUTO"))
        crs_fr = tk.Frame(p, bg=BG); crs_fr.pack(padx=14, fill=tk.X, pady=2)

        common_crs = ["AUTO (UTM from AOI centroid)", "EPSG:4326 (WGS84 geographic)",
                      "EPSG:3857 (Web Mercator)", "EPSG:32632 (UTM 32N)",
                      "EPSG:32633 (UTM 33N)", "EPSG:32634 (UTM 34N)"]
        self.crs_cb = ttk.Combobox(crs_fr, values=common_crs, font=FONT, width=38)
        cur = self.v_crs.get()
        if cur in ("AUTO","AUTO (UTM from AOI centroid)",""):
            self.crs_cb.set("AUTO (UTM from AOI centroid)")
        else:
            self.crs_cb.set(cur if cur in common_crs else cur)
        self.crs_cb.pack(side=tk.LEFT)
        def _crs_changed(e=None):
            val = self.crs_cb.get()
            # extract EPSG code if present
            import re as _re
            m = _re.search(r"EPSG:\d+", val, _re.IGNORECASE)
            self.v_crs.set(m.group(0).upper() if m else "AUTO")
        self.crs_cb.bind("<<ComboboxSelected>>", _crs_changed)
        self.crs_cb.bind("<FocusOut>", _crs_changed)
        tk.Label(crs_fr, text="  or type any EPSG code →",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(side=tk.LEFT, padx=(8,2))
        self.crs_entry = tk.Entry(crs_fr, textvariable=self.v_crs, font=FONT, width=14)
        self.crs_entry.pack(side=tk.LEFT)
        tk.Label(p, text="  AUTO = keep native UTM (fastest). Any other CRS triggers reproject of all outputs.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,4))

        # tool paths
        tk.Label(p, text="Tool Paths (edit if not auto-detected)",
                 font=(_FONT_FAM,9,"bold"), bg=BG, fg=ACCENT_L).pack(anchor="w", padx=14, pady=(6,0))
        for label, var, browse_file in [
            ("SNAP GPT (.EXE)",    self.v_gpt,   True),
            ("gdal_translate",     self.v_gdal,  True),
            # SNAP graph XML is selected in section 2 (Preprocessing Graph)
        ]:
            row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=1)
            tk.Label(row, text=label, font=(_FONT_FAM,9), bg=BG, fg=FG, width=18, anchor="w").pack(side=tk.LEFT)
            tk.Entry(row, textvariable=var, font=(_FONT_FAM,9)).pack(side=tk.LEFT, fill=tk.X, expand=True)
            if browse_file:
                self._btn(row, "…", lambda v=var: self._browse_file(v, [("Executables","*.exe *.EXE *.xml *.bat"),("All","*")]),
                          font=(_FONT_FAM,9), padx=8, pady=2
                          ).pack(side=tk.LEFT, padx=(2,0))

        # ── RASTER CALC TAB ──────────────────────────────────────────────
        p = self._make_tab_scroll(t_calc)

        # ── A. Input ─────────────────────────────────────────────────────
        self._section(p, "A. Input Folder")
        self.v_calc_input = tk.StringVar(value="")
        row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row, text="Input folder", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        self._entry(row, self.v_calc_input).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row, "…", lambda: self._browse_dir(self.v_calc_input)
                  ).pack(side=tk.LEFT, padx=(4,0))
        tk.Label(p, text="  Tip: use the COG indices folder (step 3 output) or any folder with *_VV_*.tif files.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,2))

        scan_fr = tk.Frame(p, bg=BG); scan_fr.pack(fill=tk.X, padx=14, pady=4)
        self._btn(scan_fr, "🔍  Scan bands", self._scan_calc_bands, color=BG2
                  ).pack(side=tk.LEFT)
        self.v_calc_bands_info = tk.StringVar(value="← click to detect available bands")
        tk.Label(scan_fr, textvariable=self.v_calc_bands_info,
                 font=(_FONT_FAM,9,"italic"), bg=BG, fg=ACCENT_L
                 ).pack(side=tk.LEFT, padx=(10,0))

        # ── B. Expression ────────────────────────────────────────────────
        self._section(p, "B. Expression  (numpy)")
        tk.Label(p,
                 text="  Use band names as variables: VV, VH, CR, RVI, DIFF — and np for numpy.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(2,0))
        tk.Label(p,
                 text="  Examples:  (VV - VH) / (VV + VH)   |   np.sqrt(VH / VV)   |   4 * VH / (VV + VH)",
                 font=(_FONT_FAM,8,"italic"), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,4))
        expr_outer = tk.Frame(p, bg=BG2, bd=1, relief="flat")
        expr_outer.pack(fill=tk.X, padx=14, pady=(0,6))
        self.calc_expr = tk.Text(expr_outer, height=3, font=(_FONT_FAM,11),
                                  bg=BG2, fg=FG, insertbackground=FG,
                                  relief="flat", bd=8, wrap=tk.WORD)
        self.calc_expr.insert("1.0", "(VV - VH) / (VV + VH)")
        self.calc_expr.pack(fill=tk.X)

        # ── C. Output ────────────────────────────────────────────────────
        self._section(p, "C. Output")
        self.v_calc_band_name = tk.StringVar(value="CUSTOM")
        self._row(p, "Output band name",
                  lambda par, **kw: self._entry(par, self.v_calc_band_name, **kw))
        self.v_calc_output = tk.StringVar(value="")
        row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row, text="Output folder", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        self._entry(row, self.v_calc_output).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row, "…", lambda: self._browse_dir(self.v_calc_output)
                  ).pack(side=tk.LEFT, padx=(4,0))
        tk.Label(p,
                 text="  Output files: {scene}_{band_name}.tif, mirroring the input subfolder structure.",
                 font=(_FONT_FAM,8), bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0,6))

        self.btn_calc = self._btn(p, "▶  Run Calculator", self._on_run_calc,
                                   pady=10, font=(_FONT_FAM,12,"bold"))
        self.btn_calc.pack(fill=tk.X, padx=14, pady=(2,10))
        self._btn(p, "■  Stop", self._on_stop_calc, color=RED,
                  pady=8, font=(_FONT_FAM,10,"bold")).pack(fill=tk.X, padx=14, pady=(0,8))

        # ── BATCH TAB ────────────────────────────────────────────────────
        self._build_batch_tab(t_batch)

        # ── FIXED FOOTER: Run / Stop / History ───────────────────────────
        footer = tk.Frame(parent, bg=BG2)
        footer.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 0))
        run_fr = tk.Frame(footer, bg=BG2)
        run_fr.pack(fill=tk.X, padx=8, pady=6)
        self.btn_run = self._btn(run_fr, "▶   START PIPELINE", self._on_start,
                                  pady=10, font=(_FONT_FAM, 12, "bold"))
        self.btn_run.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.btn_stop = self._btn(run_fr, "■  STOP", self._on_stop, color=RED,
                                   pady=10, font=(_FONT_FAM, 10, "bold"),
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)

        def _show_history():
            import json as _json
            hist_path = os.path.join(_SCRIPT_DIR, "sar_foundry_history.json")
            if not os.path.isfile(hist_path):
                messagebox.showinfo("Run history", "No runs recorded yet.")
                return
            try:
                history = _json.loads(open(hist_path, encoding="utf-8").read())
            except Exception:
                messagebox.showerror("Run history", "Could not read history file.")
                return
            win = tk.Toplevel(self); win.title("Run History")
            win.configure(bg=BG); win.geometry("640x400")
            tk.Label(win, text="Last 50 pipeline runs", font=FONT_BOLD,
                     bg=BG, fg=ACCENT_L).pack(anchor="w", padx=10, pady=(8,4))
            # Plain tk.Listbox (not ttk.Treeview): on Windows the vista ttk theme
            # ignores Treeview background, leaving a white panel; a Listbox honors
            # dark colours directly and needs no app-global theme change.
            hdr = (f"{'Time':<18}{'St':<4}{'AOI':<16}"
                   f"{'Dates':<26}{'Orbit':<7}{'Speckle'}")
            tk.Label(win, text=hdr, font=FONT_MONO, bg=BG, fg=FG2,
                     anchor="w", justify="left").pack(anchor="w", padx=12)
            list_fr = tk.Frame(win, bg=BG)
            list_fr.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
            lb = tk.Listbox(list_fr, font=FONT_MONO, bg=SURFACE, fg=FG,
                            selectbackground=ACCENT, selectforeground=BG,
                            relief="flat", bd=0, highlightthickness=0,
                            activestyle="none")
            sb2 = tk.Scrollbar(list_fr, orient="vertical", command=lb.yview)
            lb.configure(yscrollcommand=sb2.set)
            sb2.pack(side=tk.RIGHT, fill=tk.Y)
            lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            for h in history:
                _dates = f"{h.get('start_date','?')} → {h.get('end_date','?')}"
                lb.insert(tk.END,
                    f"{str(h.get('timestamp','?')):<18}"
                    f"{('✓' if h.get('success') else '✗'):<4}"
                    f"{str(h.get('aoi','?'))[:15]:<16}"
                    f"{_dates:<26}"
                    f"{str(h.get('orbit','?')):<7}"
                    f"{h.get('speckle','?')}")
        self._btn(footer, "↻  Retry failed downloads", self._on_retry_failed,
                  color=GOLD, font=(_FONT_FAM, 9, "bold"), pady=4
                  ).pack(fill=tk.X, padx=8, pady=(0, 2))
        self._btn(footer, "📋  Run history", _show_history,
                  color=BG2, font=(_FONT_FAM, 9), pady=4
                  ).pack(fill=tk.X, padx=8, pady=(0, 6))


    def _build_log(self, parent):
        hdr = tk.Frame(parent, bg=BG2); hdr.pack(fill=tk.X)
        tk.Frame(hdr, bg=ACCENT, width=4).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(hdr, text="  Dependencies", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT_L, pady=7).pack(side=tk.LEFT)
        self._btn(hdr, "⟳  Re-check", self._run_dep_check,
                  pady=4, padx=8, font=(_FONT_FAM,8,"bold")
                  ).pack(side=tk.RIGHT, padx=8, pady=4)
        self.dep_frame = tk.Frame(parent, bg=SURFACE)
        self.dep_frame.pack(fill=tk.X)
        self.dep_rows = tk.Frame(self.dep_frame, bg=SURFACE)
        self.dep_rows.pack(fill=tk.X, padx=4, pady=(2,4))

        log_hdr = tk.Frame(parent, bg=BG2); log_hdr.pack(fill=tk.X)
        tk.Frame(log_hdr, bg=LOG_GREEN, width=4).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(log_hdr, text="  Pipeline Log", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT_L, pady=7).pack(side=tk.LEFT)
        def _export_log():
            from tkinter import filedialog as _fd
            from datetime import datetime as _dt
            default = f"sarfoundry_log_{_dt.now().strftime('%Y%m%d_%H%M%S')}.txt"
            path = _fd.asksaveasfilename(defaultextension=".txt",
                                          initialfile=default,
                                          filetypes=[("Text files","*.txt"),("All","*")])
            if path:
                content = self.log_box.get("1.0", tk.END)
                # append hidden GDAL log if any messages were collected
                gdal_lines = getattr(self, "_gdal_log_lines", [])
                if gdal_lines:
                    content += "\n" + "="*60 + "\n"
                    content += "GDAL diagnostics (hidden from UI — harmless SNAP metadata warnings)\n"
                    content += "="*60 + "\n"
                    content += "\n".join(gdal_lines) + "\n"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
        def _clear_log():
            self.log_box.configure(state=tk.NORMAL)
            self.log_box.delete("1.0", tk.END)
            self.log_box.configure(state=tk.DISABLED)
        self._btn(log_hdr, "💾 Export", _export_log,
                  pady=4, padx=8, font=(_FONT_FAM,8)).pack(side=tk.RIGHT, padx=(0,4), pady=4)
        self._btn(log_hdr, "🗑 Clear", _clear_log,
                  pady=4, padx=8, font=(_FONT_FAM,8), color=BG2).pack(side=tk.RIGHT, pady=4)

        self.log_box = scrolledtext.ScrolledText(
            parent, font=FONT_MONO, bg=SURFACE, fg="#90A4AE",
            insertbackground=FG, wrap=tk.WORD, relief="flat",
            state=tk.DISABLED, padx=8, pady=6)
        self.log_box.pack(fill=tk.BOTH, expand=True)

        self.log_box.tag_configure("ok",    foreground=LOG_GREEN)
        self.log_box.tag_configure("error", foreground=RED)
        self.log_box.tag_configure("warn",  foreground=GOLD)
        self.log_box.tag_configure("info",  foreground=ACCENT_L)
        self.log_box.tag_configure("head",  foreground=FG, font=(_FONT_FAM,9,"bold"))
        self.log_box.tag_configure("dim",   foreground="#546E7A")
        self.log_box.tag_configure("date",  foreground="#CE93D8")

        # ── per-phase progress bars ───────────────────────────────────────
        prog_frame = tk.Frame(parent, bg=SURFACE)
        prog_frame.pack(fill=tk.X, padx=6, pady=(4,0))
        self._prog_bars = {}
        for _phase, _lbl in [("download","Download"),("unzip","Unzip"),("process","Processing")]:
            _r = tk.Frame(prog_frame, bg=SURFACE); _r.pack(fill=tk.X, pady=1)
            tk.Label(_r, text=f"{_lbl}:", font=(_FONT_FAM,8,"bold"),
                     bg=SURFACE, fg=FG2, width=11, anchor="w").pack(side=tk.LEFT)
            # live "ETA ~4m · 145 Mbps" pinned to the right of THIS phase's bar
            _stat = tk.StringVar(value="")
            tk.Label(_r, textvariable=_stat, font=FONT_MONO,
                     bg=SURFACE, fg=ACCENT_L, width=24, anchor="e").pack(side=tk.RIGHT)
            _cnt = tk.StringVar(value="")
            tk.Label(_r, textvariable=_cnt, font=FONT_MONO,
                     bg=SURFACE, fg=FG2, width=8, anchor="e").pack(side=tk.RIGHT, padx=(4,6))
            _bar = ttk.Progressbar(_r, mode="determinate", maximum=100, value=0)
            _bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,4))
            self._prog_bars[_phase] = (_bar, _cnt, _stat)
        # elapsed row (overall wall-clock; per-phase ETA/speed live on the bars)
        eta_row = tk.Frame(parent, bg=SURFACE)
        eta_row.pack(fill=tk.X, padx=8, pady=(4, 2))
        self._eta_var     = tk.StringVar(value="")
        self._elapsed_var = tk.StringVar(value="")
        tk.Label(eta_row, textvariable=self._elapsed_var,
                 font=FONT_MONO, bg=SURFACE, fg=FG2, anchor="w").pack(side=tk.LEFT)

        # overall indeterminate spinner (shows pipeline is running)
        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(0, 4))

    # ── progress helpers ──────────────────────────────────────────────────────

    def _tick_elapsed(self):
        """Tick the elapsed-time label once a second while a run is active, so it
        updates live instead of only when a progress event fires."""
        if not getattr(self, "_running", False) or not getattr(self, "_pipeline_start", None):
            return
        s = int(time.time() - self._pipeline_start)
        if s < 60:     t = f"{s}s"
        elif s < 3600: t = f"{s//60}m {s%60:02d}s"
        else:          t = f"{s//3600}h {(s%3600)//60:02d}m"
        if hasattr(self, "_elapsed_var"):
            self._elapsed_var.set(f"Elapsed: {t}")
        self.after(1000, self._tick_elapsed)

    def _update_progress(self, phase, current, total, label="", speed=""):
        def _do():
            if not hasattr(self, "_prog_bars") or phase not in self._prog_bars:
                return
            bar, cnt_var, stat_var = self._prog_bars[phase]
            pct = (current / total * 100) if total > 0 else 0
            bar["value"] = pct
            cnt_var.set(f"{current}/{total}" if total > 0 else "")

            def _fmt(s):
                s = int(s)
                if s < 60:   return f"{s}s"
                if s < 3600: return f"{s//60}m {s%60:02d}s"
                return f"{s//3600}h {(s%3600)//60:02d}m"

            # Speed/throughput shown right of the bar. Download passes a live
            # byte rate ("145 Mbps"); other phases derive scenes/min from the
            # recent completion history below.
            if not hasattr(self, "_pipeline_start") or not self._pipeline_start:
                if speed:
                    stat_var.set(speed)
                return

            now = time.time()
            if hasattr(self, "_elapsed_var"):
                self._elapsed_var.set(f"Elapsed: {_fmt(now - self._pipeline_start)}")

            # Moving-average over recent completions — reflects the RECENT rate
            # (not the whole-phase average), so slow early scenes (JVM cold
            # start, first DEM download) don't skew it, and it already accounts
            # for parallel workers since it is wall-clock based.
            if not hasattr(self, "_phase_hist"):
                self._phase_hist = {}
            hist = self._phase_hist.setdefault(phase, [])
            if not hist or hist[-1][0] != current:
                hist.append((current, now))
            WIN = 10
            if len(hist) > WIN:
                del hist[:-WIN]

            rate = ""
            if len(hist) >= 2:
                c0, t0 = hist[0]
                dc, dt = current - c0, now - t0
                if dc > 0 and dt > 0:
                    rate = f"{dc / dt * 60:.1f}/min"
            spd = speed or rate       # live byte-rate wins; else scenes/min

            eta = ""
            if current >= total > 0:
                eta = "✓ done"
            elif current > 0 and total > current and len(hist) >= 2:
                c0, t0 = hist[0]
                dc, dt = current - c0, now - t0
                if dc > 0 and dt > 0:
                    eta = f"ETA ~{_fmt((dt / dc) * (total - current))}"

            stat_var.set("  ·  ".join(p for p in (eta, spd) if p))

        self.after(0, _do)

    def _reset_all_progress(self):
        if not hasattr(self, "_prog_bars"):
            return
        for bar, cnt_var, txt_var in self._prog_bars.values():
            bar["value"] = 0
            cnt_var.set("")
            txt_var.set("")
        if hasattr(self, "_eta_var"):     self._eta_var.set("")
        if hasattr(self, "_elapsed_var"): self._elapsed_var.set("")
        self._pipeline_start = None
        self._phase_starts   = {}
        self._phase_hist     = {}

    # ── dependency check ──────────────────────────────────────────────────────

    def _schedule_dep_check(self):
        """Debounced dep check — collapses multiple rapid calls into one."""
        if getattr(self, "_suppress_dep_trace", False):
            return   # programmatic auto-fill, not a user edit — no re-check
        if getattr(self, "_dep_check_pending", False):
            return
        self._dep_check_pending = True
        def _fire():
            self._dep_check_pending = False
            self._run_dep_check()
        self.after(1500, _fire)

    def _run_dep_check(self):
        gpt_path  = self.v_gpt.get()  if hasattr(self, "v_gpt")  else DEFAULT_GPT
        gdal_path = self.v_gdal.get() if hasattr(self, "v_gdal") else ""
        def _check():
            results = check_dependencies(gpt_path, gdal_path.strip() or None)
            self.after(0, lambda: self._show_dep_results(results))
        threading.Thread(target=_check, daemon=True).start()
        for widget in self.dep_rows.winfo_children():
            widget.destroy()
        tk.Label(self.dep_rows, text="Checking dependencies...",
                 font=("Calibri",9,"italic"), fg="#A8C97A", bg=DARK).pack(anchor="w")

    def _show_dep_results(self, results):
        self._dep_results = results  # store for pre-flight check
        for widget in self.dep_rows.winfo_children():
            widget.destroy()
        all_ok = True
        for name, (ok, detail) in results.items():
            row = tk.Frame(self.dep_rows, bg=SURFACE)
            row.pack(fill=tk.X, pady=1)
            if ok is True:      icon, fg = "✓", LOG_GREEN
            elif ok == "info":  icon, fg = "i", FG2
            elif ok == "install": icon, fg = "⚠", GOLD; all_ok = False
            elif ok is None:    icon, fg = "i", GOLD
            else:               icon, fg = "✗", RED; all_ok = False
            tk.Label(row, text=f" {icon} ", font=(_FONT_FAM,9,"bold"),
                     fg=fg, bg=SURFACE, width=4).pack(side=tk.LEFT)
            tk.Label(row, text=name, font=(_FONT_FAM,9,"bold"),
                     fg=FG, bg=SURFACE, width=26, anchor="w").pack(side=tk.LEFT)
            short = detail.split("\n")[0][:62]
            lbl = tk.Label(row, text=short, font=(_FONT_FAM,9),
                     fg=LOG_GREEN if ok is True else GOLD if ok in (None,"info","install") else RED,
                     bg=SURFACE, anchor="w")
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

            # show INSTALL button for missing Python packages
            if ok == "install":
                pkgs = [w for w in detail.split("\n")[0].replace("Missing: ","").split(", ") if w]
                def _do_install(packages=pkgs):
                    btn_ref["state"] = tk.DISABLED
                    self._log("\n[INSTALL] Installing: " + " ".join(packages) + " ...")
                    def _run():
                        r = subprocess.run(
                            [sys.executable, "-m", "pip", "install"] + packages + ["--quiet"],
                            capture_output=True, text=True)
                        if r.returncode == 0:
                            self._log("[INSTALL] Done!")
                            self._log("[INSTALL] Restarting app to activate new packages...")
                            def _restart():
                                import subprocess as _sp
                                _sp.Popen([sys.executable] + sys.argv)
                                self.destroy()
                            self.after(1500, _restart)
                        else:
                            self._log("[INSTALL] Failed: " + r.stderr[:200])
                            self.after(500, self._run_dep_check)
                    threading.Thread(target=_run, daemon=True).start()
                btn_ref = self._btn(row, "INSTALL", _do_install, color=GOLD, font=(_FONT_FAM,8,"bold"), padx=8, pady=2)
                btn_ref.pack(side=tk.LEFT, padx=(4,0))

        tk.Frame(self.dep_rows, bg=ACCENT, height=1).pack(fill=tk.X, pady=(4,2))
        summary = "All dependencies OK" if all_ok else "Some dependencies missing -- see red items"
        sfg = LOG_GREEN if all_ok else GOLD
        tk.Label(self.dep_rows, text=f"  {summary}",
                 font=(_FONT_FAM,9,"bold"), fg=sfg, bg=SURFACE).pack(anchor="w")
        self._log("-- Dependency check --")
        has_missing = False
        for name, (ok, detail) in results.items():
            if ok is True:
                self._log(f"  [  ✓   ] {name}: {detail.split(chr(10))[0]}")
            elif ok in (None, "info", "install"):
                self._log(f"  [  ℹ   ] {name}: {detail.split(chr(10))[0]}")
            elif ok not in (None, "info", "install"):
                has_missing = True
                self._log(f"  [  ✗   ] {name}: {detail.split(chr(10))[0]}")
        self._log("----------------------")

        # auto-fill gdal path in UI if it was found and field is empty.
        # Suppress the write-trace so this programmatic set doesn't kick off a
        # second dep check (the path is the one we just checked).
        for name, (ok, detail) in results.items():
            if ok is True and "gdal" in name.lower() and hasattr(self, "v_gdal"):
                if not self.v_gdal.get().strip():
                    found_path = detail.split("\n")[0].strip()
                    if os.path.isfile(found_path):
                        self._suppress_dep_trace = True
                        self.v_gdal.set(found_path)
                        self._suppress_dep_trace = False

        # print installation instructions for anything missing
        if has_missing:
            self._log("")
            self._log("HOW TO FIX MISSING DEPENDENCIES:")
            self._log("")
            for name, (ok, detail) in results.items():
                if ok is False:
                    self._log(f"  >>> {name}")
                    for line in detail.split("\n"):
                        self._log(f"      {line}")
                    self._log("")
            self._log("After installing, restart this application.")
            self._log("----------------------------------------------")


    # ── callbacks ─────────────────────────────────────────────────────────────

    def _log(self, text):
        def _append():
            self.log_box.configure(state=tk.NORMAL)
            tl = text.lower()
            if text.startswith("=") or text.startswith("-"):
                tag = "head"
            elif any(x in tl for x in ("ok", "done", "success", "completed", "written")):
                tag = "ok"
            elif any(x in tl for x in ("error", "fail", "missing", "err]", "[error")):
                tag = "error"
            elif any(x in tl for x in ("warn", "skip", "info]", "[warn", "no data")):
                tag = "warn"
            elif any(x in tl for x in ("started", "processing", "downloading", "aoi", "crs")):
                tag = "info"
            elif text.strip().startswith("202"):
                tag = "date"
            elif text.startswith("  "):
                tag = "dim"
            else:
                tag = "info"
            self.log_box.insert(tk.END, text + "\n", tag)
            self.log_box.see(tk.END)
            self.log_box.configure(state=tk.DISABLED)
        self.after(0, _append)

    def _show_calendar_popup(self, var):
        """Open a stable calendar popup window."""
        import datetime
        popup = tk.Toplevel(self)
        popup.title("Select date")
        popup.resizable(False, False)
        popup.grab_set()  # modal — stays on top and captures all events

        try:
            d = datetime.datetime.strptime(var.get(), "%Y-%m-%d").date()
        except Exception:
            d = datetime.date.today()

        from tkcalendar import Calendar as _Cal
        popup.configure(bg=BG)
        cal = _Cal(popup, selectmode="day",
                   year=d.year, month=d.month, day=d.day,
                   date_pattern="yyyy-mm-dd",
                   background=BG2, foreground=FG,
                   selectbackground=ACCENT, selectforeground=WHITE,
                   headersbackground=ACCENT2, headersforeground=WHITE,
                   normalbackground=BG, normalforeground=FG,
                   weekendbackground=BG, weekendforeground=ACCENT_L,
                   othermonthbackground=BG, othermonthforeground=FG2,
                   font=FONT)
        cal.pack(padx=10, pady=10)

        def _select():
            var.set(cal.get_date())
            popup.destroy()

        self._btn(popup, "  Select  ", _select, pady=8).pack(pady=(0,12))

        # position near the main window
        popup.update_idletasks()
        x = self.winfo_x() + 200
        y = self.winfo_y() + 200
        popup.geometry(f"+{x}+{y}")
        popup.focus_set()

    def _show_calendar_range(self, start_var, end_var):
        """One calendar, two clicks: first sets start, second sets end
        (auto-ordered). Fills start_var/end_var as YYYY-MM-DD."""
        import datetime
        popup = tk.Toplevel(self)
        popup.title("Select date range")
        popup.resizable(False, False)
        popup.grab_set()
        popup.configure(bg=BG)
        try:
            d = datetime.datetime.strptime(start_var.get(), "%Y-%m-%d").date()
        except Exception:
            d = datetime.date.today()
        from tkcalendar import Calendar as _Cal
        info = tk.Label(popup, text="Click the START date…", bg=BG, fg=ACCENT_L, font=FONT_BOLD)
        info.pack(padx=10, pady=(10, 4))
        cal = _Cal(popup, selectmode="day", year=d.year, month=d.month, day=d.day,
                   date_pattern="yyyy-mm-dd",
                   background=BG2, foreground=FG, selectbackground=ACCENT, selectforeground=WHITE,
                   headersbackground=ACCENT2, headersforeground=WHITE,
                   normalbackground=BG, normalforeground=FG,
                   weekendbackground=BG, weekendforeground=ACCENT_L,
                   othermonthbackground=BG, othermonthforeground=FG2, font=FONT)
        cal.pack(padx=10, pady=(0, 10))
        state = {"start": None}
        def _on_click(_e=None):
            picked = cal.get_date()   # yyyy-mm-dd sorts lexicographically
            if state["start"] is None:
                state["start"] = picked
                info.configure(text=f"Start: {picked}  —  now click the END date")
            else:
                a, b = sorted([state["start"], picked])
                start_var.set(a); end_var.set(b)
                popup.destroy()
        cal.bind("<<CalendarSelected>>", _on_click)
        self._btn(popup, "  Cancel  ", popup.destroy, color=BG2, pady=6).pack(pady=(0, 12))
        popup.update_idletasks()
        popup.geometry(f"+{self.winfo_x() + 200}+{self.winfo_y() + 160}")
        popup.focus_set()

    def _speckle_spec(self, name, params=None):
        """Map a batch speckle choice to (speckle_key, speckle_params).
        Accepts filter display names, legacy keys (lee/gamma/none), or None.
        Uses the popup-tuned params for that filter when the caller passes none,
        so tuning applies across every mode."""
        if name in ("none", "None", "", None):
            return ("none", None)
        name = {"lee": "Lee Sigma", "gamma": "Gamma Map"}.get(name, name)
        if name in FILTER_DEFAULTS:
            params = params or self._batch_speckle_params.get(name) or FILTER_DEFAULTS[name]
            return ("custom", dict(params))
        return ("none", None)

    def _batch_speckle_summary(self):
        on = [n for n in (["None"] + SPECKLE_FILTER_NAMES)
              if self._batch_speckle_on.get(n) and self._batch_speckle_on[n].get()]
        return ("  → " + ", ".join(on)) if on else "  (none selected)"

    def _open_batch_speckle_popup(self):
        dlg = tk.Toplevel(self)
        dlg.title("Batch speckle filters")
        dlg.configure(bg=BG); dlg.resizable(False, False); dlg.grab_set()
        tk.Label(dlg, text="  Select speckle filters to run (one output per filter).\n"
                 "  Use Configure to tune a filter's parameters.",
                 font=FONT, bg=BG, fg=FG2, justify="left").pack(anchor="w", padx=14, pady=(12, 6))
        body = tk.Frame(dlg, bg=BG); body.pack(fill=tk.X, padx=18, pady=4)
        for name in ["None"] + SPECKLE_FILTER_NAMES:
            row = tk.Frame(body, bg=BG); row.pack(fill=tk.X, pady=1)
            tk.Checkbutton(row, text=name, variable=self._batch_speckle_on[name],
                           bg=BG, font=FONT, fg=FG, width=16, anchor="w",
                           selectcolor=BG2, activebackground=BG,
                           activeforeground=ACCENT_L).pack(side=tk.LEFT)
            if name != "None" and FILTER_SCHEMAS.get(name):
                self._btn(row, "Configure", lambda n=name: self._open_filter_config(n),
                          color=BG2, font=(_FONT_FAM, 9)).pack(side=tk.LEFT, padx=6)
        def _close():
            self._lbl_batch_speckle.configure(text=self._batch_speckle_summary())
            dlg.destroy()
        self._btn(dlg, "  Done  ", _close, pady=8).pack(pady=(8, 12))
        dlg.update_idletasks()
        dlg.geometry(f"+{self.winfo_x() + 180}+{self.winfo_y() + 120}")

    def _open_filter_config(self, name):
        """Schema-driven param editor for one batch speckle filter (mirrors the
        single-AOI custom dialog, but writes into self._batch_speckle_params)."""
        schema = FILTER_SCHEMAS.get(name, [])
        if not schema:
            return
        dlg = tk.Toplevel(self)
        dlg.title(f"Configure — {name}")
        dlg.configure(bg=BG); dlg.resizable(False, False); dlg.grab_set()
        saved = self._batch_speckle_params.get(name, dict(FILTER_DEFAULTS.get(name, {})))
        pvars = {}
        for item in schema:
            key, wtype, lbl = item[0], item[1], item[2]
            row = tk.Frame(dlg, bg=BG); row.pack(fill=tk.X, padx=14, pady=3)
            tk.Label(row, text=lbl, font=FONT, bg=BG, fg=FG, width=24, anchor="w").pack(side=tk.LEFT)
            if wtype == "bool":
                var = tk.BooleanVar(value=(saved.get(key, "true" if item[3] else "false") == "true"))
                tk.Checkbutton(row, variable=var, bg=BG, selectcolor=BG2,
                               activebackground=BG, fg=FG, activeforeground=ACCENT_L).pack(side=tk.LEFT)
            elif wtype == "choice":
                choices, dflt = item[3], item[4]
                cur = saved.get(key, dflt)
                var = tk.StringVar(value=cur if cur in choices else dflt)
                self._mk_optmenu(row, var, choices).pack(side=tk.LEFT)
            else:
                mn, mx, dflt = item[3], item[4], item[5]
                var = tk.StringVar(value=saved.get(key, str(dflt)))
                self._entry(row, var, width=8).pack(side=tk.LEFT)
                tk.Label(row, text=f"  ({mn}-{mx})", font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(side=tk.LEFT)
            pvars[key] = var
        def _apply():
            params = dict(FILTER_DEFAULTS.get(name, {"filter": name}))
            for item in schema:
                key, wtype = item[0], item[1]
                v = pvars[key].get()
                params[key] = ("true" if v else "false") if wtype == "bool" else str(v).strip()
            self._batch_speckle_params[name] = params
            self._batch_speckle_on[name].set(True)   # tuning implies selected
            dlg.destroy()
        _bfr = tk.Frame(dlg, bg=BG); _bfr.pack(fill=tk.X, padx=14, pady=10)
        self._btn(_bfr, "  Apply  ", _apply).pack(side=tk.RIGHT, padx=(6, 0))
        self._btn(_bfr, "  Cancel  ", dlg.destroy, color=BG2).pack(side=tk.RIGHT)
        dlg.update_idletasks()
        dlg.geometry(f"+{self.winfo_x() + 220}+{self.winfo_y() + 140}")

    def _on_remember_change(self):
        """Save or clear credentials when checkbox changes. Stored in plain text
        in sar_foundry_config.json — the CDSE password is included at the user's
        request (relaxes S7's tokens-only rule)."""
        if self.v_remember.get():
            _save_config({
                "asf_token":       self.v_token.get(),
                "cdse_user":       self.v_cdse_user.get(),
                "cdse_pass":       self.v_cdse_pass.get(),
                "s3_access":       self.v_s3_access.get(),
                "s3_secret":       self.v_s3_secret.get(),
                "remember_creds":  True,
            })
        else:
            _save_config({"remember_creds": False,
                          "asf_token": "", "cdse_user": "", "cdse_pass": "",
                          "s3_access": "", "s3_secret": ""})
    def _on_graph_preset_change(self):
        if self.v_graph_preset.get() == "custom":
            self.custom_graph_fr.pack(fill=tk.X, padx=4, pady=(2,4))
        else:
            self.custom_graph_fr.pack_forget()

    def _speckle_custom_summary(self):
        p = self._speckle_custom_params
        if not p:
            return "(not configured)"
        parts = [p.get("filter", "?")]
        for k in ("filterSizeX", "windowSize", "anSize"):
            if k in p:
                parts.append(f"{k}={p[k]}")
                break
        if "sigmaStr" in p:
            parts.append(f"sigma={p['sigmaStr']}")
        return "  -> " + " | ".join(parts)

    def _open_speckle_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Custom Speckle Filter")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width()  // 2 - 240
        y = self.winfo_y() + self.winfo_height() // 2 - 230
        dlg.geometry(f"480x460+{x}+{y}")
        hdr = tk.Frame(dlg, bg=ACCENT2, pady=8); hdr.pack(fill=tk.X)
        tk.Label(hdr, text="  Speckle Filter Configuration",
                 font=FONT_H, bg=ACCENT2, fg=WHITE).pack(side=tk.LEFT, padx=12)
        sel_fr = tk.Frame(dlg, bg=BG2, pady=10)
        sel_fr.pack(fill=tk.X, padx=16, pady=(12, 0))
        tk.Label(sel_fr, text="Filter:", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT_L, width=20, anchor="w").pack(side=tk.LEFT)
        _fnames = list(FILTER_SCHEMAS.keys())
        _sav_f  = (self._speckle_custom_params or {}).get("filter", "Lee Sigma")
        v_filt  = tk.StringVar(value=_sav_f if _sav_f in _fnames else "Lee Sigma")
        from tkinter import ttk as _ttkS
        _fcb = _ttkS.Combobox(sel_fr, textvariable=v_filt, values=_fnames,
                               state="readonly", width=22, font=FONT)
        _fcb.pack(side=tk.LEFT, padx=(4, 0))
        _pout = tk.Frame(dlg, bg=BG)
        _pout.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        _pvars = {}

        def _build(fname):
            for w in _pout.winfo_children(): w.destroy()
            _pvars.clear()
            schema = FILTER_SCHEMAS.get(fname, [])
            saved  = self._speckle_custom_params or {}
            if not schema:
                tk.Label(_pout, text="No parameters for this filter.",
                         font=FONT, bg=BG, fg=FG2).pack(pady=20)
                return
            for item in schema:
                key, wtype, lbl = item[0], item[1], item[2]
                row = tk.Frame(_pout, bg=BG); row.pack(fill=tk.X, pady=3)
                tk.Label(row, text=lbl, font=FONT, bg=BG, fg=FG,
                         width=24, anchor="w").pack(side=tk.LEFT)
                if wtype == "bool":
                    dflt = saved.get(key, "true" if item[3] else "false") == "true"
                    var  = tk.BooleanVar(value=dflt)
                    tk.Checkbutton(row, variable=var, bg=BG, selectcolor=BG2,
                                   activebackground=BG, fg=FG,
                                   activeforeground=ACCENT_L).pack(side=tk.LEFT)
                elif wtype == "choice":
                    choices, dflt = item[3], item[4]
                    cur = saved.get(key, dflt)
                    if cur not in choices: cur = dflt
                    var = tk.StringVar(value=cur)
                    om  = tk.OptionMenu(row, var, *choices)
                    om.configure(bg=BG2, fg=FG, activebackground=BG,
                                 activeforeground=ACCENT_L, relief="flat",
                                 font=FONT, highlightthickness=0)
                    om["menu"].configure(bg=BG2, fg=FG,
                                         activebackground=ACCENT2, activeforeground=WHITE)
                    om.pack(side=tk.LEFT)
                else:
                    mn, mx, dflt = item[3], item[4], item[5]
                    var = tk.StringVar(value=saved.get(key, str(dflt)))
                    tk.Entry(row, textvariable=var, font=FONT, bg=BG2, fg=FG,
                             insertbackground=FG, relief="flat", bd=4,
                             width=8).pack(side=tk.LEFT)
                    tk.Label(row, text=f"  ({mn}-{mx})", font=(_FONT_FAM, 8),
                             bg=BG, fg=FG2).pack(side=tk.LEFT)
                _pvars[key] = var

        _build(v_filt.get())
        _fcb.bind("<<ComboboxSelected>>", lambda e: _build(v_filt.get()))
        _bfr = tk.Frame(dlg, bg=BG, pady=10); _bfr.pack(fill=tk.X, padx=16)

        def _apply():
            fname  = v_filt.get()
            params = dict(FILTER_DEFAULTS.get(fname, {"filter": fname}))
            for item in FILTER_SCHEMAS.get(fname, []):
                key, wtype = item[0], item[1]
                if key not in _pvars: continue
                val = _pvars[key].get()
                params[key] = ("true" if val else "false") if wtype == "bool"                               else str(val).strip()
            self._speckle_custom_params = params
            self.v_speckle.set("custom")
            self._lbl_speckle_custom.configure(text=self._speckle_custom_summary())
            dlg.destroy()

        self._btn(_bfr, "  Apply  ", _apply).pack(side=tk.RIGHT, padx=(6,0))
        self._btn(_bfr, "  Cancel  ", dlg.destroy, color=BG2).pack(side=tk.RIGHT)


    # ── Raster Calculator helpers ──────────────────────────────────────────

    def _scan_calc_bands(self):
        folder = self.v_calc_input.get().strip()
        if not folder or not os.path.isdir(folder):
            self.v_calc_bands_info.set("⚠ Set a valid input folder first")
            return
        import re as _re2
        BAND_RE = _re2.compile(
            r'_(VV|VH|CR|RVI|DIFF(?:VV|VH|polar)?|DIFFpolar|dVV|dVH)'
            r'(?:_(lin|dB|linear|db))?\.tif$', _re2.IGNORECASE)
        tifs = (glob.glob(os.path.join(folder, "**", "*.tif"), recursive=True) +
                glob.glob(os.path.join(folder, "*.tif")))
        bands_found, prefixes = set(), set()
        for f in tifs:
            m = BAND_RE.search(os.path.basename(f))
            if m:
                bands_found.add(m.group(1).upper())
                prefixes.add(f[:len(f) - len(m.group(0))])
        if bands_found:
            self.v_calc_bands_info.set(
                f"{len(prefixes)} scenes  |  bands: {', '.join(sorted(bands_found))}")
        else:
            self.v_calc_bands_info.set("No band files detected — check folder")

    def _on_run_calc(self):
        if self._running:
            messagebox.showwarning("Pipeline running",
                "The main pipeline is running.\nStop it before using the calculator.")
            return
        if getattr(self, "_calc_running", False):
            messagebox.showinfo("Calculator", "Calculator is already running.")
            return
        folder_in  = self.v_calc_input.get().strip()
        folder_out = self.v_calc_output.get().strip()
        band_out   = self.v_calc_band_name.get().strip() or "CUSTOM"
        expr       = self.calc_expr.get("1.0", tk.END).strip()
        if not folder_in or not os.path.isdir(folder_in):
            messagebox.showerror("Input folder", "Please select a valid input folder.")
            return
        if not folder_out:
            messagebox.showerror("Output folder", "Please set an output folder.")
            return
        if not expr:
            messagebox.showerror("Expression", "Please enter an expression.")
            return
        self._calc_running = True
        self._calc_stop_ev = threading.Event()
        self.btn_calc.configure(state=tk.DISABLED, bg="#455A64")
        self._log(f"\n── Raster Calculator ──")
        self._log(f"  Input:  {folder_in}")
        self._log(f"  Output: {folder_out}")
        self._log(f"  Band:   {band_out}  |  Expr: {expr}")
        def _worker():
            try:
                _raster_calc_worker(folder_in, expr, band_out, folder_out,
                                    self._log, None, self._calc_stop_ev)
            except Exception as _ce:
                import traceback as _tb2
                self._log(f"  ERROR: {_ce}")
                self._log(_tb2.format_exc())
            finally:
                self._calc_running = False
                self.after(0, lambda: self.btn_calc.configure(
                    state=tk.NORMAL, bg=ACCENT))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_stop_calc(self):
        if hasattr(self, "_calc_stop_ev"):
            self._calc_stop_ev.set()
            self._log("  [Calculator stop requested]")

    def _on_source_change(self):
        """Show/hide existing folder pickers, lock/unlock download + SNAP checkboxes."""
        src = self.v_safe_source.get()

        # hide both pickers first, then show the relevant one
        self.existing_fr.pack_forget()
        self.existing_snap_fr.pack_forget()

        if src in ("existing", "existing_zip"):
            self.existing_fr.pack(fill=tk.X, padx=10, pady=(0, 4))
            self.v_do_dl.set(False)
            self.v_do_snap.set(True)
            if hasattr(self, "chk_dl"):
                self.chk_dl.configure(state=tk.DISABLED)
            if hasattr(self, "chk_snap"):
                self.chk_snap.configure(state=tk.NORMAL)
            _iszip = (src == "existing_zip")
            if hasattr(self, "_existing_lbl"):
                self._existing_lbl.configure(
                    text="Existing .zip folder:" if _iszip else "Existing .SAFE folder:")
            if hasattr(self, "_existing_help"):
                self._existing_help.configure(
                    text=("  Download skipped. These .zip are unzipped (to the '.SAFE unzip "
                          "folder' if you set one), then SNAP-processed." if _iszip
                          else "  Download + unzip skipped. .SAFE go straight to SNAP."))
            # auto-fill from the Download/.zip folder if still empty
            if hasattr(self, "v_existing_safe") and not self.v_existing_safe.get().strip():
                candidate = getattr(self, "v_safe_dir", None)
                if candidate and candidate.get().strip() and os.path.isdir(candidate.get().strip()):
                    self.v_existing_safe.set(candidate.get().strip())

        elif src == "existing_snap":
            self.existing_snap_fr.pack(fill=tk.X, padx=10, pady=(0, 4))
            self.v_do_dl.set(False)
            self.v_do_snap.set(False)
            if hasattr(self, "chk_dl"):
                self.chk_dl.configure(state=tk.DISABLED)
            if hasattr(self, "chk_snap"):
                self.chk_snap.configure(state=tk.DISABLED)
            # auto-fill from "SNAP GeoTIFF folder" (section 10) if still empty
            if hasattr(self, "v_existing_snap") and not self.v_existing_snap.get().strip():
                candidate = getattr(self, "v_snap_dir", None)
                if candidate and candidate.get().strip() and os.path.isdir(candidate.get().strip()):
                    self.v_existing_snap.set(candidate.get().strip())

        else:  # download (ASF or CDSE)
            self.v_do_dl.set(True)
            self.v_do_snap.set(True)
            if hasattr(self, "chk_dl"):
                self.chk_dl.configure(state=tk.NORMAL)
            if hasattr(self, "chk_snap"):
                self.chk_snap.configure(state=tk.NORMAL)

    # ── Batch AOI runner ────────────────────────────────────────────────
    def _mk_optmenu(self, parent, var, options, width=8):
        om = tk.OptionMenu(parent, var, *options)
        om.configure(bg=BG2, fg=FG, activebackground=BG, activeforeground=ACCENT_L,
                     relief="flat", font=(_FONT_FAM, 9), highlightthickness=0,
                     width=width, anchor="w")
        om["menu"].configure(bg=BG2, fg=FG, activebackground=ACCENT2, activeforeground=WHITE)
        return om

    def _build_batch_tab(self, parent):
        p = self._make_tab_scroll(parent)
        _bsaved = self._saved_cfg.get("batch", {})
        # only AOIs that still exist survive a reload
        self._batch_aois = [a for a in _bsaved.get("aois", []) if os.path.isfile(a)]
        self._batch_per_aoi = {}   # aoi_path -> (preset_var, speckle_var, start_var, end_var)
        self._batch_saved_per_aoi = _bsaved.get("per_aoi", {})  # seeds per-AOI rows on rebuild
        # speckle selection state for the popup (all-combinations mode)
        _sp_saved = _bsaved.get("speckle_params", {})
        _sp_on    = set(_bsaved.get("all_speckles_on", ["Lee Sigma"]))
        self._batch_speckle_on = {n: tk.BooleanVar(value=(n in _sp_on))
                                  for n in ["None"] + SPECKLE_FILTER_NAMES}
        self._batch_speckle_params = {n: dict(_sp_saved.get(n, FILTER_DEFAULTS.get(n, {"filter": n})))
                                      for n in SPECKLE_FILTER_NAMES}
        # DEM is a third per-mode dimension (short tags: cop30, srtm1, …)
        _dem_default = SNAP_DEM_TAGS.get(self.v_dem.get(), "cop30")
        _dem_on = set(_bsaved.get("all_dems", [_dem_default]))
        self.v_batch_dem = tk.StringVar(value=_bsaved.get("uniform_dem", _dem_default))
        self.v_batch_cd = {t: tk.BooleanVar(value=(t in _dem_on)) for t in DEM_TAG_LIST}

        tk.Label(p, text="Batch: process many AOIs in one sequential run. Each AOI (and each\n"
                         "pipeline) gets its own auto-named output folder. AOIs run one after another.",
                 font=(_FONT_FAM, 9), bg=BG, fg=FG2, justify="left").pack(anchor="w", padx=14, pady=(10, 4))

        # 1. AOI files
        self._section(p, "1. AOI files")
        lb_fr = tk.Frame(p, bg=BG); lb_fr.pack(fill=tk.X, padx=14, pady=(2, 2))
        self.batch_listbox = tk.Listbox(lb_fr, height=6, font=(_FONT_FAM, 9),
                                        bg=BG2, fg=FG, selectbackground=ACCENT2,
                                        relief="flat", highlightthickness=0, activestyle="none",
                                        selectmode=tk.EXTENDED)
        self.batch_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _sb = tk.Scrollbar(lb_fr, command=self.batch_listbox.yview); _sb.pack(side=tk.LEFT, fill=tk.Y)
        self.batch_listbox.config(yscrollcommand=_sb.set)
        btns = tk.Frame(p, bg=BG); btns.pack(fill=tk.X, padx=14, pady=(2, 4))
        self._btn(btns, "＋ Add files…", self._batch_add_files, color=BG2).pack(side=tk.LEFT)
        self._btn(btns, "📁 Add folder…", self._batch_add_folder, color=BG2).pack(side=tk.LEFT, padx=4)
        self._btn(btns, "－ Remove", self._batch_remove, color=BG2).pack(side=tk.LEFT)
        self._btn(btns, "Clear", self._batch_clear, color=BG2).pack(side=tk.LEFT, padx=4)

        # 2. Pipeline assignment
        self._section(p, "2. Pipeline assignment")
        self.v_batch_mode = tk.StringVar(value=_bsaved.get("mode", "uniform"))
        for _val, _lbl in [
            ("uniform", "Same pipeline + speckle + DEM for all AOIs"),
            ("all",     "All combinations — every AOI × pipelines × speckles × DEMs"),
            ("per_aoi", "Per-AOI — choose pipeline + speckle + DEM for each AOI"),
        ]:
            tk.Radiobutton(p, text=_lbl, variable=self.v_batch_mode, value=_val,
                           bg=BG, font=FONT_BOLD, fg=ACCENT_L, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L,
                           command=self._on_batch_mode_change).pack(anchor="w", padx=14)

        _spk_opts = ["none"] + SPECKLE_FILTER_NAMES
        self.batch_uniform_fr = tk.Frame(p, bg=BG)
        self.v_batch_preset  = tk.StringVar(value=_bsaved.get("uniform_preset", PRESET_LABELS["sigma0"]))
        self.v_batch_speckle = tk.StringVar(value=_bsaved.get("uniform_speckle", "Lee Sigma"))
        _u1 = tk.Frame(self.batch_uniform_fr, bg=BG); _u1.pack(fill=tk.X, padx=28, pady=2)
        tk.Label(_u1, text="Pipeline", font=FONT, bg=BG, fg=FG, width=10, anchor="w").pack(side=tk.LEFT)
        self._mk_optmenu(_u1, self.v_batch_preset, list(PRESET_LABELS.values()), width=15).pack(side=tk.LEFT)
        tk.Label(_u1, text="  Speckle", font=FONT, bg=BG, fg=FG).pack(side=tk.LEFT)
        self._mk_optmenu(_u1, self.v_batch_speckle, _spk_opts, width=12).pack(side=tk.LEFT)
        _u2 = tk.Frame(self.batch_uniform_fr, bg=BG); _u2.pack(fill=tk.X, padx=28, pady=2)
        tk.Label(_u2, text="DEM", font=FONT, bg=BG, fg=FG, width=10, anchor="w").pack(side=tk.LEFT)
        self._mk_optmenu(_u2, self.v_batch_dem, DEM_TAG_LIST, width=15).pack(side=tk.LEFT)

        self.batch_all_fr = tk.Frame(p, bg=BG)
        _saved_cp = set(_bsaved.get("all_presets", ["sigma0"]))
        self.v_batch_cp = {k: tk.BooleanVar(value=(k in _saved_cp)) for k in ("sigma0", "gamma0")}
        _c1 = tk.Frame(self.batch_all_fr, bg=BG); _c1.pack(fill=tk.X, padx=28, pady=2)
        tk.Label(_c1, text="Pipelines", font=FONT, bg=BG, fg=FG, width=10, anchor="w").pack(side=tk.LEFT)
        for k in ("sigma0", "gamma0"):
            tk.Checkbutton(_c1, text=PRESET_LABELS[k], variable=self.v_batch_cp[k], bg=BG, font=FONT, fg=FG,
                           selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L).pack(side=tk.LEFT, padx=4)
        _c2 = tk.Frame(self.batch_all_fr, bg=BG); _c2.pack(fill=tk.X, padx=28, pady=2)
        tk.Label(_c2, text="Speckles", font=FONT, bg=BG, fg=FG, width=10, anchor="w").pack(side=tk.LEFT)
        self._btn(_c2, "Select speckle filters…", self._open_batch_speckle_popup,
                  color=BG2).pack(side=tk.LEFT)
        self._lbl_batch_speckle = tk.Label(_c2, text=self._batch_speckle_summary(),
                                            font=(_FONT_FAM, 8, "italic"), bg=BG, fg=FG2)
        self._lbl_batch_speckle.pack(side=tk.LEFT, padx=(6, 0))
        _c3 = tk.Frame(self.batch_all_fr, bg=BG); _c3.pack(fill=tk.X, padx=28, pady=2)
        tk.Label(_c3, text="DEMs", font=FONT, bg=BG, fg=FG, width=10, anchor="w").pack(side=tk.LEFT, anchor="n")
        _cd_grid = tk.Frame(_c3, bg=BG); _cd_grid.pack(side=tk.LEFT)
        for _i, t in enumerate(DEM_TAG_LIST):
            tk.Checkbutton(_cd_grid, text=t, variable=self.v_batch_cd[t], bg=BG, font=FONT, fg=FG,
                           selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L
                           ).grid(row=_i // 4, column=_i % 4, sticky="w", padx=4)

        self.batch_per_fr = tk.Frame(p, bg=BG)
        tk.Label(self.batch_per_fr, text="  (one row per AOI — add AOIs above first).  "
                 "Dates: YYYY-MM-DD, default = Download tab's range.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(anchor="w", padx=28)
        self._btn(self.batch_per_fr, "Apply row 1's dates to all", self._batch_apply_row1_dates,
                  color=BG2).pack(anchor="w", padx=28, pady=(0, 2))
        _ph = tk.Frame(self.batch_per_fr, bg=BG); _ph.pack(fill=tk.X, padx=28)
        tk.Label(_ph, text="AOI", font=(_FONT_FAM, 8), bg=BG, fg=FG2, width=24, anchor="w").pack(side=tk.LEFT)
        tk.Label(_ph, text="pipeline / speckle / DEM / start / end", font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(side=tk.LEFT)
        self.batch_per_rows = tk.Frame(self.batch_per_fr, bg=BG); self.batch_per_rows.pack(fill=tk.X, padx=28)

        # 3. Common settings (shared by every AOI) — bound to the same vars as
        # the Download/Processing tabs, so editing here edits the shared state.
        self._section(p, "3. Common settings (all AOIs)")
        _cs = tk.Frame(p, bg=BG); _cs.pack(fill=tk.X, padx=28, pady=(2, 0))
        tk.Label(_cs, text="Output bands", font=FONT, bg=BG, fg=FG, width=12, anchor="w").pack(side=tk.LEFT)
        def _toggle_batch_bands():
            new = not all(self.v_bands[b].get() for b in ALL_BANDS)
            for b in ALL_BANDS: self.v_bands[b].set(new)
        for b in ALL_BANDS:
            tk.Checkbutton(_cs, text=b, variable=self.v_bands[b], bg=BG, font=FONT, fg=FG,
                           selectcolor=BG2, activebackground=BG, activeforeground=ACCENT_L).pack(side=tk.LEFT)
        self._btn(_cs, "all/none", _toggle_batch_bands, color=BG2, font=(_FONT_FAM, 8)).pack(side=tk.LEFT, padx=(6, 0))

        _cw = tk.Frame(p, bg=BG); _cw.pack(fill=tk.X, padx=28, pady=(4, 0))
        tk.Label(_cw, text="Workers", font=FONT, bg=BG, fg=FG, width=12, anchor="w").pack(side=tk.LEFT)
        for _lbl, _var, _lo, _hi in [("unzip", self.v_unzip_workers, 1, 8),
                                     ("SNAP jobs", self.v_snap_workers, 1, 4),
                                     ("index", self.v_idx_workers, 1, 8)]:
            tk.Label(_cw, text=_lbl, font=(_FONT_FAM, 9), bg=BG, fg=FG2).pack(side=tk.LEFT, padx=(6, 2))
            tk.Spinbox(_cw, from_=_lo, to=_hi, width=3, textvariable=_var, font=FONT,
                       bg=BG2, fg=FG, insertbackground=FG, relief="flat", justify="center").pack(side=tk.LEFT)

        _ck = tk.Frame(p, bg=BG); _ck.pack(fill=tk.X, padx=28, pady=(4, 0))
        self._btn(_ck, "Speckle filter parameters…", self._open_batch_speckle_popup,
                  color=BG2, font=(_FONT_FAM, 9)).pack(side=tk.LEFT)
        tk.Label(_ck, text="  tune per-filter params (applies wherever that filter runs)",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2).pack(side=tk.LEFT)

        # 4. Output base
        self._section(p, "4. Output base folder")
        self.v_batch_out = tk.StringVar(value=_bsaved.get("out", self._saved_cfg.get("batch_out", "")))
        row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=2)
        self._entry(row, self.v_batch_out).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row, "…", lambda: self._browse_dir(self.v_batch_out)).pack(side=tk.LEFT, padx=(4, 0))
        tk.Label(p, text="  Outputs →  <base>/<AOI>/<pipeline_speckle_DEM>/ ;  downloads shared per AOI in\n"
                         "  <base>/<AOI>/_safe/ .  Source, credentials, satellites, orbit and scale (dB/linear)\n"
                         "  come from the Download/Processing tabs.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2, justify="left").pack(anchor="w", padx=14, pady=(0, 6))

        self.btn_batch = self._btn(p, "▶  Run Batch", self._on_run_batch,
                                   pady=10, font=(_FONT_FAM, 12, "bold"))
        self.btn_batch.pack(fill=tk.X, padx=14, pady=(2, 8))
        tk.Label(p, text="  STOP (bottom) halts after the current scene; partial downloads are kept.\n"
                         "  The batch is saved on Run: reopen the app and the same list is here. Finished\n"
                         "  AOIs drop off automatically; unfinished ones stay and resume from disk.",
                 font=(_FONT_FAM, 8), bg=BG, fg=FG2, justify="left").pack(anchor="w", padx=14, pady=(0, 10))

        self._on_batch_mode_change()

    def _batch_add_files(self):
        from tkinter import filedialog as _fd
        paths = _fd.askopenfilenames(title="Select AOI files",
                    filetypes=[("Vector", "*.geojson *.shp *.gpkg"), ("All", "*")])
        for pth in paths:
            if pth and pth not in self._batch_aois:
                self._batch_aois.append(pth)
        self._refresh_batch_listbox()

    def _batch_add_folder(self):
        from tkinter import filedialog as _fd
        d = _fd.askdirectory(title="Folder of AOI files")
        if not d:
            return
        found = []
        for ext in ("*.geojson", "*.shp", "*.gpkg"):
            found += glob.glob(os.path.join(d, ext))
        for pth in sorted(found):
            if pth not in self._batch_aois:
                self._batch_aois.append(pth)
        self._refresh_batch_listbox()

    def _batch_remove(self):
        for i in reversed(list(self.batch_listbox.curselection())):
            del self._batch_aois[i]
        self._refresh_batch_listbox()

    def _batch_clear(self):
        self._batch_aois = []
        self._refresh_batch_listbox()

    def _refresh_batch_listbox(self):
        self.batch_listbox.delete(0, tk.END)
        for pth in self._batch_aois:
            self.batch_listbox.insert(tk.END, os.path.basename(pth))
        if self.v_batch_mode.get() == "per_aoi":
            self._rebuild_batch_per_aoi()

    def _on_batch_mode_change(self):
        for fr in (self.batch_uniform_fr, self.batch_all_fr, self.batch_per_fr):
            fr.pack_forget()
        m = self.v_batch_mode.get()
        if m == "uniform":
            self.batch_uniform_fr.pack(fill=tk.X, pady=(2, 4))
        elif m == "all":
            self.batch_all_fr.pack(fill=tk.X, pady=(2, 4))
        else:
            self.batch_per_fr.pack(fill=tk.X, pady=(2, 4))
            self._rebuild_batch_per_aoi()

    def _rebuild_batch_per_aoi(self):
        for w in self.batch_per_rows.winfo_children():
            w.destroy()
        new_map = {}
        _spk_opts = ["none"] + SPECKLE_FILTER_NAMES
        _dem_default = SNAP_DEM_TAGS.get(self.v_dem.get(), "cop30")
        for pth in self._batch_aois:
            prev = self._batch_per_aoi.get(pth)
            saved = self._batch_saved_per_aoi.get(pth, {})   # from persisted config
            pv = prev[0] if prev else tk.StringVar(value=saved.get("preset", PRESET_LABELS["sigma0"]))
            sv = prev[1] if prev else tk.StringVar(value=saved.get("speckle", "Lee Sigma"))
            stv = prev[2] if prev and len(prev) > 2 else tk.StringVar(value=saved.get("start", self.v_start.get()))
            env = prev[3] if prev and len(prev) > 3 else tk.StringVar(value=saved.get("end", self.v_end.get()))
            dv = prev[4] if prev and len(prev) > 4 else tk.StringVar(value=saved.get("dem", _dem_default))
            new_map[pth] = (pv, sv, stv, env, dv)
            row = tk.Frame(self.batch_per_rows, bg=BG); row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=os.path.basename(pth)[:24], font=(_FONT_FAM, 8), bg=BG, fg=FG,
                     width=24, anchor="w").pack(side=tk.LEFT)
            self._mk_optmenu(row, pv, list(PRESET_LABELS.values()), width=14).pack(side=tk.LEFT)
            self._mk_optmenu(row, sv, _spk_opts, width=11).pack(side=tk.LEFT, padx=3)
            self._mk_optmenu(row, dv, DEM_TAG_LIST, width=8).pack(side=tk.LEFT, padx=3)
            self._entry(row, stv, width=10).pack(side=tk.LEFT, padx=(3, 0))
            self._entry(row, env, width=10).pack(side=tk.LEFT, padx=(3, 0))
            if HAS_CALENDAR:
                self._btn(row, "📅", lambda s=stv, e=env: self._show_calendar_range(s, e),
                          color=BG2, font=(_FONT_FAM, 10)).pack(side=tk.LEFT, padx=(3, 0))
        self._batch_per_aoi = new_map

    def _batch_apply_row1_dates(self):
        if not self._batch_aois:
            return
        first = self._batch_per_aoi.get(self._batch_aois[0])
        if not first or len(first) < 4:
            return
        start, end = first[2].get(), first[3].get()
        for pth in self._batch_aois[1:]:
            row = self._batch_per_aoi.get(pth)
            if row and len(row) >= 4:
                row[2].set(start); row[3].set(end)

    def _batch_cfg(self, aoi_path, preset, speckle, out_base, start_date=None, end_date=None,
                   speckle_params=None, dem=None):
        preset = PRESET_KEYS.get(preset, preset)   # pretty label -> internal key
        dem_tag  = dem or SNAP_DEM_TAGS.get(self.v_dem.get(), "cop30")
        dem_name = SNAP_DEM_BY_TAG.get(dem_tag, SNAP_DEM_DEFAULT)
        aoi_label = re.sub(r"[^A-Za-z0-9_-]", "_", Path(aoi_path).stem) or "AOI"
        # folder suffix from the actual filter name + DEM (e.g. sigma0_lee_sigma_cop30)
        if speckle == "custom" and speckle_params:
            sfx = speckle_params.get("filter", "custom").lower().replace(" ", "_")
        elif speckle in ("none", None, ""):
            sfx = "nospeckle"
        else:
            sfx = speckle
        sub = f"{preset}_{sfx}_{dem_tag}"
        aoi_root = os.path.join(out_base, aoi_label)
        safe_dir = os.path.join(aoi_root, "_safe")            # per-AOI, shared across its combos
        snap_dir = os.path.join(aoi_root, sub, "_snap")
        out_dir  = os.path.join(aoi_root, sub)
        graph_names = {"sigma0": "s1_sigma0_standard.xml", "gamma0": "s1_gamma0_rtc.xml"}
        graph_path  = os.path.join(_SCRIPT_DIR, "snap_graphs", graph_names.get(preset, ""))
        scales = []
        if self.v_linear.get(): scales.append("linear")
        if self.v_db.get():     scales.append("db")
        if not scales:          scales = ["linear"]
        return {
            "aoi_path": aoi_path, "aoi_wkt": None, "aoi_label": aoi_label,
            "start_date": start_date or self.v_start.get(),
            "end_date": end_date or self.v_end.get(),
            "orbit_dir": self.v_orbit.get(),
            "satellites": [s for s, v in self.v_sat.items() if v.get()] or ["S1A", "S1B", "S1C"],
            "asf_token": self.v_token.get(),
            "dl_source": self.v_safe_source.get() if self.v_safe_source.get() in ("cdse", "cdse_s3", "auto") else "asf",
            "cdse_user": self.v_cdse_user.get(), "cdse_pass": self.v_cdse_pass.get(),
            "s3_access": self.v_s3_access.get(), "s3_secret": self.v_s3_secret.get(),
            "do_download": True, "do_snap": True, "do_indices": True,
            "clean_snap": self.v_clean_snap.get(),
            "clean_safe": False,   # keep .SAFE so an AOI's pipeline combos reuse the download
            "bands": {b: self.v_bands[b].get() for b in ALL_BANDS},
            "custom_bands": [
                {"name": n.get().strip(), "expr": e.get().strip()}
                for en, n, e in self.v_custom_bands
                if en.get() and n.get().strip() and e.get().strip()
            ],
            "scales": scales,
            "safe_dir": safe_dir, "safe_out_dir": "", "safe_scratch_gb": "", "snap_dir": snap_dir, "out_dir": out_dir,
            "gpt_path": self.v_gpt.get(),
            "graph_path": graph_path, "graph_preset": preset,
            "speckle": speckle, "speckle_params": speckle_params,
            "dem_name": dem_name, "mosaic_method": self.v_mosaic_method.get(),
            "cluster_aoi": bool(self.v_cluster_aoi.get()),
            "cluster_gap_km": _safe_float(self.v_cluster_gap.get(), 5.0),
            "fields_path": (self.v_fields_path.get() or "").strip(),
            "retry_download": self.v_retry_dl.get(),
            "max_dl_workers": int(self.v_dl_workers.get()),
            "s3_workers": int(self.v_s3_workers.get()),
            "unzip_workers": int(self.v_unzip_workers.get()),
            "unzip_stage_dir": self._unzip_stage_cfg(),
            "max_idx_workers": int(self.v_idx_workers.get()),
            "max_snap_workers": int(self.v_snap_workers.get()),
            "jvm_mb": int(self.v_jvm_mb.get()),
            "gdal_path": self.v_gdal.get().strip(),
            "output_crs": self.v_crs.get().strip(),
        }

    def _save_batch_state(self):
        """Persist the batch setup so reopening the app re-arms it."""
        per_aoi = dict(self._batch_saved_per_aoi)
        for pth, row in self._batch_per_aoi.items():
            if len(row) >= 5:
                pv, sv, stv, env, dv = row
                per_aoi[pth] = {"preset": pv.get(), "speckle": sv.get(),
                                "start": stv.get(), "end": env.get(), "dem": dv.get()}
        per_aoi = {a: per_aoi[a] for a in self._batch_aois if a in per_aoi}
        self._batch_saved_per_aoi = per_aoi
        _save_config({"batch": {
            "aois": list(self._batch_aois),
            "mode": self.v_batch_mode.get(),
            "out": self.v_batch_out.get().strip(),
            "uniform_preset": self.v_batch_preset.get(),
            "uniform_speckle": self.v_batch_speckle.get(),
            "uniform_dem": self.v_batch_dem.get(),
            "all_presets": [k for k, v in self.v_batch_cp.items() if v.get()],
            "all_speckles_on": [n for n in (["None"] + SPECKLE_FILTER_NAMES)
                                if self._batch_speckle_on[n].get()],
            "all_dems": [t for t in DEM_TAG_LIST if self.v_batch_cd[t].get()],
            "speckle_params": self._batch_speckle_params,
            "per_aoi": per_aoi,
        }})

    def _on_run_batch(self):
        if self._running:
            return
        if not self._batch_aois:
            messagebox.showerror("Batch", "Add at least one AOI file (section 1)."); return
        for pth in self._batch_aois:
            if not os.path.isfile(pth):
                messagebox.showerror("Batch", f"AOI not found:\n{pth}"); return
        out_base = self.v_batch_out.get().strip()
        if not out_base:
            messagebox.showerror("Batch", "Set an output base folder (section 3)."); return

        mode = self.v_batch_mode.get()
        g_start, g_end = self.v_start.get(), self.v_end.get()
        # jobs: (aoi, preset, speckle_key, speckle_params, dem_tag, start, end)
        if mode == "uniform":
            sk, sp = self._speckle_spec(self.v_batch_speckle.get())
            dem = self.v_batch_dem.get()
            jobs = [(a, self.v_batch_preset.get(), sk, sp, dem, g_start, g_end)
                    for a in self._batch_aois]
        elif mode == "all":
            presets  = [k for k, v in self.v_batch_cp.items() if v.get()]
            spk_names = [n for n in (["None"] + SPECKLE_FILTER_NAMES)
                         if self._batch_speckle_on[n].get()]
            dems = [t for t in DEM_TAG_LIST if self.v_batch_cd[t].get()]
            if not presets or not spk_names or not dems:
                messagebox.showerror("Batch", "Select at least one pipeline, one speckle filter and one DEM."); return
            speckles = [self._speckle_spec("none" if n == "None" else n,
                                           self._batch_speckle_params.get(n))
                        for n in spk_names]
            jobs = [(a, p, sk, sp, d, g_start, g_end)
                    for a in self._batch_aois for p in presets
                    for (sk, sp) in speckles for d in dems]
        else:  # per_aoi
            jobs = []
            for a in self._batch_aois:
                row = self._batch_per_aoi.get(a)
                if not row:
                    messagebox.showerror("Batch", "Per-AOI table not built — reselect Per-AOI mode."); return
                pv, sv, stv, env, dv = row
                sk, sp = self._speckle_spec(sv.get())
                jobs.append((a, pv.get(), sk, sp, dv.get(), stv.get(), env.get()))

        if not messagebox.askyesno("Run batch",
                f"Run {len(jobs)} job(s) across {len(self._batch_aois)} AOI(s)?\n\n"
                f"Mode: {mode}\nOutput base: {out_base}\n\n"
                "AOIs run sequentially — use STOP to halt after the current one."):
            return

        self._save_batch_state()

        # begin run state (mirrors _on_start)
        self._running = True
        self._stopping = False
        self._stop_event.clear()
        self._force_event.clear()
        with self._proc_lock:
            self._cur_procs.clear()
        self.btn_run.configure(state=tk.DISABLED, bg="#455A64")
        self.btn_batch.configure(state=tk.DISABLED, bg="#455A64")
        self.btn_stop.configure(state=tk.NORMAL, bg=RED)
        self.progress.start(12)
        self._reset_all_progress()
        self._pipeline_start = time.time()
        self._phase_starts = {}
        self._tick_elapsed()

        def _set_proc(pr):
            with self._proc_lock:
                if pr is not None:
                    self._cur_procs.add(pr)
                else:
                    self._cur_procs = {q for q in self._cur_procs if q.poll() is None}

        self._thread = threading.Thread(
            target=self._batch_worker, args=(jobs, out_base, _set_proc), daemon=True)
        self._thread.start()

    def _batch_worker(self, jobs, out_base, set_proc):
        ok_all = True
        total = len(jobs)
        # track completion per AOI so fully-finished AOIs can drop off the saved batch
        jobs_per_aoi = {}
        for j in jobs:
            jobs_per_aoi[j[0]] = jobs_per_aoi.get(j[0], 0) + 1
        ok_per_aoi = {a: 0 for a in jobs_per_aoi}
        for idx, (aoi, preset, speckle, speckle_params, dem, start, end) in enumerate(jobs, 1):
            if self._stop_event.is_set():
                self._log(f"\n[Batch stopped — {idx-1}/{total} job(s) completed]")
                break
            spk_lbl = (speckle_params.get("filter") if speckle == "custom" and speckle_params
                       else speckle)
            label = f"{Path(aoi).stem} · {preset}" + ("" if speckle == "none" else f"/{spk_lbl}") + f" · {dem}"
            self._log("\n" + "#" * 62)
            self._log(f"# BATCH {idx}/{total}:  {label}  [{start} → {end}]")
            self._log("#" * 62)
            cfg = self._batch_cfg(aoi, preset, speckle, out_base, start, end,
                                  speckle_params=speckle_params, dem=dem)
            cfg["stop_event"]  = self._stop_event
            cfg["force_event"] = self._force_event
            cfg["set_proc_cb"] = set_proc
            for _d in (cfg["safe_dir"], cfg["snap_dir"], cfg["out_dir"]):
                try: os.makedirs(_d, exist_ok=True)
                except Exception: pass
            _res = {"ok": False}
            try:
                run_pipeline(cfg, self._log,
                             (lambda ok, r=_res: r.__setitem__("ok", ok)),
                             self._update_progress)
            except Exception as e:
                self._log(f"[BATCH job error] {e}")
            if _res["ok"]:
                ok_per_aoi[aoi] += 1
            else:
                ok_all = False
            self.after(0, self._reset_all_progress)
        # prune AOIs whose every job finished ok; keep partial/unrun ones for resume
        finished = {a for a, n in jobs_per_aoi.items() if ok_per_aoi.get(a, 0) == n}
        if finished:
            self.after(0, lambda f=finished: self._batch_prune_finished(f))
        self._on_done(ok_all)

    def _batch_prune_finished(self, finished):
        remaining = [a for a in self._batch_aois if a not in finished]
        if remaining == self._batch_aois:
            return
        self._batch_aois = remaining
        self._refresh_batch_listbox()
        self._save_batch_state()
        self._log(f"[Batch] {len(finished)} finished AOI(s) removed from the saved batch; "
                  f"{len(remaining)} remain.")

    def _on_start(self, retry_dates=None):
        if self._running:
            return

        # pre-flight: block if critical dependencies are missing
        if self._dep_results:
            critical = ["SNAP GPT", "Python packages"]
            missing_critical = [
                name for name in critical
                if self._dep_results.get(name, (False,))[0] is False
            ]
            if missing_critical:
                msg = "Cannot start pipeline — missing critical dependencies:\n\n"
                for name in missing_critical:
                    detail = self._dep_results[name][1]
                    msg += f"  {name}:\n"
                    for line in detail.split("\n")[:4]:
                        msg += f"    {line}\n"
                msg += "\nFix the issues shown in the Dependencies panel and try again."
                messagebox.showerror("Missing Dependencies", msg)
                return

        scales = []
        if self.v_linear.get(): scales.append("linear")
        if self.v_db.get():     scales.append("db")
        if not scales:
            messagebox.showwarning("Scale", "Select at least one output scale.")
            return
        _has_custom_bands = any(
            en.get() and name.get().strip() and expr.get().strip()
            for en, name, expr in self.v_custom_bands
        )
        if not any(self.v_bands[b].get() for b in ALL_BANDS) and not _has_custom_bands:
            messagebox.showwarning("Bands",
                "Select at least one preset band or define a Custom Band.")
            return

        # validate dates (YYYY-MM-DD, start ≤ end) — a bad range otherwise fails
        # silently as "0 days" or throws on the UI thread (S3).
        # Skipped on a retry run — dates come from the logged error files.
        if not retry_dates:
            import datetime as _dt
            try:
                _sd = _dt.datetime.strptime(self.v_start.get().strip(), "%Y-%m-%d").date()
                _ed = _dt.datetime.strptime(self.v_end.get().strip(), "%Y-%m-%d").date()
            except ValueError:
                messagebox.showerror("Dates", "Dates must be in YYYY-MM-DD format.")
                return
            if _sd > _ed:
                messagebox.showerror("Dates", "Start date must be on or before the end date.")
                return

        # validate required paths
        required_dirs = []
        if self.v_do_snap.get() or self.v_do_indices.get():
            required_dirs.append(("SNAP GeoTIFF folder — step 2 output (section 10)", self.v_snap_dir))
        if self.v_do_indices.get():
            required_dirs.append(("COG indices folder — step 3 output (section 10)", self.v_out_dir))
        if self.v_safe_source.get() in ("download", "cdse", "cdse_s3", "auto") and self.v_do_dl.get():
            required_dirs.append(("Raw .SAFE folder — step 1 output (section 10)", self.v_safe_dir))
        for label, var in required_dirs:
            if not var.get().strip():
                messagebox.showerror("Missing path", f"Please set: {label}")
                return

        # resolve graph path from preset
        _preset = self.v_graph_preset.get()
        _graph_names = {
            "sigma0": "s1_sigma0_standard.xml",
            "gamma0": "s1_gamma0_rtc.xml",
        }
        if _preset == "custom":
            _resolved_graph = self.v_graph.get().strip()
            if self.v_do_snap.get() and not _resolved_graph:
                messagebox.showerror("Missing path",
                    "Please select a custom SNAP graph XML in section 2.")
                return
        else:
            _resolved_graph = os.path.join(
                _SCRIPT_DIR, "snap_graphs", _graph_names[_preset])
            if self.v_do_snap.get() and not os.path.isfile(_resolved_graph):
                messagebox.showerror("Graph missing",
                    f"Built-in graph not found:\n{_resolved_graph}\n\n"
                    "Make sure the snap_graphs/ folder is next to this script.")
                return

        # if using existing .SAFE folder, override safe_dir and force skip download
        using_existing      = self.v_safe_source.get() == "existing"
        using_existing_zip  = self.v_safe_source.get() == "existing_zip"
        using_existing_snap = self.v_safe_source.get() == "existing_snap"

        if using_existing or using_existing_zip:
            existing_path = self.v_existing_safe.get().strip()
            if not existing_path:
                existing_path = self.v_safe_dir.get().strip()
                if existing_path:
                    self.v_existing_safe.set(existing_path)
            existing_path = os.path.normpath(existing_path) if existing_path else ""
            if not existing_path:
                messagebox.showerror("Existing .SAFE folder",
                    "Please select the folder that contains your .SAFE files.\n\n"
                    "Use the Browse button in the Download tab, or set the\n"
                    "'Raw .SAFE folder' path in the Output tab.")
                return
            if not os.path.isdir(existing_path):
                messagebox.showerror("Existing .SAFE folder",
                    f"Folder not found:\n{existing_path}\n\n"
                    "Make sure the path is correct and the drive is connected.")
                return

        if using_existing_snap:
            existing_snap_path = self.v_existing_snap.get().strip()
            if not existing_snap_path:
                existing_snap_path = self.v_snap_dir.get().strip()
                if existing_snap_path:
                    self.v_existing_snap.set(existing_snap_path)
            existing_snap_path = os.path.normpath(existing_snap_path) if existing_snap_path else ""
            if not existing_snap_path:
                messagebox.showerror("Existing GeoTIFF folder",
                    "Please select the folder that contains your SNAP GeoTIFF files.\n\n"
                    "Use the Browse button in the Download tab, or set the\n"
                    "'SNAP GeoTIFF folder' path in the Output tab.")
                return
            if not os.path.isdir(existing_snap_path):
                messagebox.showerror("Existing GeoTIFF folder",
                    f"Folder not found:\n{existing_snap_path}\n\n"
                    "Make sure the path is correct and the drive is connected.")
                return

        # always save paths + CRS + graph preset for next session
        _save_config({
            "aoi_path":       self.v_aoi.get(),
            "start_date":     self.v_start.get(),
            "end_date":       self.v_end.get(),
            "output_crs":     self.v_crs.get(),
            "safe_dir":       self.v_safe_dir.get(),
            "safe_out_dir":   self.v_safe_out_dir.get(),
            "safe_scratch_gb": self.v_safe_scratch_gb.get(),
            "snap_dir":       self.v_snap_dir.get(),
            "out_dir":        self.v_out_dir.get(),
            "graph_preset":   self.v_graph_preset.get(),
            "graph_custom":   self.v_graph.get(),
            "speckle":        self.v_speckle.get(),
            "speckle_params": self._speckle_custom_params,
            "dem_name":       self.v_dem.get(),
            "mosaic_method":  self.v_mosaic_method.get(),
            "cluster_aoi":     bool(self.v_cluster_aoi.get()),
            "cluster_gap_km":  _safe_float(self.v_cluster_gap.get(), 5.0),
            "fields_path":     (self.v_fields_path.get() or "").strip(),
            "retry_download":    self.v_retry_dl.get(),
            "max_dl_workers":    int(self.v_dl_workers.get()),
            "s3_workers":        int(self.v_s3_workers.get()),
            "satellites":        [s for s, v in self.v_sat.items() if v.get()] or ["S1A", "S1B", "S1C"],
            "unzip_workers":     int(self.v_unzip_workers.get()),
            "unzip_stage_dir":   self._unzip_stage_cfg(),
            "max_idx_workers":   int(self.v_idx_workers.get()),
            "max_snap_workers":  int(self.v_snap_workers.get()),
            "jvm_mb":            int(self.v_jvm_mb.get()),
        })
        # save credentials if user wants to remember them (plain text — includes
        # the CDSE password at the user's request; relaxes S7)
        if self.v_remember.get():
            _save_config({
                "asf_token":      self.v_token.get(),
                "cdse_user":      self.v_cdse_user.get(),
                "cdse_pass":      self.v_cdse_pass.get(),
                "s3_access":      self.v_s3_access.get(),
                "s3_secret":      self.v_s3_secret.get(),
                "remember_creds": True,
            })

        cfg = {
            "aoi_path":    self.v_aoi.get(),
            "aoi_wkt":     None,  # derived from AOI file below
            "aoi_label":   re.sub(r"[^A-Za-z0-9_-]", "_", Path(self.v_aoi.get()).stem) if self.v_aoi.get() else "AOI",
            "start_date":  self.v_start.get(),
            "end_date":    self.v_end.get(),
            "orbit_dir":   self.v_orbit.get(),
            "satellites":  [s for s, v in self.v_sat.items() if v.get()] or ["S1A", "S1B", "S1C"],
            "asf_token":   self.v_token.get(),
            "dl_source":   self.v_safe_source.get() if self.v_safe_source.get() in ("cdse", "cdse_s3", "auto") else "asf",
            "cdse_user":   self.v_cdse_user.get(),
            "cdse_pass":   self.v_cdse_pass.get(),
            "s3_access":   self.v_s3_access.get(),
            "s3_secret":   self.v_s3_secret.get(),
            "do_download": False if (using_existing or using_existing_zip or using_existing_snap) else self.v_do_dl.get(),
            "do_snap":     False if using_existing_snap else self.v_do_snap.get(),
            "do_indices":  self.v_do_indices.get(),
            "clean_snap":  self.v_clean_snap.get(),
            "clean_safe":  self.v_clean_safe.get(),
            "bands":       {b: self.v_bands[b].get() for b in ALL_BANDS},
            "custom_bands": [
                {"name": n.get().strip(), "expr": e.get().strip()}
                for en, n, e in self.v_custom_bands
                if en.get() and n.get().strip() and e.get().strip()
            ],
            "scales":      scales,
            "safe_dir":    existing_path if (using_existing or using_existing_zip) else self.v_safe_dir.get(),
            "safe_out_dir": "" if using_existing else self.v_safe_out_dir.get(),
            "safe_scratch_gb": self.v_safe_scratch_gb.get(),
            "snap_dir":    existing_snap_path if using_existing_snap else self.v_snap_dir.get(),
            "out_dir":     self.v_out_dir.get(),
            "gpt_path":    self.v_gpt.get(),
            "graph_path":      _resolved_graph,
            "graph_preset":    self.v_graph_preset.get(),
            "speckle":         self.v_speckle.get(),
            "speckle_params":  self._speckle_custom_params,
            "dem_name":        self.v_dem.get(),
            "mosaic_method":  self.v_mosaic_method.get(),
            "cluster_aoi":     bool(self.v_cluster_aoi.get()),
            "cluster_gap_km":  _safe_float(self.v_cluster_gap.get(), 5.0),
            "fields_path":     (self.v_fields_path.get() or "").strip(),
            "retry_download":    self.v_retry_dl.get(),
            "max_dl_workers":    int(self.v_dl_workers.get()),
            "s3_workers":        int(self.v_s3_workers.get()),
            "unzip_workers":     int(self.v_unzip_workers.get()),
            "unzip_stage_dir":   self._unzip_stage_cfg(),
            "max_idx_workers":   int(self.v_idx_workers.get()),
            "max_snap_workers":  int(self.v_snap_workers.get()),
            "jvm_mb":            int(self.v_jvm_mb.get()),
            "gdal_path":         self.v_gdal.get().strip(),
            "output_crs":  self.v_crs.get().strip(),
        }
        if retry_dates:
            # Retry only the failed dates. Force the download step on (the whole
            # point) and filter the scene search to those dates; SNAP/indices
            # follow whatever is checked and skip scenes already processed.
            cfg["retry_dates"] = set(retry_dates)
            cfg["do_download"] = True

        if not cfg["aoi_path"] or not os.path.isfile(cfg["aoi_path"]):
            messagebox.showerror("AOI file missing",
                "Please select an AOI file (.shp, .gpkg or .geojson) in section 3.")
            return
        _fields_path = (self.v_fields_path.get() or "").strip()
        if _fields_path and not os.path.isfile(_fields_path):
            messagebox.showerror("Fields file", f"Fields file not found:\n{_fields_path}")
            return
        _gap  = _safe_float(self.v_cluster_gap.get(), 5.0)
        # The 3b checkbox is authoritative: deselected = process the full AOI,
        # even when a fields file is set (it used to force cluster mode on).
        _want = bool(self.v_cluster_aoi.get())
        if _fields_path and not _want:
            self._log("[fields] fields file set but 'separate clusters' (3b) is "
                      "off — processing the full AOI, no field masking")

        # The AOI read + union + field clustering can take several seconds on a
        # big AOI. Run it off the Tk thread so the window doesn't go "Not
        # Responding" (S2), then resume on the main thread for dialogs + launch.
        self.btn_run.configure(state=tk.DISABLED, bg="#455A64")
        self._log("Preparing AOI …")

        def _prep():
            out = {"logs": [], "clusters": [], "coverage": 1.0, "npoly": 1}
            try:
                import geopandas as gpd
                gdf = gpd.read_file(cfg["aoi_path"]).to_crs("EPSG:4326")
                union = _safe_union(gdf)
                out["union_wkt"] = union.wkt
            except Exception as e:
                out["error"] = ("AOI", f"Cannot read AOI file:\n{e}")
                return out
            try:
                if _fields_path:
                    _csrc = _read_fields(_fields_path).to_crs("EPSG:4326")
                    try: _csrc["geometry"] = _csrc.geometry.make_valid()
                    except Exception: _csrc["geometry"] = _csrc.geometry.buffer(0)
                    _total = len(_csrc)
                    _sel = _csrc[_csrc.intersects(union)]
                    if len(_sel) == 0:
                        out["logs"].append("[fields] no field polygons fall inside "
                                           "the AOI — using whole AOI")
                    else:
                        _csrc = _sel
                        out["logs"].append(f"[fields] {len(_csrc)} of {_total} field "
                                           "polygons fall inside the AOI")
                else:
                    _csrc = gdf
                out["npoly"] = int(len(_csrc))
            except Exception as _ce:
                _csrc, out["npoly"] = gdf, 1
                out["logs"].append(f"[fields] could not read fields file: {_ce}")
            if out["npoly"] > 1:
                try:
                    out["clusters"], out["coverage"] = _cluster_polygons(
                        _csrc, max_gap_km=_gap)
                except Exception as _ce:
                    out["logs"].append(f"[fields] clustering skipped: {_ce}")
            return out

        def _prep_thread():
            res = _prep()
            self.after(0, lambda: self._after_prep(cfg, res, _fields_path, _gap, _want))
        threading.Thread(target=_prep_thread, daemon=True).start()

    def _after_prep(self, cfg, res, _fields_path, _gap, _want):
        """Back on the Tk main thread after the heavy AOI prep (S2)."""
        for m in res["logs"]:
            self._log(m)
        if res.get("error"):
            title, msg = res["error"]
            messagebox.showerror(title, msg)
            self.btn_run.configure(state=tk.NORMAL, bg=ACCENT)
            return

        union_wkt = res["union_wkt"]
        cfg["aoi_wkt"]     = union_wkt
        cfg["sub_aois"]    = [{"tag": "", "wkt": union_wkt}]
        cfg["fields_path"] = ""
        _npoly, _clusters, _coverage = res["npoly"], res["clusters"], res["coverage"]

        if _npoly > 1 and _want:
            if _clusters:
                cfg["sub_aois"] = _clusters
            if _fields_path:
                cfg["fields_path"] = _fields_path
                self._log(f"[fields] {len(_clusters)} cluster(s) from {_npoly} field "
                          f"polygons (gap <= {_gap:g} km); output masked to field shapes")
            else:
                self._log(f"[fields] {len(_clusters)} cluster(s) from {_npoly} AOI "
                          f"polygons (gap <= {_gap:g} km)")
        elif _npoly > 1 and len(_clusters) > 1 and _coverage < 0.30:
            _go = messagebox.askyesno(
                "Scattered AOI detected",
                f"This AOI has {_npoly} polygons covering only "
                f"{_coverage*100:.1f}% of their bounding box, across "
                f"{len(_clusters)} separate areas.\n\n"
                f"Processing them as one AOI wastes compute and storage on the "
                f"empty space between fields.\n\n"
                f"Process each cluster as its own tight bounding box "
                f"(recommended)?\n\nMake it the default in section 3b.")
            if _go:
                cfg["sub_aois"] = _clusters
                self.v_cluster_aoi.set(True)
                self._log(f"[fields] enabled — {len(_clusters)} clusters")

        # Confirm raw .SAFE deletion (even if the checkbox is ticked) — the
        # files are ~4 GB/scene and must be re-downloaded if deleted.
        if cfg.get("clean_safe"):
            if not messagebox.askyesno(
                    "Delete raw .SAFE files?",
                    "After processing, the raw .SAFE download files will be DELETED "
                    "(~4 GB/scene) to free space.\n\nThey must be re-downloaded if you "
                    "re-run later.\n\nDelete them?   (No = keep the .SAFE files)"):
                cfg["clean_safe"] = False
                self._log("Keeping raw .SAFE files (deletion declined).")
        self._running = True
        self._stopping = False
        self._stop_event.clear()
        self._force_event.clear()
        with self._proc_lock:
            self._cur_procs.clear()
        self.btn_run.configure(state=tk.DISABLED, bg="#455A64")
        self.btn_stop.configure(state=tk.NORMAL, bg=RED)
        self.progress.start(12)
        self._reset_all_progress()
        self._pipeline_start = time.time()
        self._phase_starts   = {}
        self._tick_elapsed()          # start the live 1-second elapsed ticker
        cfg["stop_event"] = self._stop_event
        cfg["force_event"] = self._force_event
        self._last_cfg = cfg.copy()
        def _set_proc(p):
            with self._proc_lock:
                if p is not None:
                    self._cur_procs.add(p)
                else:                       # prune finished processes
                    self._cur_procs = {q for q in self._cur_procs if q.poll() is None}
        cfg["set_proc_cb"] = _set_proc
        self._thread = threading.Thread(
            target=run_pipeline,
            args=(cfg, self._log, self._on_done, self._update_progress),
            daemon=True)
        self._thread.start()

    def _on_retry_failed(self):
        """Re-run only the dates whose download failed in the last run."""
        if self._running:
            return
        base = (self.v_out_dir.get().strip() or self.v_snap_dir.get().strip()
                or self.v_safe_dir.get().strip())
        if not base:
            messagebox.showerror("Output missing", "Set an output folder first.")
            return
        edir = os.path.join(base, "pipeline_errors")
        # Download-phase logs are named "<sceneName>__download*.error.txt"; the
        # acquisition date is embedded in the scene name as _YYYYMMDDTHHMMSS_.
        dates = set()
        for p in glob.glob(os.path.join(edir, "*__download*.error.txt")):
            m = re.search(r"_(\d{8})T\d{6}", os.path.basename(p))
            if m:
                d = m.group(1)
                dates.add(f"{d[:4]}-{d[4:6]}-{d[6:]}")
        if not dates:
            messagebox.showinfo("Retry failed downloads",
                "No failed downloads logged — nothing to retry.")
            return
        dates = sorted(dates)
        if not messagebox.askyesno("Retry failed downloads",
                f"Re-download {len(dates)} failed date(s)?\n\n" + "\n".join(dates)):
            return
        self._on_start(retry_dates=dates)

    def _on_window_close(self):
        if getattr(self, "_running", False):
            if not messagebox.askyesno(
                    "Processing in progress",
                    "The pipeline is still running.\n\n"
                    "Closing now will stop it and kill any running SNAP "
                    "subprocess(es). Are you sure you want to quit?",
                    icon="warning", default="no"):
                return
            try: self._on_stop(graceful=False)
            except Exception: pass
        self.destroy()

    def _on_stop(self, graceful=True):
        # Graceful (default): stop launching new scenes, but let the SNAP run(s)
        # already in flight finish and publish — avoids killing minutes of work
        # and leaving a half-done group. The worker thread drains and calls
        # _on_done, which re-enables Run. A 2nd Stop press force-kills.
        if graceful and not getattr(self, "_stopping", False):
            self._stopping = True
            self._stop_event.set()
            self._log("\n[Stopping — finishing the scene(s) already running; no new "
                      "scenes will start. Press Stop again to force-kill.]")
            return
        # Force stop: 2nd Stop press, or window close.
        self._log("\n[Force-stopping — killing running subprocess(es)…]")
        self._running = False
        self._stopping = False
        self._stop_event.set()
        self._force_event.set()   # also abort any in-flight CDSE download mid-scene
        # Kill ALL running SNAP/GPT subprocesses (covers parallel workers)
        with self._proc_lock:
            _procs = [q for q in self._cur_procs if q.poll() is None]
        for q in _procs:
            try: q.kill()
            except Exception: pass
        # Reset UI so user can re-run
        def _reset():
            self.btn_run.configure(state=tk.NORMAL, bg=ACCENT)
            if hasattr(self, "btn_batch"):
                self.btn_batch.configure(state=tk.NORMAL, bg=ACCENT)
            self.btn_stop.configure(state=tk.DISABLED, bg="#455A64")
            self.progress.stop()
        self.after(0, _reset)

    def _on_done(self, success):
        def _update():
            self._running = False
            self._stopping = False
            self.btn_run.configure(state=tk.NORMAL, bg=ACCENT)
            if hasattr(self, "btn_batch"):
                self.btn_batch.configure(state=tk.NORMAL, bg=ACCENT)
            self.btn_stop.configure(state=tk.DISABLED, bg="#455A64")
            self.progress.stop()
            # show final elapsed; clear ETA
            if hasattr(self, "_pipeline_start") and self._pipeline_start:
                elapsed = time.time() - self._pipeline_start
                def _fmt(s):
                    s = int(s)
                    if s < 60:   return f"{s}s"
                    if s < 3600: return f"{s//60}m {s%60:02d}s"
                    return f"{s//3600}h {(s%3600)//60:02d}m"
                if hasattr(self, "_elapsed_var"):
                    _status = "✓ Done" if success else "■ Stopped"
                    self._elapsed_var.set(f"Total: {_fmt(elapsed)}   ·   {_status}")
            if hasattr(self, "_eta_var"):
                self._eta_var.set("")

            # ── Completion notification (Windows beep) ──────────────
            try:
                import winsound
                if success:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                else:
                    winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                pass

            # ── Run history ─────────────────────────────
            try:
                import json as _json
                from datetime import datetime as _dt
                hist_path = os.path.join(_SCRIPT_DIR, "sar_foundry_history.json")
                history = []
                if os.path.isfile(hist_path):
                    try:
                        history = _json.loads(open(hist_path, encoding="utf-8").read())
                    except Exception:
                        history = []
                cfg_snap = getattr(self, "_last_cfg", {})
                entry = {
                    "timestamp":  _dt.now().strftime("%Y-%m-%d %H:%M"),
                    "success":    success,
                    "aoi":        os.path.basename(cfg_snap.get("aoi_path","?")),
                    "start_date": cfg_snap.get("start_date","?"),
                    "end_date":   cfg_snap.get("end_date","?"),
                    "orbit":      cfg_snap.get("orbit_dir","?"),
                    "speckle":    cfg_snap.get("speckle","?"),
                    "dem":        cfg_snap.get("dem_name","?"),
                }
                history.insert(0, entry)
                history = history[:50]  # keep last 50 runs
                with open(hist_path, "w", encoding="utf-8") as _f:
                    _json.dump(history, _f, indent=2)
            except Exception:
                pass

            # ── Output summary ──────────────────────
            if success:
                try:
                    cfg_snap = getattr(self, "_last_cfg", {})
                    out_dir = cfg_snap.get("out_dir","")
                    snap_dir = cfg_snap.get("snap_dir","")
                    lines = ["-"*40, "OUTPUT SUMMARY"]
                    for label, folder in [("GeoTIFFs (SNAP)", snap_dir),
                                           ("Indices (COG)",   out_dir)]:
                        if folder and os.path.isdir(folder):
                            tifs = [f for f in os.listdir(folder) if f.endswith(".tif")]
                            for root2, dirs, files in os.walk(folder):
                                tifs += [f for f in files if f.endswith(".tif")
                                         and root2 != folder]
                            total_mb = sum(
                                os.path.getsize(os.path.join(r,f))
                                for r,ds,fs in os.walk(folder)
                                for f in fs if f.endswith(".tif")
                            ) / 1e6
                            lines.append(f"  {label}: {len(tifs)} files  ({total_mb:.0f} MB)")
                    lines.append("-"*40)
                    for line in lines:
                        self._log(line)
                except Exception:
                    pass
        self.after(0, _update)


# ===============================================================================
# MAIN
# ===============================================================================

if __name__ == "__main__":
    app = App()
    app.mainloop()
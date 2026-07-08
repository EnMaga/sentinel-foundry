"""
s2_pipeline_ui.py — Optical Foundry
=====================================
GUI for Sentinel-2 L2A download and processing using AWS sentinel-cogs
(EarthSearch, no credentials needed) + satellitetools biophysical NN.

Pipeline per AOI + date range:
  1. Download S2 L2A from AWS EarthSearch (sentinel-2-l2a-cogs, public)
  2. Apply SCL cloud mask
  3. Compute and save as COG GeoTIFFs at 10 m UTM:

     Biophysical (pure Python, no SNAP):
       LAI, CCC, CWC, FAPAR, FCOVER

     Spectral bands (reflectance 0–1):
       B2, B3, B4, B5, B6, B7, B8, B8A, B11, B12

     Spectral indices:
       NDVI, NDWI, NDII, MSAVI2, CIRE, EVI, NDRE1, MTCI

Run:
    python s2_pipeline_ui.py
    (venv created and packages installed automatically on first run)
"""

import os, sys

_here = os.path.dirname(os.path.abspath(__file__))

def _find_venv_py():
    win = os.path.join(_here, ".venv", "Scripts", "python.exe")
    nix = os.path.join(_here, ".venv", "bin", "python")
    if os.path.isfile(win): return win
    if os.path.isfile(nix): return nix
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
    _venv_py = _find_venv_py()
    req = os.path.join(_here, "requirements_s2.txt")
    if os.path.isfile(req):
        import subprocess, threading
        print("[SETUP] Installing packages (first run) ...")
        _done = [False]
        def _spin():
            import time
            chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            i = 0
            while not _done[0]:
                print(f"\r  {chars[i % len(chars)]} installing...", end="", flush=True)
                i += 1; time.sleep(0.1)
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

# ── stdlib imports ────────────────────────────────────────────────────────────
import re, glob, shutil, subprocess, threading, time, json

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
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    from tkcalendar import DateEntry as _DateEntry
    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

# ── dark theme palette ────────────────────────────────────────────────────────
BG        = "#1A1A2E"   # form panel background
BG2       = "#252542"   # card / section background
SURFACE   = "#0F0F1F"   # log & deep background
ACCENT    = "#2196F3"   # electric blue (primary)
ACCENT2   = "#1565C0"   # darker blue (hover / banner start)
ACCENT_L  = "#90CAF9"   # light blue (secondary text)
FG        = "#E8EAED"   # primary text
FG2       = "#78909C"   # secondary / hint text
GREEN     = "#66BB6A"   # success
GOLD      = "#FFA726"   # warning
RED       = "#EF5350"   # error
WHITE     = "#FFFFFF"
# legacy aliases kept so existing code doesn't break
DARK      = BG2
BLUE      = ACCENT
TEAL      = ACCENT2
LTBLUE    = ACCENT_L
_FONT_FAM = "Segoe UI"
FONT      = (_FONT_FAM, 10)
FONT_BOLD = (_FONT_FAM, 10, "bold")
FONT_MONO = ("Consolas", 9)

NODATA = -9999.0

# Sentinel returned by _process_day when a tile footprint overlaps the AOI but no
# pixels actually cover it (swath/orbit edge). Used verbatim so _run_day can
# collapse N identical per-cluster results into one line.
_NO_PIXELS_MSG = ("tile footprint matched but no pixels cover this AOI on this date "
                  "(no S2 acquisition here — try a wider date range; S2 revisits "
                  "every ~5 days)")

_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_SCRIPT_DIR, "optical_foundry_config.json")

def _load_config():
    if os.path.isfile(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f: return json.load(f)
        except Exception as e:
            print(f"[CONFIG] Could not read {_CONFIG_FILE} ({e}); using defaults")
    return {}

def _save_config(data):
    try:
        ex = _load_config(); ex.update(data)
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(ex, f, indent=2)
    except Exception as e: print(f"[CONFIG] {e}")


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

# ── output groups ─────────────────────────────────────────────────────────────
BIOPHYS = ["LAI", "CCC", "CWC", "FAPAR", "FCOVER"]
BANDS   = ["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
INDICES = ["NDVI", "NDWI", "NDII", "MSAVI2", "CIRE", "EVI", "NDRE1", "MTCI"]

BIOPHYS_DESC = {
    "LAI":    "Leaf Area Index (m²/m²)",
    "CCC":    "Canopy Chlorophyll Content (µg/cm²)",
    "CWC":    "Canopy Water Content (g/cm²)",
    "FAPAR":  "Fraction of Absorbed PAR (0–1)",
    "FCOVER": "Fraction of Vegetation Cover (0–1)",
}
INDICES_DESC = {
    "NDVI":   "(NIR-RED)/(NIR+RED)",
    "NDWI":   "(GREEN-NIR)/(GREEN+NIR)",
    "NDII":   "(NIR-SWIR1)/(NIR+SWIR1)",
    "MSAVI2": "Modified Soil-Adjusted VI",
    "CIRE":   "Chlorophyll Index Red-Edge",
    "EVI":    "Enhanced Vegetation Index",
    "NDRE1":  "Red-Edge Normalised Diff",
    "MTCI":   "MERIS Terrestrial Chlorophyll Index",
}


# ═══════════════════════════════════════════════════════════════════════════
# ERROR LOGGING HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _error_dir(cfg):
    """Return the folder where per-day error logs are written."""
    base = cfg.get("out_dir", ".")
    return os.path.join(base, "pipeline_errors")

def _write_error(cfg, date_str, phase, message):
    """Write a per-day error log so the user knows what to retry."""
    edir = _error_dir(cfg)
    os.makedirs(edir, exist_ok=True)
    fname = os.path.join(edir, f"S2_{date_str}__{phase}.error.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(f"Optical Foundry — Error Log\n{'='*60}\n")
        f.write(f"Phase  : {phase}\n")
        f.write(f"Date   : {date_str}\n")
        f.write(f"Time   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"\nError:\n{message}\n")
        f.write(f"\n{'='*60}\n")
        f.write("To retry: re-run the pipeline covering this date.\n")


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE LOGIC
# ═══════════════════════════════════════════════════════════════════════════

import ast as _ast

# Band expressions typed by the user are eval'd. {"__builtins__": {}} is NOT a
# security sandbox (bypassable); real safety comes from validating the AST against
# this allowlist first, which also turns a typo into a clear error not a silent
# no-output (S1).
_EXPR_NODES = (
    _ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.BoolOp, _ast.Compare,
    _ast.IfExp, _ast.Call, _ast.Attribute, _ast.Name, _ast.Load,
    _ast.Constant, _ast.Tuple, _ast.List, _ast.Subscript, _ast.Slice,
    _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.FloorDiv, _ast.Mod, _ast.Pow,
    _ast.USub, _ast.UAdd, _ast.And, _ast.Or, _ast.Not,
    _ast.Eq, _ast.NotEq, _ast.Lt, _ast.LtE, _ast.Gt, _ast.GtE,
    _ast.BitAnd, _ast.BitOr, _ast.BitXor,
)


def _safe_float(text, default):
    """float(text) or default — never raises on blank/garbage input (S4)."""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


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


def _reproject_outputs(out_dir: str, target_crs: str, log) -> None:
    """Reproject all COG GeoTIFFs in out_dir to target_crs in-place, keeping the
    COG format (S10 — a plain GTiff here silently broke the promised COG output)."""
    import glob, tempfile, shutil
    import numpy as np
    import rasterio
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    tifs = glob.glob(os.path.join(out_dir, "**", "*.tif"), recursive=True)
    if not tifs: return
    log(f"  Reprojecting {len(tifs)} files to {target_crs} ...")
    done = 0
    for tif in tifs:
        try:
            with rasterio.open(tif) as src_r:
                if str(src_r.crs).upper() == target_crs.upper(): continue
                transform, width, height = calculate_default_transform(
                    src_r.crs, target_crs, src_r.width, src_r.height, *src_r.bounds)
                dtype = src_r.dtypes[0]
                # COG is write-once (builds overviews on close), so reproject into
                # an array first, then write the whole COG in one shot.
                dst_arr = np.empty((src_r.count, height, width), dtype=dtype)
                for i in range(1, src_r.count + 1):
                    reproject(source=rasterio.band(src_r, i),
                              destination=dst_arr[i - 1],
                              src_transform=src_r.transform, src_crs=src_r.crs,
                              dst_transform=transform, dst_crs=target_crs,
                              src_nodata=src_r.nodata, dst_nodata=src_r.nodata,
                              resampling=Resampling.bilinear)
                prof = {"driver": "COG", "height": height, "width": width,
                        "count": src_r.count, "dtype": dtype, "crs": target_crs,
                        "transform": transform, "nodata": src_r.nodata,
                        "compress": "DEFLATE", "zlevel": 9, "blocksize": 512,
                        "overviews": "AUTO", "bigtiff": "IF_SAFER"}
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
                tmp_path = tmp.name
            with rasterio.open(tmp_path, "w", **prof) as dst_r:
                dst_r.write(dst_arr)
            shutil.move(tmp_path, tif)
            done += 1
        except Exception as e:
            log(f"    WARNING: {os.path.basename(tif)}: {e}")
    log(f"  Reprojection done: {done} files")


def run_pipeline(cfg, log, done_cb, progress_cb=None):
    """
    cfg          : dict with all user settings
    log          : callable(str) — sends text to the UI log (thread-safe)
    done_cb      : callable(success: bool) — called when finished
    progress_cb  : callable(phase, current, total, label) — updates UI progress
    """
    if progress_cb is None:
        progress_cb = lambda phase, cur, tot, lbl="": None

    stop_event = cfg.get("stop_event")   # threading.Event or None

    try:
        log("=" * 60)
        log(f"Optical Foundry — started  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log("=" * 60)

        out_dir  = cfg["out_dir"]
        aoi_path = cfg["aoi_path"]
        os.makedirs(out_dir, exist_ok=True)

        # Clear stale error logs from a previous run so the end-of-run summary
        # reflects THIS run only (a clean re-run otherwise re-reports old errors).
        for _old in glob.glob(os.path.join(_error_dir(cfg), "*.error.txt")):
            try: os.remove(_old)
            except Exception: pass

        # load AOI
        import geopandas as gpd
        gdf = gpd.read_file(aoi_path).to_crs("EPSG:4326")
        geom = _safe_union(gdf)
        aoi_wkt   = geom.wkt
        aoi_label = re.sub(r"[^A-Za-z0-9_-]", "_", cfg.get("aoi_label") or Path(aoi_path).stem)

        # ── Scattered fields: cluster a per-field file into tight sub-AOIs ─────
        # Each cluster is read as its own small window (less download + compute)
        # and, when a fields file is given, masked to the field shapes. Default
        # is one sub-AOI = the whole region (identical to legacy behaviour).
        sub_aois = [{"tag": "", "wkt": aoi_wkt, "fields": None}]
        _fields_path = (cfg.get("fields_path") or "").strip()
        if cfg.get("cluster_aoi") or _fields_path:
            try:
                fg   = _read_fields(_fields_path).to_crs("EPSG:4326") if _fields_path else gdf
                try: fg["geometry"] = fg.geometry.make_valid()
                except Exception: fg["geometry"] = fg.geometry.buffer(0)
                fsel = fg[fg.intersects(geom)]
                if len(fsel) > 0:
                    if _fields_path:
                        log(f"[fields] {len(fsel)} of {len(fg)} field polygons inside the AOI")
                    _cl, _cov = _cluster_polygons(
                        fsel, max_gap_km=float(cfg.get("cluster_gap_km", 5.0)))
                    if _cl:
                        from shapely import wkt as _wktmod
                        sub_aois = []
                        for c in _cl:
                            bb = _wktmod.loads(c["wkt"])
                            members = (list(fsel[fsel.intersects(bb)].geometry)
                                       if _fields_path else None)
                            sub_aois.append({"tag": c["tag"], "wkt": c["wkt"],
                                             "fields": members})
                        log(f"[fields] {len(sub_aois)} cluster(s) — each read as a tight "
                            f"window" + ("; output masked to field shapes" if _fields_path else ""))
            except Exception as _ce:
                log(f"[fields] clustering skipped: {_ce}")

        # UTM CRS from centroid
        c = geom.centroid
        zone = min(60, max(1, int((c.x + 180) / 6) + 1))   # clamp: no zone 61 at the antimeridian (S13)
        target_crs = f"EPSG:{32600 + zone if c.y >= 0 else 32700 + zone}"
        log(f"AOI: {aoi_label}  |  CRS: {target_crs}")

        import pandas as pd
        days = list(pd.date_range(
            start=cfg["start_date"], end=cfg["end_date"], freq="D"
        ).strftime("%Y-%m-%d"))
        log(f"Date range: {cfg['start_date']} → {cfg['end_date']}  ({len(days)} days)")

        max_cloud = cfg.get("max_cloud", 0)
        selected  = cfg.get("selected_outputs", BIOPHYS + BANDS + INDICES)
        max_workers = max(1, int(cfg.get("max_workers", 4)))

        log(f"Parallel workers: {max_workers}")
        log("")

        # ── Parallel day processing ────────────────────────────────────────
        errors    = []
        completed = 0
        total     = len(days)
        lock      = threading.Lock()

        def _run_day(sd, is_retry=False):
            nonlocal completed
            # Check stop BEFORE starting any network I/O
            if stop_event and stop_event.is_set():
                if not is_retry:
                    with lock:
                        completed += 1
                        _c = completed
                    progress_cb("process", _c, total, f"{sd} [skipped]")
                return
            ed = (pd.to_datetime(sd) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                _parts = []
                _ran   = []
                for sub in sub_aois:
                    if stop_event and stop_event.is_set():
                        break
                    _lbl = aoi_label + (f"_{sub['tag']}" if sub["tag"] else "")
                    r = _process_day(sub["wkt"], _lbl, sd, ed,
                                     target_crs, out_dir, max_cloud, selected,
                                     overwrite=cfg.get("overwrite", False),
                                     custom_indices=cfg.get("custom_indices", []),
                                     field_geoms=sub["fields"])
                    if r is not None:
                        _ran.append(r)
                        _parts.append(f"{sub['tag'] or 'AOI'}: {r}")
                # collapse the common case where EVERY cluster that ran hit the
                # footprint-matched-but-no-pixels result — one line, not N copies
                if _ran and all(r == _NO_PIXELS_MSG for r in _ran):
                    log(f"  {sd}: no usable pixels (footprint matched, swath missed "
                        f"the fields — S2 revisits every ~5 days)")
                elif _parts:
                    log(f"  {sd}: {'; '.join(_parts)}")
                else:
                    log(f"  {sd}: no data")
            except Exception as e:
                import traceback as _tb
                msg = _tb.format_exc()
                log(f"  {sd}: ERROR — {e}")
                _write_error(cfg, sd.replace("-", ""), "process", msg)
                with lock:
                    errors.append((sd, str(e)))
            finally:
                if not is_retry:
                    with lock:
                        completed += 1
                        _c = completed
                    progress_cb("process", _c, total, sd)
                else:
                    progress_cb("process", completed, total, f"{sd} [retry]")

        progress_cb("process", 0, total, "starting…")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_day, sd): sd for sd in days}
            for fut in as_completed(futures):
                if stop_event and stop_event.is_set():
                    # cancel all futures not yet started
                    for f in futures:
                        f.cancel()
                    break
                # exceptions are caught inside _run_day; re-raise only unexpected ones
                try:
                    fut.result()
                except Exception as e:
                    sd = futures[fut]
                    log(f"  [UNEXPECTED] {sd}: {e}")

        if stop_event and stop_event.is_set():
            log("\n[Stopped by user]")
            done_cb(False)
            return

        # ── Retry failed days ─────────────────────────────────────────────
        if errors and cfg.get("retry_failed", True):
            max_retries = max(1, int(cfg.get("max_retries", 2)))
            for attempt in range(1, max_retries + 1):
                if not errors:
                    break
                if stop_event and stop_event.is_set():
                    break
                retry_days = [sd for sd, _ in errors]
                errors.clear()
                log(f"\n── Retry attempt {attempt}/{max_retries}"
                    f" — {len(retry_days)} day(s) ──")
                log("  Waiting 10 s before retry…")
                time.sleep(10)
                for sd in retry_days:
                    if stop_event and stop_event.is_set():
                        break
                    log(f"  [retry] {sd}")
                    _run_day(sd, is_retry=True)
                if errors:
                    log(f"  After retry {attempt}: {len(errors)} day(s) still failing.")
                else:
                    log(f"  Retry {attempt} succeeded — all days OK.")

        log("")
        log(f"Done. {total - len(errors)} days OK, {len(errors)} errors.")

        # ── optional CRS reprojection ─────────────────────────────────────
        out_crs = cfg.get("output_crs", "").strip()
        if out_crs and out_crs.upper() not in ("AUTO", "UTM", ""):
            log(f"\n── Reprojecting outputs to {out_crs} ──")
            _reproject_outputs(out_dir, out_crs, log)

        # ── error summary ─────────────────────────────────────────────────
        _edir = _error_dir(cfg)
        _errs = glob.glob(os.path.join(_edir, "*.error.txt"))
        log("\n" + "=" * 60)
        if _errs:
            log(f"Pipeline complete — {len(_errs)} day(s) had errors.")
            log(f"Error logs saved to: {_edir}")
            for _ef in sorted(_errs):
                log(f"  ✗ {os.path.basename(_ef)}")
        else:
            log("Pipeline complete — no errors.")
        log(f"Output: {out_dir}")
        log("=" * 60)

        done_cb(True)

    except Exception as e:
        import traceback
        log(f"\nERROR: {e}")
        log(traceback.format_exc())
        done_cb(False)


# Thread-local storage so each parallel day-thread can set its own tile filter
# independently without interfering with other threads running concurrently.
_tile_local = threading.local()
_TILE_PATCH_DONE = False            # search_s2_items monkeypatch installed once
_TILE_PATCH_LOCK = threading.Lock()

# Let GDAL auto-retry transient S3/HTTP errors when reading the COGs.
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")


def _retry(fn, attempts=5, base=1.0, cap=20.0):
    """Call fn() with exponential back-off + jitter on transient errors.
    Re-raises the last error if all attempts fail.

    Needs to be generous: on Windows, many day-threads opening TLS connections
    to AWS at once make schannel intermittently fail the handshake ("schannel:
    failed to receive handshake", "UNEXPECTED_EOF_WHILE_READING", "Read failed").
    These are transient — more attempts and jittered back-off (so parallel
    threads don't all retry in lockstep and re-collide) ride them out. Waits are
    ~1,2,4,8s capped at 20s, each ±25% jittered."""
    import random
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < attempts - 1:
                delay = min(cap, base * (2 ** i))
                time.sleep(delay * random.uniform(0.75, 1.25))
    raise last


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


def _safe_union(gdf):
    """Union all geometries, repairing invalid ones first (self-intersections /
    holes not assignable to a shell would otherwise raise TopologyException)."""
    try:
        g = gdf.geometry.make_valid()
    except Exception:
        g = gdf.geometry.buffer(0)
    try:
        return g.union_all() if hasattr(g, "union_all") else g.unary_union
    except Exception:
        g = g.buffer(0)
        return g.union_all() if hasattr(g, "union_all") else g.unary_union


def _cluster_polygons(gdf_wgs84, max_gap_km=5.0, edge_buffer_m=200.0):
    """Group nearby polygons into spatial clusters (tight bounding boxes), so a
    scattered field AOI is processed as several small windows. Two polygons end
    up in the same cluster when the gap between them is <= max_gap_km. Returns
    (clusters, coverage) where each cluster is {tag, wkt(bbox EPSG:4326), n,
    area_km2}, ordered north->south, west->east."""
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
    cid_per_poly = joined["cid"].groupby(joined.index).first()
    boxes_m, counts = {}, {}
    for idx, g in gm.geometry.items():
        cid = cid_per_poly.get(idx, -1)
        minx, miny, maxx, maxy = g.bounds
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


def _valid_raster(path, min_bands=1):
    """True only if `path` is a COMPLETE, readable raster — reads a block from
    the bottom-right of the last band to detect tail-truncated files left by an
    interrupted write."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 1024:
            return False
        import rasterio
        from rasterio.windows import Window
        with rasterio.open(path) as ds:
            if ds.count < min_bands or ds.width <= 0 or ds.height <= 0:
                return False
            w = min(ds.width, 16); h = min(ds.height, 16)
            ds.read(indexes=ds.count,
                    window=Window(ds.width - w, ds.height - h, w, h))
        return True
    except Exception:
        return False


# SCL classes: 0 NO_DATA, 1 SATURATED/DEFECTIVE, 2 DARK, 3 CLOUD_SHADOW,
# 4 VEGETATION, 5 BARE_SOIL, 6 WATER, 7 UNCLASSIFIED, 8 CLOUD_MED, 9 CLOUD_HIGH,
# 10 THIN_CIRRUS, 11 SNOW/ICE.
_SCL_KEEP  = (2, 4, 5, 6, 7)              # usable surface
_SCL_CLOUD = (1, 3, 8, 9, 10, 11)         # real cloud/shadow/snow/defective


def _scl_valid_mask(scl_arr):
    """Boolean 'keep' mask from a (possibly reprojected) SCL raster.

    Everything that is neither a keep-class nor a real cloud-class is treated as
    NODATA — SCL class 0 AND any fill value introduced upstream. satellitetools
    stamps its own fill (99) and a cross-zone reproject_match adds another
    (e.g. -32768 / 255). NODATA must NOT be lumped into 'cloud': on a small
    cross-zone window the valid data is a tiny island ringed by that fill, and
    binary_fill_holes would treat the island as a hole and fill it back to cloud
    — wiping every field (the "0% valid seam" bug). Deriving nodata by exclusion
    is robust to whatever fill value actually appears."""
    import numpy as np
    from skimage.morphology import remove_small_objects, closing, square
    from scipy.ndimage import binary_fill_holes
    keep   = np.isin(scl_arr, _SCL_KEEP)
    cloudy = np.isin(scl_arr, _SCL_CLOUD)
    nodata = ~keep & ~cloudy               # class 0 + all fill (99, -32768, …)
    cloud = cloudy
    cloud = remove_small_objects(cloud, min_size=49)
    cloud = binary_fill_holes(cloud)
    cloud = closing(cloud, square(3))
    cloud = remove_small_objects(cloud, min_size=47)
    return (~cloud) & (~nodata)


def _process_single_tile(sattools, aws_mod, S2Band, geom, aoi_label, sd, ed,
                          tile_id, sat_letter, target_crs, out_dir,
                          max_cloud, selected, overwrite=False, single_tile=True,
                          custom_indices=None, field_geoms=None):
    """
    Run the full satellitetools + rasterio pipeline for ONE specific tile.
    The caller must have already set _tile_local.tile_id / .sat_letter so the
    monkey-patch will filter to this tile only.
    Returns (written_paths, step_errors).

    single_tile=True  → use classic filename _A_B2.tif (no tile ID)
    single_tile=False → use intermediate filename _SA_32TQN_B2.tif (will be
                        mosaicked and deleted by the caller)
    If overwrite=False, existing files are kept and their paths are still
    returned so the mosaic step can include them.
    """
    import numpy as np, pandas as pd, rasterio, rioxarray
    import xarray as xr

    written_paths = []   # absolute paths of files written
    step_errors   = []
    date_prefix   = sd.replace("-", "")

    aoi   = sattools.AOI(aoi_label, geom, "EPSG:4326")
    bands = S2Band.get_10m_to_20m_bands()
    req_ms  = sattools.Sentinel2RequestParams(sd, ed, sattools.DataSource.AWS, bands)
    req_scl = sattools.Sentinel2RequestParams(sd, ed, sattools.DataSource.AWS, [S2Band.SCL])

    try:
        _, ds_ms = _retry(lambda: sattools.wrappers.get_s2_qi_and_data(
            aoi=aoi, req_params=req_ms, qi_threshold=0., qi_filter=[]))
        _, ds_scl = _retry(lambda: sattools.wrappers.get_s2_qi_and_data(
            aoi=aoi, req_params=req_scl, qi_threshold=0., qi_filter=[]))
    except Exception as e:
        return [], [f"tile {tile_id}/{sat_letter} fetch: {e}"]

    if ds_ms is None or (hasattr(ds_ms, "time") and ds_ms.time.size == 0):
        return [], []   # no scene for this tile today — normal

    # biophysical (pure Python NN)
    bp = {}
    for var_name, bv_name in [("LAI","LAI"),("CCC","LAI_Cab"),("CWC","LAI_Cw"),
                                ("FAPAR","FAPAR"),("FCOVER","FCOVER")]:
        if var_name not in selected: continue
        try:
            bv = getattr(sattools.biophys.BiophysVariable, bv_name)
            bp[var_name] = sattools.biophys.run_snap_biophys(ds_ms, bv)
        except Exception:
            bp[var_name] = None

    times = ds_ms.time.values if hasattr(ds_ms, "time") else [None]

    # True native CRS of the source granule. satellitetools returns pixels in the
    # granule's own UTM zone (e.g. EPSG:32634 for tile 34TGR) with x/y coords in
    # that zone, but stamps the CRS only in ds.attrs["crs"] — the band DataArrays
    # carry no rio CRS. We MUST write this native CRS before reprojecting to
    # target_crs; stamping target_crs directly (the old bug) relabels cross-zone
    # pixels without warping them, landing outputs one UTM zone (~6° lon) off.
    src_crs     = ds_ms.attrs.get("crs", target_crs)
    scl_src_crs = ds_scl.attrs.get("crs", src_crs) if ds_scl is not None else src_crs

    for t in times:
        date_str = pd.to_datetime(t).strftime("%Y%m%d") if t is not None else date_prefix
        ds_t = ds_ms.sel(time=t)

        def _get_b(name, _ds=ds_t):
            if "band_data" in _ds.data_vars and "band" in _ds.coords:
                da = _ds["band_data"]
                bvals = [str(b) for b in da["band"].values]
                if name in bvals:
                    return da.isel(band=bvals.index(name)).squeeze()
            if name in _ds.data_vars:
                return _ds[name].squeeze()
            raise KeyError(f"band {name!r} not found")

        def _to_refl(da):
            # satellitetools (aws.py) already returns 0-1 BOA reflectance: it
            # applies scale (1e-4) and the processing-baseline >=04.00 offset
            # per the STAC 'earthsearch:boa_offset_applied' flag. So NO rescaling
            # here — just ensure float. (The old `max()>10 -> /10000` heuristic
            # could never help correctly-scaled data and could only wrongly
            # divide it by 10000 if a stray value exceeded 10.)
            return da.astype(float)

        try:
            ref_10m = _get_b("B8").rio.write_crs(src_crs).rio.reproject(
                target_crs, resolution=10)
        except Exception as e:
            step_errors.append(f"ref_10m failed ({date_str}/{tile_id}): {e}")
            continue

        # ── SCL cloud mask ────────────────────────────────────────────────────
        mask = None
        if ds_scl is not None:
            try:
                scl_t = ds_scl.sel(time=t)
                if "SCL" in scl_t.data_vars:
                    scl_da = scl_t["SCL"].squeeze()
                elif "band_data" in scl_t.data_vars:
                    scl_da = scl_t["band_data"].squeeze()
                else:
                    raise KeyError("SCL not found")
                scl_10m = scl_da.rio.write_crs(scl_src_crs).rio.reproject_match(ref_10m)
                scl_arr = scl_10m.values
                m = _scl_valid_mask(scl_arr)
                pct_valid = m.sum() / m.size * 100
                if max_cloud > 0 and (100 - pct_valid) > max_cloud:
                    step_errors.append(
                        f"skipped {date_str}/{tile_id}: cloud {100-pct_valid:.0f}%")
                    continue
                mask = m.astype(bool)
                # save SCL raster (with tile tag)
                try:
                    arr_scl = scl_arr[np.newaxis].astype(np.uint8)
                    d_scl = os.path.join(out_dir, f"index=SCL/aoi={aoi_label}")
                    os.makedirs(d_scl, exist_ok=True)
                    _scl_stem = (f"S2_{date_str}_000_{aoi_label}_{sat_letter}_SCL.tif"
                                 if single_tile else
                                 f"S2_{date_str}_000_{aoi_label}_S{sat_letter}_{tile_id}_SCL.tif")
                    p_scl = os.path.join(d_scl, _scl_stem)
                    _pscl_part = p_scl + ".part"
                    with rasterio.open(_pscl_part, "w", driver="GTiff",
                            height=arr_scl.shape[1], width=arr_scl.shape[2],
                            count=1, dtype="uint8", crs=ref_10m.rio.crs,
                            transform=ref_10m.rio.transform(),
                            compress="DEFLATE") as dst:
                        dst.write(arr_scl)
                    os.replace(_pscl_part, p_scl)
                except Exception as e:
                    step_errors.append(f"SCL save ({date_str}/{tile_id}): {e}")
            except Exception as e:
                step_errors.append(f"SCL mask ({date_str}/{tile_id}): {e}")

        # restrict to the field polygons (nodata outside) when in fields mode.
        # MUST run even if SCL was absent or its processing failed above —
        # otherwise a cluster is written as its full bounding box instead of the
        # field shapes, silently defeating the scattered-fields feature.
        if field_geoms:
            try:
                from rasterio.features import geometry_mask as _geom_mask
                import geopandas as _gpd
                _fg = _gpd.GeoSeries(field_geoms, crs="EPSG:4326").to_crs(target_crs)
                _fmask = _geom_mask(list(_fg.geometry.values),
                                    out_shape=(ref_10m.shape[-2], ref_10m.shape[-1]),
                                    transform=ref_10m.rio.transform(), invert=True)
                mask = _fmask if mask is None else (mask & _fmask)
                # Cross-zone regression guard: when the source granule's UTM zone
                # differs from the target tile's zone, a missing warp silently
                # lands pixels a full zone (~6° lon) away. The field polygons
                # define the AOI, so after a correct warp at least some must fall
                # inside the raster grid. Zero + cross-zone == the misprojection
                # bug — fail the tile loudly instead of writing a mislocated file.
                if (str(src_crs).upper() != str(target_crs).upper()
                        and not _fmask.any()):
                    step_errors.append(
                        f"CROSS-ZONE MISPROJECTION ({date_str}/{tile_id}): source "
                        f"{src_crs} → target {target_crs}, but 0 field polygons fall "
                        f"within the raster extent after warp — aborting tile")
                    continue
            except Exception as _fe:
                step_errors.append(f"field-mask ({date_str}/{tile_id}): {_fe}")

        def _write(da, index_name, _ref=ref_10m, _mask=mask):
            d = os.path.join(out_dir, f"index={index_name}/aoi={aoi_label}")
            os.makedirs(d, exist_ok=True)
            # single-tile day → classic name _A_B2.tif
            # multi-tile day  → intermediate name _SA_32TQN_B2.tif (mosaicked later)
            _stem = (f"S2_{date_str}_000_{aoi_label}_{sat_letter}_{index_name}.tif"
                     if single_tile else
                     f"S2_{date_str}_000_{aoi_label}_S{sat_letter}_{tile_id}_{index_name}.tif")
            path = os.path.join(d, _stem)
            # skip if file already exists and overwrite is off;
            # still register the path so the mosaic step knows about it
            if os.path.isfile(path) and not overwrite and _valid_raster(path):
                written_paths.append(path)
                return
            da = da.rio.write_crs(src_crs).rio.reproject_match(_ref)
            if _mask is not None:
                da = da.where(_mask, NODATA)
            da = da.rio.write_nodata(NODATA)
            arr = da.values[np.newaxis].astype("float32")
            # Index divisions (e.g. MTCI=(B6-B5)/(B5-B4), CIRE=B8/B5-1) yield
            # inf/NaN where the denominator is ~0 (bare soil, water, masked
            # pixels). The NODATA check below does NOT catch inf, so non-finite
            # values would otherwise be written into the COG — clean them first.
            arr[~np.isfinite(arr)] = NODATA
            if not np.any(arr != NODATA):
                return
            prof = {
                "driver": "COG", "height": _ref.shape[0], "width": _ref.shape[1],
                "count": 1, "dtype": "float32", "crs": _ref.rio.crs,
                "transform": _ref.rio.transform(), "nodata": NODATA,
                "compress": "DEFLATE", "zlevel": 9, "blocksize": 512,
                "overviews": "AUTO", "bigtiff": "IF_SAFER",
            }
            _part = path + ".part"
            try:
                with rasterio.open(_part, "w", **prof) as dst:
                    dst.write(arr)
                os.replace(_part, path)
            except BaseException:
                try: os.remove(_part)
                except Exception: pass
                raise
            written_paths.append(path)

        # ── biophysicals ──────────────────────────────────────────────────────
        for var_name, ds_bp in bp.items():
            if ds_bp is None or var_name not in selected: continue
            try:
                key = {"LAI":"lai","CCC":"lai_cab","CWC":"lai_cw",
                       "FAPAR":"fapar","FCOVER":"fcover"}[var_name]
                if key in ds_bp:
                    _write(ds_bp.sel(time=t)[key].squeeze(), var_name)
            except Exception as e:
                step_errors.append(f"{var_name} ({date_str}/{tile_id}): {e}")

        # ── spectral bands (lazy: each band loaded+scaled only when needed) ─────
        _ALL_BANDS_S2 = ("B2","B3","B4","B5","B6","B7","B8","B8A","B11","B12")
        _band_cache = {}
        def _B(name):
            if name not in _band_cache:
                _band_cache[name] = _to_refl(_get_b(name))
            return _band_cache[name]

        for bname in _ALL_BANDS_S2:
            if bname in selected:
                try: _write(_B(bname), bname)
                except Exception as e:
                    step_errors.append(f"{bname} ({date_str}/{tile_id}): {e}")

        # ── spectral indices (lambdas → only the selected ones are computed) ────
        idx_exprs = {
            "NDVI":   lambda: (_B("B8")-_B("B4"))/(_B("B8")+_B("B4")),
            "NDWI":   lambda: (_B("B3")-_B("B8"))/(_B("B3")+_B("B8")),
            "NDII":   lambda: (_B("B8")-_B("B11"))/(_B("B8")+_B("B11")),
            "MSAVI2": lambda: ((2*_B("B8")+1) - ((2*_B("B8")+1)**2 - 8*(_B("B8")-_B("B4")))**0.5) / 2,
            "CIRE":   lambda: (_B("B8")/_B("B5")) - 1,
            "EVI":    lambda: 2.5*(_B("B8")-_B("B4"))/(_B("B8")+6*_B("B4")-7.5*_B("B2")+1),
            "NDRE1":  lambda: (_B("B8A")-_B("B5"))/(_B("B8A")+_B("B5")),
            "MTCI":   lambda: (_B("B6")-_B("B5"))/(_B("B5")-_B("B4")),
        }
        for iname, fn in idx_exprs.items():
            if iname in selected:
                try: _write(fn(), iname)
                except Exception as e:
                    step_errors.append(f"{iname} ({date_str}/{tile_id}): {e}")

        # ── custom indices ────────────────────────────────────────────────────
        if custom_indices:
            import numpy as _np
            try:
                _ns = {bn: _B(bn) for bn in _ALL_BANDS_S2}
            except Exception as e:
                step_errors.append(f"custom-index bands ({date_str}/{tile_id}): {e}")
                _ns = {}
            _ns["np"] = _np
            for ci in custom_indices:
                cname = ci.get("name", "").strip()
                cexpr = ci.get("expr", "").strip()
                if not cname or not cexpr:
                    continue
                try:
                    _validate_expr(cexpr, [k for k in _ns if k != "np"])
                    result = eval(cexpr, {"__builtins__": {}}, _ns)
                    _write(result, cname)
                except Exception as e:
                    step_errors.append(f"{cname} ({date_str}/{tile_id}): {e}")

    return written_paths, step_errors


def _process_day(aoi_wkt, aoi_label, sd, ed, target_crs, out_dir, max_cloud, selected,
                 overwrite=False, custom_indices=None, field_geoms=None):
    """
    Process one day with explicit per-tile handling.

    Architecture:
      1. Direct EarthSearch query to discover every (tile_id, sat_letter) pair.
      2. For each pair, temporarily set a thread-local tile filter and run the
         full satellitetools pipeline — this way satellitetools only sees items
         for that one tile, guaranteeing we get real data for BOTH tiles when the
         AOI straddles a tile boundary (e.g. 32TQN + 32TQP).
      3. After all tiles are processed, mosaic per-variable with rasterio.merge.
    """
    import warnings, gc, time as _time
    from collections import defaultdict as _dd
    from shapely import wkt as shapely_wkt

    warnings.filterwarnings("ignore")
    t0 = _time.time()

    try:
        import satellitetools as sattools
        from satellitetools.common.sentinel2 import S2Band
        import satellitetools.aws as aws_mod
    except ImportError:
        raise ImportError("satellitetools not installed. Run: pip install satellitetools")

    geom = shapely_wkt.loads(aoi_wkt)
    bbox = list(geom.bounds)
    date_prefix = sd.replace("-", "")

    # ── 1. Discovery: find every (tile_id, sat_letter) available for this day ─
    try:
        raw_items = _retry(lambda: aws_mod.EarthSearch(
            datestart=sd, dateend=ed, bbox=bbox,
            collection=aws_mod.EarthSearchCollection.SENTINEL2_L2A,
        ).get_items())
    except Exception as e:
        return f"ERROR EarthSearch: {e}"
    # cache discovery for this thread/day so the per-tile patch reuses it
    _tile_local.raw_items = raw_items
    _tile_local.raw_key   = (sd, ed, tuple(round(b, 6) for b in bbox))

    tile_sats = {}   # (tile_id, sat_letter) → (raw_item, seq_idx)
    for raw in raw_items:
        raw_id = raw['id'] if isinstance(raw, dict) else raw.id
        parts  = raw_id.split('_')
        if len(parts) < 5 or not parts[0].startswith("S2"):
            continue
        tile_id    = parts[1]
        sat_letter = parts[0][2:3].upper()
        if sat_letter not in ("A", "B", "C", "D"):
            sat_letter = "X"
        try:
            idx = int(parts[3])
        except Exception:
            idx = 0
        key = (tile_id, sat_letter)
        if key not in tile_sats or idx > tile_sats[key][1]:
            tile_sats[key] = (raw, idx)

    if not tile_sats:
        return None   # no scenes today

    # ── 2. Per-tile monkey-patch using thread-local filter ─────────────────────
    def _patched_tile_search(self):
        """Return only EarthSearch items for the tile set in _tile_local."""
        target_tile = getattr(_tile_local, 'tile_id',    None)
        target_sat  = getattr(_tile_local, 'sat_letter', None)

        _key = (self.req_params.datestart, self.req_params.dateend,
                tuple(round(b, 6) for b in self.aoi.geometry.bounds))
        if (getattr(_tile_local, "raw_key", None) == _key
                and getattr(_tile_local, "raw_items", None) is not None):
            items = _tile_local.raw_items          # reuse the day's discovery
        else:
            items = _retry(lambda: aws_mod.EarthSearch(
                datestart=self.req_params.datestart,
                dateend=self.req_params.dateend,
                bbox=list(self.aoi.geometry.bounds),
                collection=aws_mod.EarthSearchCollection.SENTINEL2_L2A,
            ).get_items())

        unique = {}
        for raw in items:
            raw_id = raw['id'] if isinstance(raw, dict) else raw.id
            parts  = raw_id.split('_')
            if len(parts) < 5:
                continue
            t_id = parts[1]
            s_lt = parts[0][2:3].upper() if parts[0].startswith("S2") else "X"
            # apply per-thread tile filter
            if target_tile is not None and (t_id != target_tile or s_lt != target_sat):
                continue
            base_key = '_'.join(parts[0:3] + [parts[-1]])
            try:
                idx = int(parts[3])
            except Exception:
                idx = 0
            prev = unique.get(base_key)
            if prev is None or idx > prev[1]:
                unique[base_key] = (raw, idx)

        self.s2_items = [aws_mod.AWSSentinel2Item(i) for i, _ in unique.values()]
        self.sort_s2_items()

    global _TILE_PATCH_DONE
    if not _TILE_PATCH_DONE:                 # install once (avoids a per-day race)
        with _TILE_PATCH_LOCK:
            if not _TILE_PATCH_DONE:
                # S12: this monkeypatches a satellitetools internal. If a version
                # bump renamed/removed it, fail loudly here instead of silently
                # skipping the per-tile filter. Pin satellitetools==2.1.6.
                if not hasattr(aws_mod, "AWSSentinel2DataCollection") or not hasattr(
                        aws_mod.AWSSentinel2DataCollection, "search_s2_items"):
                    raise RuntimeError(
                        "satellitetools "
                        f"{getattr(sattools, '__version__', '?')} is missing the "
                        "internals Optical Foundry patches (search_s2_items). "
                        "Pin satellitetools==2.1.6.")
                aws_mod.AWSSentinel2DataCollection.search_s2_items = _patched_tile_search
                _TILE_PATCH_DONE = True

    # ── 3. Process each (tile_id, sat_letter) pair independently ──────────────
    all_written  = []          # flat list of written file paths
    step_errors  = []
    tile_files   = _dd(list)   # index_name → [path_tile1, path_tile2, ...]

    is_single = len(tile_sats) == 1   # → classic filename; False → intermediate + mosaic

    for (tile_id, sat_letter) in tile_sats:
        _tile_local.tile_id    = tile_id
        _tile_local.sat_letter = sat_letter
        try:
            w_paths, errs = _process_single_tile(
                sattools, aws_mod, S2Band, geom, aoi_label, sd, ed,
                tile_id, sat_letter, target_crs, out_dir, max_cloud, selected,
                overwrite=overwrite, single_tile=is_single,
                custom_indices=custom_indices, field_geoms=field_geoms)
            all_written.extend(w_paths)
            step_errors.extend(errs)
            # group written paths by index_name for the mosaic step
            for p in w_paths:
                stem = Path(p).stem           # e.g. S2_20250325_000_..._SA_32TQN_NDVI
                idx_name = stem.rsplit("_", 1)[-1]
                tile_files[idx_name].append(p)
        except Exception as e:
            import traceback as _tb
            step_errors.append(f"tile {tile_id}/{sat_letter}: {e}")
        finally:
            _tile_local.tile_id    = None
            _tile_local.sat_letter = None

    # ── 4. Mosaic per-variable when more than one tile was written ─────────────
    if any(len(v) > 1 for v in tile_files.values()):
        import rasterio
        from rasterio.merge import merge as _rio_merge
        for idx_name, files in tile_files.items():
            if len(files) < 2:
                continue
            var_dir = os.path.join(out_dir, f"index={idx_name}/aoi={aoi_label}")
            mosaic_path = os.path.join(
                var_dir,
                f"S2_{date_prefix}_000_{aoi_label}_mosaic_{idx_name}.tif")
            if os.path.isfile(mosaic_path) and not overwrite and _valid_raster(mosaic_path):
                continue   # mosaic already done — skip
            try:
                datasets = [rasterio.open(f) for f in files]
                mosaic, transform = _rio_merge(datasets, method="first", nodata=NODATA)
                meta = datasets[0].meta.copy()
                meta.update({
                    "driver": "COG",
                    "height": mosaic.shape[1], "width": mosaic.shape[2],
                    "transform": transform, "nodata": NODATA,
                    "compress": "DEFLATE", "zlevel": 9,
                    "blocksize": 512, "overviews": "AUTO", "bigtiff": "IF_SAFER",
                })
                for ds in datasets:
                    ds.close()
                _mpart = mosaic_path + ".part"
                with rasterio.open(_mpart, "w", **meta) as dst:
                    dst.write(mosaic)
                os.replace(_mpart, mosaic_path)
                # remove the individual tile files — only after the mosaic is in place
                for f in files:
                    try: os.remove(f)
                    except Exception: pass
            except Exception as e:
                step_errors.append(f"mosaic failed ({idx_name}): {e}")

    gc.collect()
    n = len(all_written)
    # A dropped tile download (transient TLS/network) is tagged "… fetch: …".
    # Re-queue the whole day for retry even when other tiles succeeded (n>0),
    # otherwise a partial mosaic silently loses that tile forever. Keyed on
    # "fetch:" so deterministic post-download errors (SCL mask, index calc)
    # don't loop pointlessly — they'd never succeed on retry.
    fetch_fail = [e for e in step_errors if "fetch:" in e]
    if fetch_fail:
        raise RuntimeError(
            f"{n} file(s) written but {len(fetch_fail)} tile fetch(es) failed — "
            + " | ".join(fetch_fail[:4]))
    if n:
        n_tiles = len(tile_sats)
        suffix  = f" ({n_tiles} tiles mosaicked)" if n_tiles > 1 else ""
        return f"{n} outputs in {_time.time()-t0:.0f}s{suffix}"
    if step_errors:
        # Found scenes but wrote NOTHING while hitting errors → the output disk
        # is the likely cause (unplugged drive → WinError 433, disk full, denied
        # permissions). Raise so _run_day queues the day for retry instead of
        # silently downgrading it to a WARN that never re-runs.
        raise RuntimeError("wrote 0 files — " + " | ".join(step_errors[:4]))
    if tile_sats:
        # A tile whose FOOTPRINT overlaps the AOI can still carry no pixels over
        # it: Sentinel-2 revisits a given spot only every ~5 days, so on an
        # off-date the granule is all NODATA (SCL=99) here. That is "no
        # acquisition", not "all cloud".
        return _NO_PIXELS_MSG
    return None


# ═══════════════════════════════════════════════════════════════════════════
# DEPENDENCY CHECK
# ═══════════════════════════════════════════════════════════════════════════

def check_dependencies():
    results = {}
    packages = {
        "satellitetools": "satellitetools",
        "rasterio":        "rasterio",
        "geopandas":       "geopandas",
        "rioxarray":       "rioxarray",
        "numpy":           "numpy",
        "pandas":          "pandas",
        "scikit-image":    "skimage",
        "scipy":           "scipy",
        "tkcalendar":      "tkcalendar",
    }
    missing = []
    for pkg, imp in packages.items():
        try: __import__(imp)
        except ImportError: missing.append(pkg)
    if missing:
        results["Python packages"] = ("install",
            f"Missing: {', '.join(missing)}\n"
            f"Click INSTALL to install automatically.\n"
            f"Command: pip install {' '.join(missing)}")
    else:
        results["Python packages"] = (True, "All packages present")
    return results


# ═══════════════════════════════════════════════════════════════════════════
# RASTER CALCULATOR  (standalone worker, shared by the 🧮 tab)
# ═══════════════════════════════════════════════════════════════════════════

# S2 band / product name pattern  — matches the last token before .tif
_S2_BAND_RE = re.compile(
    r'_(B2|B3|B4|B5|B6|B7|B8A?|B11|B12'
    r'|NDVI|NDWI|NDII|MSAVI2|CIRE|EVI|NDRE1|MTCI'
    r'|LAI|CCC|CWC|FAPAR|FCOVER|SCL)'
    r'\.tif$', re.IGNORECASE)


def _s2_raster_calc_worker(input_folder, expr_str, band_name_out, output_folder,
                           log, progress_cb=None, stop_ev=None):
    """
    Apply a numpy expression to every S2 scene group found in input_folder.

    Band variables (B2, B3, NDVI, LAI, …) are loaded by name from matching
    files.  Results are written as float32 LZW-tiled GeoTIFFs to output_folder,
    mirroring the input subfolder structure.
    """
    try:
        import numpy as np
        import rasterio
    except ImportError as e:
        log(f"ERROR: Missing package: {e}.  Run: pip install numpy rasterio")
        return

    if progress_cb is None:
        progress_cb = lambda *a: None

    # Collect all tifs
    tifs = sorted(
        glob.glob(os.path.join(input_folder, "**", "*.tif"), recursive=True) +
        glob.glob(os.path.join(input_folder, "*.tif")))

    if not tifs:
        log("  No .tif files found in input folder"); return

    # Group by scene prefix (strip band suffix)
    groups: dict = {}
    for fpath in tifs:
        m = _S2_BAND_RE.search(os.path.basename(fpath))
        if m:
            band   = m.group(1).upper()
            prefix = fpath[:len(fpath) - len(m.group(0))]
            groups.setdefault(prefix, {})[band] = fpath

    if not groups:
        log("  No band-organised files found.")
        log("  Expected names like: S2_YYYYMMDD_000_AOI_A_NDVI.tif, …")
        return

    log(f"  Found {len(groups)} scene groups")
    log(f"  Expression : {expr_str}")
    log(f"  Output band: {band_name_out}")
    os.makedirs(output_folder, exist_ok=True)

    total, done = len(groups), 0
    _nodata = -9999.0

    # Validate against a name/node allowlist, then compile — both surface a clear
    # error instead of silently producing nothing (S1).
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
                result = eval(_code, {"__builtins__": {}}, ns)  # type: ignore
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
                    "SentinelFoundry.Optical")
            except Exception:
                pass
        super().__init__()
        self.title("Optical Foundry")
        self.configure(bg=BG)
        self.minsize(960, 680)
        self._running    = False
        self._thread     = None
        self._stop_event = threading.Event()
        self._dep_results = {}
        self._saved_cfg = _load_config()
        # restore the previous run's output selection (None on first run → all on)
        _sel = self._saved_cfg.get("selected_outputs")
        self._sel_set = set(_sel) if _sel is not None else None

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

        self._build()
        # match SAR Foundry's opening size
        w, h = 1200, 860
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(1100, 720)
        self.after(300, self._run_dep_check)

    # ── gradient banner ───────────────────────────────────────────────────────
    def _draw_banner(self, event=None):
        c = self._banner
        w = c.winfo_width() or 1200
        h = 80
        c.delete("all")
        steps = 200
        for i in range(steps):
            t = i / steps
            r = int(0x15 + (0x0D - 0x15) * t)
            g = int(0x65 + (0x0D - 0x65) * t)
            b = int(0xC0 + (0x1A - 0xC0) * t)
            x0 = int(w * i / steps); x1 = int(w * (i + 1) / steps) + 1
            c.create_rectangle(x0, 0, x1, h, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
        c.create_rectangle(0, h - 3, w, h, fill=ACCENT, outline="")
        c.create_text(24, 26, text="🛰  Optical Foundry",
                      font=(_FONT_FAM, 21, "bold"), fill=WHITE, anchor="w")
        c.create_text(26, 56,
                      text="Sentinel-2 L2A  ·  Cloud-masked COG outputs  ·  Biophysical + Spectral",
                      font=(_FONT_FAM, 9), fill=ACCENT_L, anchor="w")

    def _build(self):
        self.configure(bg=SURFACE)
        # gradient splash banner
        self._banner = tk.Canvas(self, height=80, highlightthickness=0, bd=0)
        self._banner.pack(fill=tk.X)
        self._banner.bind("<Configure>", self._draw_banner)
        self.after(50, self._draw_banner)

        # draggable paned window (same as SAR Foundry)
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL,
                               bg=BG, sashwidth=5, sashrelief="flat",
                               bd=0, handlesize=0)
        paned.pack(fill=tk.BOTH, expand=True)
        left  = tk.Frame(paned, bg=BG)
        right = tk.Frame(paned, bg=SURFACE)
        paned.add(left,  minsize=440, width=500, stretch="never")
        paned.add(right, minsize=300, stretch="always")
        self._build_form(left)
        self._build_log(right)
        self.after(300, lambda: paned.sash_place(0, 500, 0))

    def _section(self, p, text):
        outer = tk.Frame(p, bg=BG2); outer.pack(fill=tk.X, padx=8, pady=(12, 2))
        accent_bar = tk.Frame(outer, bg=ACCENT, width=4)
        accent_bar.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(outer, text=f"  {text}", font=FONT_BOLD,
                 fg=ACCENT_L, bg=BG2, pady=7).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _btn(self, parent, text, cmd, color=None, **kw):
        c = color or ACCENT
        hover = {ACCENT: ACCENT2, RED: "#C62828", GREEN: "#388E3C"}.get(c, ACCENT2)
        kw.setdefault("font", FONT_BOLD)
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

    def _make_tab_scroll(self, parent):
        """Scrollable frame inside a tab — independent mouse-wheel per tab."""
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
            # Windows: delta in ±120 steps · macOS: small ±deltas · Linux: no
            # MouseWheel event at all, uses Button-4/5 instead.
            num = getattr(event, "num", 0)
            if num == 4:
                c.yview_scroll(-1, "units")
            elif num == 5:
                c.yview_scroll(1, "units")
            else:
                step = (event.delta // 120 if abs(event.delta) >= 120
                        else (1 if event.delta > 0 else -1))
                c.yview_scroll(-step, "units")
        def _bind(e, c=canvas, f=_scroll):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                c.bind_all(seq, f)
        def _unbind(e, c=canvas):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                c.unbind_all(seq)
        canvas.bind("<Enter>", _bind)
        canvas.bind("<Leave>", _unbind)
        return frame

    def _build_form(self, parent):
        # ── fixed footer (Start / Stop) — sits below the notebook ───────────
        footer = tk.Frame(parent, bg=BG2)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        run_fr = tk.Frame(footer, bg=BG2); run_fr.pack(fill=tk.X, padx=8, pady=6)
        self.btn_run = self._btn(run_fr, "▶   START PIPELINE", self._on_start,
                                  pady=10, font=(_FONT_FAM, 12, "bold"))
        self.btn_run.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.btn_stop = self._btn(run_fr, "■  STOP", self._on_stop, color=RED,
                                   pady=10, font=(_FONT_FAM, 10, "bold"),
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)
        self._btn(footer, "📋  Run history", self._show_history,
                  color=BG2, font=(_FONT_FAM, 9), pady=4
                  ).pack(fill=tk.X, padx=8, pady=(0, 6))

        # ── notebook ─────────────────────────────────────────────────────────
        style = ttk.Style()
        style.configure("App.TNotebook", background=BG, borderwidth=0,
                         tabmargins=[0, 0, 0, 0])
        style.configure("App.TNotebook.Tab",
                         background=BG2, foreground=FG2,
                         padding=[14, 8], font=(_FONT_FAM, 10, "bold"))
        style.map("App.TNotebook.Tab",
                  background=[("selected", BG), ("active", SURFACE)],
                  foreground=[("selected", ACCENT_L), ("active", FG)])

        nb = ttk.Notebook(parent, style="App.TNotebook")
        nb.pack(fill=tk.BOTH, expand=True)

        t_dl   = tk.Frame(nb, bg=BG)
        t_proc = tk.Frame(nb, bg=BG)
        t_out  = tk.Frame(nb, bg=BG)
        t_calc = tk.Frame(nb, bg=BG)
        nb.add(t_dl,   text="  ⬇  Download  ")
        nb.add(t_proc, text="  ⚙  Processing  ")
        nb.add(t_out,  text="  📁  Output  ")
        nb.add(t_calc, text="  🧮  Raster Calc  ")

        # ════════════════════════════════════════════════════════════════════
        # DOWNLOAD TAB  — AOI · Date Range · Cloud Cover
        # ════════════════════════════════════════════════════════════════════
        p = self._make_tab_scroll(t_dl)

        def _hint(text, parent=p):
            tk.Label(parent, text=f"  {text}", font=(_FONT_FAM, 8),
                     bg=BG, fg=FG2).pack(anchor="w", padx=14)

        # ── Data source banner ───────────────────────────────────────────────
        src_banner = tk.Frame(p, bg=BG2, bd=0); src_banner.pack(fill=tk.X, padx=10, pady=(8, 2))
        tk.Frame(src_banner, bg="#26A69A", width=4).pack(side=tk.LEFT, fill=tk.Y)
        src_inner = tk.Frame(src_banner, bg=BG2); src_inner.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=6)
        tk.Label(src_inner, text="📡  Data source: AWS EarthSearch  (sentinel-2-l2a-cogs, public S3 bucket)",
                 font=FONT_BOLD, bg=BG2, fg=ACCENT_L).pack(anchor="w")
        tk.Label(src_inner,
                 text="Free, anonymous, no Copernicus / ESA account needed.  "
                      "Data is identical to Copernicus Data Space (same ESA L2A product).",
                 font=(_FONT_FAM, 8), bg=BG2, fg=FG2).pack(anchor="w")
        def _open_aws():
            import webbrowser
            webbrowser.open("https://registry.opendata.aws/sentinel-2-l2a-cogs/")
        tk.Button(src_inner, text="AWS Open Data registry →",
                  font=(_FONT_FAM, 8, "underline"), bg=BG2, fg=ACCENT,
                  relief="flat", cursor="hand2",
                  command=_open_aws).pack(anchor="w")

        # ── 1. AOI ──────────────────────────────────────────────────────────
        self._section(p, "1. Area of Interest")
        self.v_aoi = tk.StringVar(value=self._saved_cfg.get("aoi_path", ""))
        row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=4)
        tk.Label(row, text="AOI file", font=FONT, bg=BG, fg=FG,
                 width=12, anchor="w").pack(side=tk.LEFT)
        self._entry(row, self.v_aoi).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row, "Browse",
                  lambda: self._browse_file(self.v_aoi,
                      [("Spatial files", "*.shp *.gpkg *.geojson"), ("All", "*")]),
                  padx=8, pady=4).pack(side=tk.LEFT, padx=(4, 0))
        self._btn(row, "🗺  Draw on map", self._open_map,
                  padx=8, pady=4).pack(side=tk.LEFT, padx=(4, 0))
        _hint(".shp · .gpkg · .geojson — any CRS, auto-reprojected to WGS84")

        # ── 1b. Scattered fields ─────────────────────────────────────────────
        self._section(p, "1b. Scattered fields")
        self.v_cluster_aoi = tk.BooleanVar(value=self._saved_cfg.get("cluster_aoi", False))
        self.v_cluster_gap = tk.StringVar(value=str(self._saved_cfg.get("cluster_gap_km", 5.0)))
        self.v_fields_path = tk.StringVar(value=self._saved_cfg.get("fields_path", ""))
        _clrow = tk.Frame(p, bg=BG); _clrow.pack(fill=tk.X, padx=14, pady=(2, 0))
        tk.Checkbutton(_clrow, text="Process scattered field polygons as separate clusters",
                       variable=self.v_cluster_aoi, bg=BG, font=FONT, fg=FG,
                       selectcolor=BG2, activebackground=BG,
                       activeforeground=ACCENT_L).pack(side=tk.LEFT)
        _ffrow = tk.Frame(p, bg=BG); _ffrow.pack(fill=tk.X, padx=14, pady=(2, 0))
        tk.Label(_ffrow, text="Fields file", font=FONT, bg=BG, fg=FG,
                 width=12, anchor="w").pack(side=tk.LEFT)
        self._entry(_ffrow, self.v_fields_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(_ffrow, "Browse",
                  lambda: self._browse_file(self.v_fields_path,
                      [("Spatial files", "*.shp *.gpkg *.geojson"), ("All", "*")]),
                  padx=8, pady=4).pack(side=tk.LEFT, padx=(4, 0))
        _gprow = tk.Frame(p, bg=BG); _gprow.pack(fill=tk.X, padx=14, pady=(2, 0))
        tk.Label(_gprow, text="Merge fields within", font=FONT, bg=BG, fg=FG2).pack(side=tk.LEFT)
        tk.Entry(_gprow, textvariable=self.v_cluster_gap, width=5, font=FONT,
                 bg=BG2, fg=FG, insertbackground=FG, relief="flat", bd=4).pack(side=tk.LEFT, padx=(4, 4))
        tk.Label(_gprow, text="km into one cluster", font=FONT, bg=BG, fg=FG2).pack(side=tk.LEFT)
        _hint("Optional per-field file (e.g. fields_unique.geojson): each cluster is read as a "
              "tight window and the output is masked to the field shapes. Empty = whole AOI.")

        # ── 2. Date Range ───────────────────────────────────────────────────
        self._section(p, "2. Date Range")
        self.v_start = tk.StringVar(value=self._saved_cfg.get("start_date", "2023-01-01"))
        self.v_end   = tk.StringVar(value=self._saved_cfg.get("end_date",   "2023-03-31"))
        for label, var in [("Start date", self.v_start), ("End date", self.v_end)]:
            row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=3)
            tk.Label(row, text=label, font=FONT, bg=BG, fg=FG,
                     width=12, anchor="w").pack(side=tk.LEFT)
            self._entry(row, var, width=13).pack(side=tk.LEFT)
            if HAS_CALENDAR:
                def _open_cal(v=var): self._show_calendar(v)
                self._btn(row, "📅", _open_cal, padx=6, pady=3
                          ).pack(side=tk.LEFT, padx=(4, 0))
        _hint("YYYY-MM-DD format. The pipeline iterates every day in the range.")

        # ── 3. Cloud Cover ──────────────────────────────────────────────────
        self._section(p, "3. Max Cloud Cover (%)")
        cloud_fr = tk.Frame(p, bg=BG); cloud_fr.pack(fill=tk.X, padx=14, pady=6)
        self.v_cloud = tk.IntVar(value=self._saved_cfg.get("max_cloud", 0))
        self.lbl_cloud = tk.Label(cloud_fr, text=f"{self.v_cloud.get()}%",
                                   font=FONT_BOLD, bg=BG, fg=ACCENT_L, width=5)
        self.lbl_cloud.pack(side=tk.RIGHT)
        ttk.Scale(cloud_fr, from_=0, to=100, variable=self.v_cloud,
                  orient=tk.HORIZONTAL,
                  command=lambda v: self.lbl_cloud.configure(
                      text=f"{int(float(v))}%")).pack(side=tk.LEFT, fill=tk.X, expand=True)
        _hint("0 = disabled (SCL mask only).  >0 = skip whole scenes above threshold.")

        # ── 4. Parallel Workers ─────────────────────────────────────────────
        self._section(p, "4. Parallel Workers")
        wk_fr = tk.Frame(p, bg=BG); wk_fr.pack(fill=tk.X, padx=14, pady=6)
        self.v_workers = tk.IntVar(value=self._saved_cfg.get("max_workers", 4))
        self.lbl_workers = tk.Label(wk_fr, text=f"{self.v_workers.get()} threads",
                                     font=FONT_BOLD, bg=BG, fg=ACCENT_L, width=10)
        self.lbl_workers.pack(side=tk.RIGHT)
        ttk.Scale(wk_fr, from_=1, to=8, variable=self.v_workers,
                  orient=tk.HORIZONTAL,
                  command=lambda v: self.lbl_workers.configure(
                      text=f"{int(float(v))} thread{'s' if int(float(v))>1 else ''}")
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        _hint("Each thread downloads + processes one day in parallel (I/O-bound, safe up to 6–8).")

        # ── 5. Network Error Handling ───────────────────────────────────────
        self._section(p, "5. Network Error Handling")
        self.v_retry = tk.BooleanVar(value=self._saved_cfg.get("retry_failed", True))
        self.v_max_retries = tk.IntVar(value=self._saved_cfg.get("max_retries", 2))
        retry_fr = tk.Frame(p, bg=BG); retry_fr.pack(padx=14, pady=(4, 2), anchor="w")
        tk.Checkbutton(retry_fr, text="Retry failed days on network errors",
                       variable=self.v_retry, bg=BG, font=FONT,
                       fg=FG, selectcolor=BG2, activebackground=BG,
                       activeforeground=ACCENT_L).pack(anchor="w")
        ret_sub = tk.Frame(p, bg=BG); ret_sub.pack(padx=28, fill=tk.X, pady=(0, 4))
        tk.Label(ret_sub, text="Max retries:", font=FONT, bg=BG, fg=FG,
                 width=12, anchor="w").pack(side=tk.LEFT)
        for n in (1, 2, 3):
            tk.Radiobutton(ret_sub, text=str(n), variable=self.v_max_retries, value=n,
                           bg=BG, font=FONT, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L).pack(side=tk.LEFT, padx=4)
        _hint("10 s back-off between attempts. Handles transient AWS S3 / EarthSearch errors.")

        # ════════════════════════════════════════════════════════════════════
        # PROCESSING TAB  — Biophysical · Spectral Bands · Spectral Indices
        # ════════════════════════════════════════════════════════════════════
        p = self._make_tab_scroll(t_proc)

        def _hint(text, parent=p):  # redefine for this tab
            tk.Label(parent, text=f"  {text}", font=(_FONT_FAM, 8),
                     bg=BG, fg=FG2).pack(anchor="w", padx=14)

        # ── Biophysical ─────────────────────────────────────────────────────
        self._section(p, "Biophysical  (pure Python NN, no SNAP)")
        # select-all toggle
        self.v_biophys = {}
        tog_bp = tk.Frame(p, bg=BG); tog_bp.pack(padx=14, pady=(4, 0), anchor="w")
        def _toggle_bp():
            new = not all(v.get() for v in self.v_biophys.values())
            for v in self.v_biophys.values(): v.set(new)
        self._btn(tog_bp, "Select all / Deselect all", _toggle_bp,
                  color=BG2, font=(_FONT_FAM, 9)).pack(side=tk.LEFT)
        bp_fr = tk.Frame(p, bg=BG); bp_fr.pack(padx=14, pady=2, anchor="w")
        for b in BIOPHYS:
            self.v_biophys[b] = tk.BooleanVar(value=(self._sel_set is None or b in self._sel_set))
            tk.Checkbutton(bp_fr, text=f"{b:<8}  {BIOPHYS_DESC.get(b, '')}",
                           variable=self.v_biophys[b],
                           bg=BG, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L,
                           font=FONT_MONO).pack(anchor="w")

        # ── Spectral Bands ──────────────────────────────────────────────────
        self._section(p, "Spectral Bands  (reflectance 0–1, 10 m)")
        self.v_bands = {}
        tog_b = tk.Frame(p, bg=BG); tog_b.pack(padx=14, pady=(4, 0), anchor="w")
        def _toggle_bands():
            new = not all(v.get() for v in self.v_bands.values())
            for v in self.v_bands.values(): v.set(new)
        self._btn(tog_b, "Select all / Deselect all", _toggle_bands,
                  color=BG2, font=(_FONT_FAM, 9)).pack(side=tk.LEFT)
        bands_fr = tk.Frame(p, bg=BG); bands_fr.pack(padx=14, pady=4, anchor="w")
        for i, b in enumerate(BANDS):
            self.v_bands[b] = tk.BooleanVar(value=(self._sel_set is None or b in self._sel_set))
            tk.Checkbutton(bands_fr, text=b, variable=self.v_bands[b],
                           bg=BG, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L,
                           font=FONT).grid(row=i // 5, column=i % 5,
                                           sticky="w", padx=6, pady=2)

        # ── Spectral Indices ────────────────────────────────────────────────
        self._section(p, "Spectral Indices")
        self.v_idx = {}
        tog_i = tk.Frame(p, bg=BG); tog_i.pack(padx=14, pady=(4, 0), anchor="w")
        def _toggle_idx():
            new = not all(v.get() for v in self.v_idx.values())
            for v in self.v_idx.values(): v.set(new)
        self._btn(tog_i, "Select all / Deselect all", _toggle_idx,
                  color=BG2, font=(_FONT_FAM, 9)).pack(side=tk.LEFT)
        idx_fr = tk.Frame(p, bg=BG); idx_fr.pack(padx=14, pady=2, anchor="w")
        for idx in INDICES:
            self.v_idx[idx] = tk.BooleanVar(value=(self._sel_set is None or idx in self._sel_set))
            tk.Checkbutton(idx_fr, text=f"{idx:<8}  {INDICES_DESC.get(idx, '')}",
                           variable=self.v_idx[idx],
                           bg=BG, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L,
                           font=FONT_MONO).pack(anchor="w")

        # ── Custom Indices ──────────────────────────────────────────────────
        self._section(p, "Custom Indices  (spectral only, evaluated during run)")
        _hint("Write a numpy expression using B2 B3 B4 B5 B6 B7 B8 B8A B11 B12.")
        _hint("Example: (B8 - B4) / (B8 + B4 + 1e-9)   →  save as  MyNDVI")
        self.v_custom_idx = []   # list of (enabled_BoolVar, name_StringVar, expr_StringVar)
        _saved_ci = self._saved_cfg.get("custom_indices", []) or []
        for i in range(3):
            row = tk.Frame(p, bg=BG)
            row.pack(padx=14, pady=3, fill=tk.X)
            _ci = _saved_ci[i] if i < len(_saved_ci) else {}
            en_var   = tk.BooleanVar(value=bool(_ci.get("name") and _ci.get("expr")))
            name_var = tk.StringVar(value=_ci.get("name", ""))
            expr_var = tk.StringVar(value=_ci.get("expr", ""))
            tk.Checkbutton(row, text="", variable=en_var,
                           bg=BG, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=ACCENT_L)\
              .pack(side=tk.LEFT)
            tk.Label(row, text="Name:", bg=BG, fg=FG2, font=FONT)\
              .pack(side=tk.LEFT, padx=(2, 2))
            tk.Entry(row, textvariable=name_var, width=12, bg=BG2, fg=FG,
                     insertbackground=FG, relief=tk.FLAT, font=FONT)\
              .pack(side=tk.LEFT, padx=(0, 8))
            tk.Label(row, text="Expression:", bg=BG, fg=FG2, font=FONT)\
              .pack(side=tk.LEFT, padx=(0, 2))
            tk.Entry(row, textvariable=expr_var, width=46, bg=BG2, fg=FG,
                     insertbackground=FG, relief=tk.FLAT, font=FONT_MONO)\
              .pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.v_custom_idx.append((en_var, name_var, expr_var))

        # ════════════════════════════════════════════════════════════════════
        # OUTPUT TAB  — Output CRS · Output Folder
        # ════════════════════════════════════════════════════════════════════
        p = self._make_tab_scroll(t_out)

        def _hint(text, parent=p):  # redefine for this tab
            tk.Label(parent, text=f"  {text}", font=(_FONT_FAM, 8),
                     bg=BG, fg=FG2).pack(anchor="w", padx=14)

        # ── Output CRS ──────────────────────────────────────────────────────
        self._section(p, "Output CRS  (optional reproject)")
        self.v_crs = tk.StringVar(value=self._saved_cfg.get("output_crs", "AUTO"))
        crs_fr = tk.Frame(p, bg=BG); crs_fr.pack(padx=14, fill=tk.X, pady=4)
        common_crs = ["AUTO (UTM from AOI centroid)", "EPSG:4326 (WGS84)",
                      "EPSG:3857 (Web Mercator)", "EPSG:32632 (UTM 32N)",
                      "EPSG:32633 (UTM 33N)", "EPSG:32634 (UTM 34N)"]
        self.crs_cb = ttk.Combobox(crs_fr, values=common_crs, font=FONT, width=34)
        cur = self.v_crs.get()
        self.crs_cb.set("AUTO (UTM from AOI centroid)" if cur in ("AUTO", "", "UTM") else cur)
        self.crs_cb.pack(side=tk.LEFT)
        def _crs_changed(e=None):
            m = re.search(r"EPSG:\d+", self.crs_cb.get(), re.IGNORECASE)
            self.v_crs.set(m.group(0).upper() if m else "AUTO")
        self.crs_cb.bind("<<ComboboxSelected>>", _crs_changed)
        self.crs_cb.bind("<FocusOut>", _crs_changed)
        tk.Label(crs_fr, text=" or type:", font=(_FONT_FAM, 8),
                 bg=BG, fg=FG2).pack(side=tk.LEFT, padx=(8, 2))
        self._entry(crs_fr, self.v_crs, width=13).pack(side=tk.LEFT)
        _hint("AUTO = keep native UTM (fastest). Any EPSG code triggers full reproject.")

        # ── Output Folder ───────────────────────────────────────────────────
        self._section(p, "Output Folder")
        self.v_out = tk.StringVar(value=self._saved_cfg.get("out_dir", ""))
        grp = tk.Frame(p, bg=BG); grp.pack(fill=tk.X, padx=14, pady=4)
        row = tk.Frame(grp, bg=BG); row.pack(fill=tk.X)
        self._entry(row, self.v_out).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row, "…", lambda: self._browse_dir(self.v_out),
                  padx=10, pady=4).pack(side=tk.LEFT, padx=(4, 0))
        _hint("Structure: index=<PRODUCT>/aoi=<LABEL>/S2_YYYYMMDD_000_<LABEL>_X_<PRODUCT>.tif")

        # ── Overwrite / Skip ────────────────────────────────────────────────
        self._section(p, "Existing Files")
        self.v_overwrite = tk.BooleanVar(
            value=self._saved_cfg.get("overwrite", False))
        ow_fr = tk.Frame(p, bg=BG); ow_fr.pack(padx=14, pady=6, anchor="w")
        tk.Radiobutton(ow_fr, text="Skip  (keep existing files, faster re-run)",
                       variable=self.v_overwrite, value=False,
                       bg=BG, font=FONT, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w")
        tk.Radiobutton(ow_fr, text="Overwrite  (re-process and replace all files)",
                       variable=self.v_overwrite, value=True,
                       bg=BG, font=FONT, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=ACCENT_L).pack(anchor="w")
        _hint("Skip is useful when extending a date range or resuming an interrupted run.")

        # ════════════════════════════════════════════════════════════════════
        # RASTER CALC TAB
        # ════════════════════════════════════════════════════════════════════
        p = self._make_tab_scroll(t_calc)

        def _hint_c(text, parent=p):
            tk.Label(parent, text=f"  {text}", font=(_FONT_FAM, 8),
                     bg=BG, fg=FG2).pack(anchor="w", padx=14, pady=(0, 2))

        # ── A. Input ─────────────────────────────────────────────────────
        self._section(p, "A. Input Folder")
        self.v_calc_input = tk.StringVar(value="")
        row = tk.Frame(p, bg=BG); row.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row, text="Input folder", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row, textvariable=self.v_calc_input, font=FONT,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row, "…", lambda: self._browse_dir(self.v_calc_input)
                  ).pack(side=tk.LEFT, padx=(4, 0))
        _hint_c("Tip: use the Output Folder from step 3 or any folder with S2_*_BAND.tif files.")

        scan_fr = tk.Frame(p, bg=BG); scan_fr.pack(fill=tk.X, padx=14, pady=4)
        self._btn(scan_fr, "🔍  Scan bands", self._scan_calc_bands, color=BG2
                  ).pack(side=tk.LEFT)
        self.v_calc_bands_info = tk.StringVar(value="← click to detect available bands")
        tk.Label(scan_fr, textvariable=self.v_calc_bands_info,
                 font=(_FONT_FAM, 9, "italic"), bg=BG, fg=ACCENT_L
                 ).pack(side=tk.LEFT, padx=(10, 0))

        # ── B. Expression ────────────────────────────────────────────────
        self._section(p, "B. Expression  (numpy)")
        _hint_c("Use band names as variables: B2, B3, B4, B8, NDVI, LAI, CCC, … and np for numpy.")
        _hint_c("Examples:  (B8 - B4) / (B8 + B4)   |   np.sqrt(LAI)   |   B8 / B4")
        expr_outer = tk.Frame(p, bg=BG2, bd=1, relief="flat")
        expr_outer.pack(fill=tk.X, padx=14, pady=(2, 6))
        self.calc_expr = tk.Text(expr_outer, height=3, font=(_FONT_FAM, 11),
                                  bg=BG2, fg=FG, insertbackground=FG,
                                  relief="flat", bd=8, wrap=tk.WORD)
        self.calc_expr.insert("1.0", "(B8 - B4) / (B8 + B4)")
        self.calc_expr.pack(fill=tk.X)

        # ── C. Output ────────────────────────────────────────────────────
        self._section(p, "C. Output")
        self.v_calc_band_name = tk.StringVar(value="CUSTOM")
        row2 = tk.Frame(p, bg=BG); row2.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row2, text="Output band name", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row2, textvariable=self.v_calc_band_name, font=FONT,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=16).pack(side=tk.LEFT)
        self.v_calc_output = tk.StringVar(value="")
        row3 = tk.Frame(p, bg=BG); row3.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(row3, text="Output folder", font=FONT, bg=BG, fg=FG,
                 width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row3, textvariable=self.v_calc_output, font=FONT,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(row3, "…", lambda: self._browse_dir(self.v_calc_output)
                  ).pack(side=tk.LEFT, padx=(4, 0))
        _hint_c("Output files: {scene}_{band_name}.tif, mirroring the input subfolder structure.")

        self.btn_calc = self._btn(p, "▶  Run Calculator", self._on_run_calc,
                                   pady=10, font=(_FONT_FAM, 12, "bold"))
        self.btn_calc.pack(fill=tk.X, padx=14, pady=(4, 2))
        self._btn(p, "■  Stop", self._on_stop_calc, color="#C62828",
                  pady=8, font=(_FONT_FAM, 10, "bold")
                  ).pack(fill=tk.X, padx=14, pady=(0, 10))

    def _build_log(self, parent):
        # dep header
        hdr = tk.Frame(parent, bg=BG2); hdr.pack(fill=tk.X)
        tk.Frame(hdr, bg=ACCENT, width=4).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(hdr, text="  Dependencies", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT_L, pady=7).pack(side=tk.LEFT)
        self._btn(hdr, "⟳  Re-check", self._run_dep_check,
                  pady=4, padx=8, font=(_FONT_FAM, 8, "bold")
                  ).pack(side=tk.RIGHT, padx=8, pady=4)
        self.dep_rows = tk.Frame(parent, bg=SURFACE)
        self.dep_rows.pack(fill=tk.X, padx=0, pady=(0, 2))

        # log header with Export + Clear buttons (matching SAR Foundry)
        log_hdr = tk.Frame(parent, bg=BG2); log_hdr.pack(fill=tk.X)
        tk.Frame(log_hdr, bg=GREEN, width=4).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(log_hdr, text="  Pipeline Log", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT_L, pady=7).pack(side=tk.LEFT)

        def _export_log():
            default = f"opticalfoundry_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            path = filedialog.asksaveasfilename(
                defaultextension=".txt", initialfile=default,
                filetypes=[("Text files", "*.txt"), ("All", "*")])
            if path:
                content = self.log_box.get("1.0", tk.END)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

        def _clear_log():
            self.log_box.configure(state=tk.NORMAL)
            self.log_box.delete("1.0", tk.END)
            self.log_box.configure(state=tk.DISABLED)

        self._btn(log_hdr, "💾 Export", _export_log,
                  pady=4, padx=8, font=(_FONT_FAM, 8)).pack(side=tk.RIGHT, padx=(0, 4), pady=4)
        self._btn(log_hdr, "🗑 Clear", _clear_log,
                  pady=4, padx=8, font=(_FONT_FAM, 8), color=BG2
                  ).pack(side=tk.RIGHT, pady=4)

        self.log_box = scrolledtext.ScrolledText(
            parent, font=FONT_MONO, bg=SURFACE, fg="#90A4AE",
            insertbackground=FG, wrap=tk.WORD, relief="flat",
            state=tk.DISABLED, padx=8, pady=6)
        self.log_box.pack(fill=tk.BOTH, expand=True)

        # coloured log tags
        self.log_box.tag_configure("ok",    foreground=GREEN)
        self.log_box.tag_configure("error", foreground=RED)
        self.log_box.tag_configure("warn",  foreground=GOLD)
        self.log_box.tag_configure("info",  foreground=ACCENT_L)
        self.log_box.tag_configure("head",  foreground=FG, font=(_FONT_FAM, 9, "bold"))
        self.log_box.tag_configure("dim",   foreground="#546E7A")
        self.log_box.tag_configure("date",  foreground="#CE93D8")

        # ── per-phase progress bars (matching SAR Foundry style) ──────────
        prog_frame = tk.Frame(parent, bg=SURFACE)
        prog_frame.pack(fill=tk.X, padx=6, pady=(4, 0))
        self._prog_bars = {}
        for _phase, _lbl in [("process", "Processing")]:
            _r = tk.Frame(prog_frame, bg=SURFACE); _r.pack(fill=tk.X, pady=1)
            tk.Label(_r, text=f"{_lbl}:", font=(_FONT_FAM, 8, "bold"),
                     bg=SURFACE, fg=FG2, width=11, anchor="w").pack(side=tk.LEFT)
            # live ETA pinned to the far RIGHT of the bar, in red (SAR-style)
            _stat = tk.StringVar(value="")
            tk.Label(_r, textvariable=_stat, font=FONT_MONO,
                     bg=SURFACE, fg=RED, width=14, anchor="e").pack(side=tk.RIGHT, padx=(4, 6))
            _bar = ttk.Progressbar(_r, mode="determinate", maximum=100, value=0)
            _bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
            _cnt = tk.StringVar(value="")
            tk.Label(_r, textvariable=_cnt, font=FONT_MONO,
                     bg=SURFACE, fg=FG2, width=8, anchor="w").pack(side=tk.LEFT)
            _txt = tk.StringVar(value="")
            tk.Label(_r, textvariable=_txt, font=FONT_MONO,
                     bg=SURFACE, fg=FG2, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._prog_bars[_phase] = (_bar, _cnt, _txt, _stat)

        # ETA row — above the progress bars so it's always visible
        eta_row = tk.Frame(parent, bg=SURFACE)
        eta_row.pack(fill=tk.X, padx=8, pady=(4, 2))
        self._elapsed_var = tk.StringVar(value="")
        tk.Label(eta_row, textvariable=self._elapsed_var,
                 font=FONT_MONO, bg=SURFACE, fg=FG2, anchor="w").pack(side=tk.LEFT)

        # overall indeterminate spinner
        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(0, 2))

    # ── progress helpers ──────────────────────────────────────────────────────
    def _update_progress(self, phase, current, total, label=""):
        def _do():
            if not hasattr(self, "_prog_bars") or phase not in self._prog_bars:
                return
            bar, cnt_var, txt_var, stat_var = self._prog_bars[phase]
            pct = (current / total * 100) if total > 0 else 0
            bar["value"] = pct
            cnt_var.set(f"{current}/{total}" if total > 0 else "")
            txt_var.set(label[:40] if label else "")

            # ── ETA (red, pinned right of the bar) + elapsed ────────────────
            if not hasattr(self, "_pipeline_start") or not self._pipeline_start:
                return
            elapsed = time.time() - self._pipeline_start

            def _fmt(secs):
                secs = int(secs)
                if secs < 60:   return f"{secs}s"
                if secs < 3600: return f"{secs//60}m {secs%60:02d}s"
                return f"{secs//3600}h {(secs%3600)//60:02d}m"

            self._elapsed_var.set(f"Elapsed: {_fmt(elapsed)}")

            if current > 0 and total > current:
                eta_secs = (elapsed / current) * (total - current)
                stat_var.set(f"ETA ~{_fmt(eta_secs)}")
            elif current >= total > 0:
                stat_var.set("✓ done")
            else:
                stat_var.set("")

        self.after(0, _do)

    def _reset_all_progress(self):
        if not hasattr(self, "_prog_bars"):
            return
        for bar, cnt_var, txt_var, stat_var in self._prog_bars.values():
            bar["value"] = 0
            cnt_var.set("")
            txt_var.set("")
            stat_var.set("")
        if hasattr(self, "_elapsed_var"): self._elapsed_var.set("")
        self._pipeline_start = None

    # ── helpers ───────────────────────────────────────────────────────────────
    def _open_map(self):
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
<title>Optical Foundry — Draw AOI</title>
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
    st("AOI sent to Optical Foundry! You can close this window.","#4CAF50");
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
                "window = webview.create_window('Draw AOI — Optical Foundry', html=html,\n"
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

            with tempfile.NamedTemporaryFile(mode="w", suffix="_aoi_map.html",
                                              delete=False, encoding="utf-8") as f:
                f.write(fb_html); html_path = f.name
            server = HTTPServer(("localhost", port), _H)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            webbrowser.open(f"file:///{html_path.replace(os.sep, '/')}")
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
                    fc = json.dumps({"type":"FeatureCollection","features":[result["geojson"]]}, indent=2)
                    geo_path = _drawn_aoi_path()
                    with open(geo_path, "w", encoding="utf-8") as f:
                        f.write(fc)
                    # Tk vars are not thread-safe — set on the main thread
                    self.after(0, lambda p=geo_path: (
                        self.v_aoi.set(p),
                        self._log(f"AOI set from map: {p}")))
                finally:
                    # always release the temp server + socket + HTML (even on timeout)
                    try: server.shutdown()
                    except Exception: pass
                    try: server.server_close()
                    except Exception: pass
                    try: os.remove(html_path)
                    except Exception: pass
            threading.Thread(target=_wait, daemon=True).start()

    def _browse_file(self, var, types):
        p = filedialog.askopenfilename(filetypes=types)
        if p: var.set(p)

    def _browse_dir(self, var):
        p = filedialog.askdirectory()
        if p: var.set(p)

    def _show_calendar(self, var):
        import datetime as dt
        popup = tk.Toplevel(self); popup.title("Select date")
        popup.resizable(False, False); popup.grab_set()
        try: d = dt.datetime.strptime(var.get(), "%Y-%m-%d").date()
        except: d = dt.date.today()
        from tkcalendar import Calendar as _Cal
        popup.configure(bg=BG)
        cal = _Cal(popup, selectmode="day", year=d.year, month=d.month, day=d.day,
                   date_pattern="yyyy-mm-dd", background=BG2, foreground=FG,
                   selectbackground=ACCENT, headersbackground=ACCENT2,
                   headersforeground=WHITE, normalbackground=BG,
                   normalforeground=FG, font=FONT)
        cal.pack(padx=12, pady=12)
        def _sel():
            var.set(cal.get_date()); popup.destroy()
        self._btn(popup, "  Select  ", _sel, pady=8).pack(pady=(0, 12))
        popup.update_idletasks()
        popup.geometry(f"+{self.winfo_x()+200}+{self.winfo_y()+200}")

    def _log(self, text):
        def _a():
            self.log_box.configure(state=tk.NORMAL)
            tl = text.lower()
            if text.startswith("=") or text.startswith("-"):
                tag = "head"
            elif any(x in tl for x in ("outputs in", "done", "[ok", "all packages",
                                        "pipeline complete — no errors")):
                tag = "ok"
            elif any(x in tl for x in ("error", "fail", "err]", "[error")):
                tag = "error"
            elif any(x in tl for x in ("warn", "skip", "missing", "diag", "cloud")):
                tag = "warn"
            elif any(x in tl for x in ("started", "date range", "aoi:", "crs:",
                                        "parallel workers", "output:")):
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
        self.after(0, _a)

    # ── dep check ─────────────────────────────────────────────────────────────
    def _run_dep_check(self):
        for w in self.dep_rows.winfo_children(): w.destroy()
        tk.Label(self.dep_rows, text="Checking...", font=("Calibri", 9, "italic"),
                 fg=LTBLUE, bg=DARK).pack(anchor="w")
        def _check():
            results = check_dependencies()
            self.after(0, lambda: self._show_dep(results))
        threading.Thread(target=_check, daemon=True).start()

    def _show_dep(self, results):
        self._dep_results = results
        for w in self.dep_rows.winfo_children(): w.destroy()
        all_ok = True
        for name, (ok, detail) in results.items():
            row = tk.Frame(self.dep_rows, bg=SURFACE); row.pack(fill=tk.X, pady=1)
            if ok is True:        icon, fg = "✓", GREEN
            elif ok == "install": icon, fg = "⚠", GOLD;  all_ok = False
            else:                 icon, fg = "✗", RED;   all_ok = False
            tk.Label(row, text=f"  {icon} ", font=(_FONT_FAM, 10, "bold"),
                     fg=fg, bg=SURFACE, width=4).pack(side=tk.LEFT)
            tk.Label(row, text=name, font=FONT_BOLD,
                     fg=FG, bg=SURFACE, width=20, anchor="w").pack(side=tk.LEFT)
            short = detail.split("\n")[0][:55]
            tk.Label(row, text=short, font=FONT,
                     fg=ACCENT_L if ok is True else GOLD if ok == "install" else RED,
                     bg=SURFACE, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
            if ok == "install":
                pkgs = detail.split("\n")[0].replace("Missing: ", "").split(", ")
                def _inst(packages=pkgs):
                    btn["state"] = tk.DISABLED
                    self._log(f"[INSTALL] Installing: {' '.join(packages)} ...")
                    def _run():
                        r = subprocess.run(
                            [sys.executable, "-m", "pip", "install"] + packages + ["--quiet"],
                            capture_output=True, text=True)
                        if r.returncode == 0:
                            self._log("[INSTALL] Done — restarting...")
                            self.after(1500, lambda: (
                                subprocess.Popen([sys.executable] + sys.argv), self.destroy()))
                        else:
                            self._log(f"[INSTALL] Failed: {r.stderr[:200]}")
                            self.after(500, self._run_dep_check)
                    threading.Thread(target=_run, daemon=True).start()
                btn = self._btn(row, "INSTALL", _inst, color=GOLD,
                                font=(_FONT_FAM, 8, "bold"), padx=8, pady=2)
                btn.pack(side=tk.LEFT, padx=(4, 4))
        tk.Frame(self.dep_rows, bg=ACCENT, height=1).pack(fill=tk.X, pady=(4, 2))
        msg = "All dependencies OK" if all_ok else "Some packages missing"
        tk.Label(self.dep_rows, text=f"  {msg}", font=FONT_BOLD,
                 fg=GREEN if all_ok else GOLD, bg=SURFACE).pack(anchor="w", pady=(0, 4))
        self._log("-- Dependency check --")
        for name, (ok, detail) in results.items():
            icon = "OK" if ok is True else "PKG" if ok == "install" else "ERR"
            self._log(f"  [{icon:5s}] {name}: {detail.split(chr(10))[0]}")
        self._log("----------------------")

    # ── Raster Calculator ─────────────────────────────────────────────────────

    def _scan_calc_bands(self):
        folder = self.v_calc_input.get().strip()
        if not folder or not os.path.isdir(folder):
            self.v_calc_bands_info.set("⚠ Set a valid input folder first")
            return
        tifs = (glob.glob(os.path.join(folder, "**", "*.tif"), recursive=True) +
                glob.glob(os.path.join(folder, "*.tif")))
        bands_found, prefixes = set(), set()
        for f in tifs:
            m = _S2_BAND_RE.search(os.path.basename(f))
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
                _s2_raster_calc_worker(folder_in, expr, band_out, folder_out,
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

    # ── pipeline ──────────────────────────────────────────────────────────────
    def _on_start(self):
        if self._running: return
        # validate
        if not self.v_aoi.get().strip():
            messagebox.showerror("AOI missing", "Please select an AOI file."); return
        if not os.path.isfile(self.v_aoi.get().strip()):
            messagebox.showerror("AOI not found", f"File not found: {self.v_aoi.get()}"); return
        if not self.v_out.get().strip():
            messagebox.showerror("Output missing", "Please set the COG output folder."); return
        # dates: YYYY-MM-DD and start ≤ end (else a silent "0 days" run) (S3)
        import datetime as _dt
        try:
            _sd = _dt.datetime.strptime(self.v_start.get().strip(), "%Y-%m-%d").date()
            _ed = _dt.datetime.strptime(self.v_end.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("Dates", "Dates must be in YYYY-MM-DD format."); return
        if _sd > _ed:
            messagebox.showerror("Dates", "Start date must be on or before the end date."); return
        _has_custom = any(
            en.get() and name.get().strip() and expr.get().strip()
            for en, name, expr in self.v_custom_idx
        )
        if not any(list(self.v_biophys.values()) + list(self.v_bands.values()) +
                   list(self.v_idx.values())) and not _has_custom:
            messagebox.showwarning("Nothing selected",
                "Select at least one preset output or define a Custom Index."); return
        if self._dep_results:
            missing = [n for n, (ok, _) in self._dep_results.items()
                       if ok not in (True, "info")]
            if missing:
                messagebox.showerror("Missing dependencies",
                    "Fix missing dependencies before starting:\n" + "\n".join(missing)); return

        selected = ([b for b, v in self.v_biophys.items() if v.get()] +
                    [b for b, v in self.v_bands.items()   if v.get()] +
                    [b for b, v in self.v_idx.items()     if v.get()])

        custom_indices = []
        for en_var, name_var, expr_var in self.v_custom_idx:
            if en_var.get():
                n = name_var.get().strip()
                e = expr_var.get().strip()
                if n and e:
                    custom_indices.append({"name": n, "expr": e})

        cfg = {
            "aoi_path":         self.v_aoi.get().strip(),
            "aoi_label":        re.sub(r"[^A-Za-z0-9_-]", "_", Path(self.v_aoi.get().strip()).stem),
            "start_date":       self.v_start.get(),
            "end_date":         self.v_end.get(),
            "max_cloud":        int(self.v_cloud.get()),
            "out_dir":          self.v_out.get().strip(),
            "selected_outputs": selected,
            "output_crs":       self.v_crs.get().strip(),
            "max_workers":      int(self.v_workers.get()),
            "retry_failed":     self.v_retry.get(),
            "max_retries":      int(self.v_max_retries.get()),
            "overwrite":        self.v_overwrite.get(),
            "custom_indices":   custom_indices,
            "cluster_aoi":      bool(self.v_cluster_aoi.get()),
            "cluster_gap_km":   _safe_float(self.v_cluster_gap.get(), 5.0),
            "fields_path":      (self.v_fields_path.get() or "").strip(),
        }
        _save_config({k: cfg[k] for k in (
            "aoi_path", "start_date", "end_date", "max_cloud",
            "out_dir", "output_crs", "max_workers", "retry_failed", "max_retries",
            "overwrite", "cluster_aoi", "cluster_gap_km", "fields_path",
            "selected_outputs", "custom_indices")})

        self._last_cfg = dict(cfg)   # snapshot for the run-history log
        self._running = True
        self._stop_event.clear()
        self._pipeline_start = time.time()
        self._reset_all_progress()
        self.btn_run.configure(state=tk.DISABLED, bg="#455A64")
        self.btn_stop.configure(state=tk.NORMAL, bg=RED)
        self.progress.start(12)
        cfg["stop_event"] = self._stop_event

        self._thread = threading.Thread(
            target=run_pipeline,
            args=(cfg, self._log, self._on_done),
            kwargs={"progress_cb": self._update_progress},
            daemon=True)
        self._thread.start()

    def _on_stop(self):
        self._log("\n[Stopping — cancelling pending days (active HTTP requests will complete then exit)…]")
        self._running = False
        self._stop_event.set()

    def _show_history(self):
        import json as _json
        hist_path = os.path.join(_SCRIPT_DIR, "optical_foundry_history.json")
        if not os.path.isfile(hist_path):
            messagebox.showinfo("Run history", "No runs recorded yet."); return
        try:
            history = _json.loads(open(hist_path, encoding="utf-8").read())
        except Exception:
            messagebox.showerror("Run history", "Could not read history file."); return
        win = tk.Toplevel(self); win.title("Run History")
        win.configure(bg=BG); win.geometry("660x400")
        tk.Label(win, text="Last 50 pipeline runs", font=FONT_BOLD,
                 bg=BG, fg=ACCENT_L).pack(anchor="w", padx=10, pady=(8, 4))
        hdr = f"{'Time':<18}{'St':<4}{'AOI':<20}{'Dates':<26}{'Cloud'}"
        tk.Label(win, text=hdr, font=FONT_MONO, bg=BG, fg=FG2,
                 anchor="w", justify="left").pack(anchor="w", padx=12)
        list_fr = tk.Frame(win, bg=BG)
        list_fr.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        lb = tk.Listbox(list_fr, font=FONT_MONO, bg=SURFACE, fg=FG,
                        selectbackground=ACCENT, selectforeground=BG,
                        relief="flat", bd=0, highlightthickness=0,
                        activestyle="none")
        sb = tk.Scrollbar(list_fr, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for h in history:
            _dates = f"{h.get('start_date','?')} → {h.get('end_date','?')}"
            lb.insert(tk.END,
                f"{str(h.get('timestamp','?')):<18}"
                f"{('✓' if h.get('success') else '✗'):<4}"
                f"{str(h.get('aoi','?'))[:19]:<20}"
                f"{_dates:<26}"
                f"{h.get('cloud','?')}%")

    def _on_done(self, success):
        # ── append to run history (last 50) ──────────────────────────────
        try:
            import json as _json
            from datetime import datetime as _dt
            hist_path = os.path.join(_SCRIPT_DIR, "optical_foundry_history.json")
            history = []
            if os.path.isfile(hist_path):
                try:
                    history = _json.loads(open(hist_path, encoding="utf-8").read())
                except Exception:
                    history = []
            c = getattr(self, "_last_cfg", {})
            history.insert(0, {
                "timestamp":  _dt.now().strftime("%Y-%m-%d %H:%M"),
                "success":    success,
                "aoi":        os.path.basename(c.get("aoi_path", "?")),
                "start_date": c.get("start_date", "?"),
                "end_date":   c.get("end_date", "?"),
                "cloud":      c.get("max_cloud", "?"),
                "out_dir":    c.get("out_dir", "?"),
            })
            with open(hist_path, "w", encoding="utf-8") as _f:
                _json.dump(history[:50], _f, indent=2)
        except Exception:
            pass

        def _u():
            self._running = False
            self.btn_run.configure(state=tk.NORMAL, bg=ACCENT)
            self.btn_stop.configure(state=tk.DISABLED, bg="#455A64")
            self.progress.stop()
            # show final elapsed time; clear ETA
            if hasattr(self, "_pipeline_start") and self._pipeline_start:
                elapsed = time.time() - self._pipeline_start
                def _fmt(s):
                    s = int(s)
                    if s < 60:   return f"{s}s"
                    if s < 3600: return f"{s//60}m {s%60:02d}s"
                    return f"{s//3600}h {(s%3600)//60:02d}m"
                if hasattr(self, "_elapsed_var"):
                    self._elapsed_var.set(f"Total: {_fmt(elapsed)}")
            # final status on the bar's (red) stat label
            if getattr(self, "_prog_bars", None) and "process" in self._prog_bars:
                self._prog_bars["process"][3].set("✓ done" if success else "■ stopped")
        self.after(0, _u)


if __name__ == "__main__":
    app = App()
    app.mainloop()

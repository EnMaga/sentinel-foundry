# Sentinel Foundry - SAR & Optical 🛰️

Two desktop GUIs for building Sentinel-1 and Sentinel-2 analysis-ready data pipelines — point-and-click, self-installing, no credentials needed for S2.

| Tool | Script | Satellite | Source |
|------|--------|-----------|--------|
| **SAR Foundry** | `s1_pipeline_ui.py` | Sentinel-1 GRD | CDSE S3 (fastest), ASF (NASA Earthdata), or CDSE OData |
| **Optical Foundry** | `s2_pipeline_ui.py` | Sentinel-2 L2A | AWS EarthSearch (public) |

> 📖 **Using Sentinel Foundry in your research?** Please cite it: click
> **"Cite this repository"** in the sidebar (APA/BibTeX from
> [CITATION.cff](CITATION.cff)), or cite *Magazzino, E. (2026). Sentinel
> Foundry (v1.0.14) [Computer software]. https://github.com/EnMaga/sentinel-foundry*

---

## 🌐 New here? Start with the tutorial website

### **▶ [enmaga.github.io/sentinel-foundry](https://enmaga.github.io/sentinel-foundry/)**

A guided, **per-tab walkthrough of both apps** with example outputs — the fastest way to see what Sentinel Foundry does and how each screen works before you install anything.

---

## What's new in v1.0.14

- **Field-clustering in batch mode (BETA — not yet live-tested).** The Batch tab now honours the section-3b **Cluster AOI** toggle and crops each AOI to tight per-field clusters, matching the single-AOI tool. See the ⚠ note under [Multiple-AOI batch runner](#multiple-aoi-batch-runner).
- **Pipeline-first merged batch layout.** Batch finals for every AOI now land together in one `<preset>_<speckle>\ASC|DSC\` folder (kept apart by the AOI label in each filename) instead of per-AOI `<AOI>\<combo>\` folders. ⚠ Pre-v1.0.14 batch outputs won't be found and will reprocess — see [Output folder layout](#output-folder-layout--reusing-existing-work).
- **Cheaper batch resume (`skip_if_final`).** A stopped/re-run batch no longer re-downloads or reprocesses any date that already has a final or `.done` marker — verified working.
- **Batch progress bars** — a top **AOI k/N** bar plus a **Batch** chunk bar (`chunk k/~N`).
- **Per-AOI speckle tuning in Batch Per-AOI mode** (⚙ per row) and **per-column ⇩ apply-to-all** buttons; per-row **🗑 remove** + **🗑 Delete all**.
- **Completion reminder** after a clean single-AOI run (run coverage check / delete `.done`).
- **"Continue downloading / Check missing dates"** — the footer button (formerly "Download missing / failed dates") relabelled; behaviour unchanged.
- **Robustness** — download truncation size-guard (ASF + OData), killable S3/CDSE downloads, date-aware chunking (never splits a multi-frame date across chunks), and an `os._exit` on window-close so no orphan process locks the download drive (both SAR & Optical Foundry).

---

## Quick start

**Option A — download a ready-made build (no git needed):** grab the zip for
your OS from the [Releases page](https://github.com/EnMaga/sentinel-foundry/releases),
extract it anywhere, and double-click **`Sentinel Foundry.exe`** (Windows),
**`Sentinel Foundry.app`** (macOS) or the **`Sentinel Foundry`** binary
(Linux). Keep everything together in the extracted folder —
the launcher looks for the pipeline scripts next to itself and builds its
`.venv` there on first run. You still need **Python 3.11/3.12, ESA SNAP and
GDAL** installed (see "Requires manual install" below). The build is unsigned,
so the first launch shows a security warning — see
"Opening the app (unsigned build)".

> ⚠️ **macOS / Linux builds are new and not yet field-tested** — the app has
> so far been developed and validated on Windows. They should work (the
> launcher is plain Python/Tkinter), but if anything misbehaves please
> [open an issue](https://github.com/EnMaga/sentinel-foundry/issues) — reports
> are very welcome.

**Option B — clone and run from source:**

```bash
git clone https://github.com/EnMaga/sentinel-foundry.git
cd sentinel-foundry
```

**Launcher (recommended — one window, handles setup):**
```cmd
python sentinel_foundry.py
```
The launcher opens a single window with two buttons — **SAR Foundry** (Sentinel-1) and **Optical Foundry** (Sentinel-2). When you pick a tool the first time, the launcher creates the shared `.venv` and installs **that tool's** Python packages (`requirements.txt` for SAR, `requirements_s2.txt` for Optical), then starts it; a per-tool marker means it won't reinstall on later launches. The window also links to the manual, non-Python prerequisites (**ESA SNAP with the Microwave Toolbox**, **GDAL**) — these can't be pip-installed and are needed by SAR Foundry. For a clickable desktop icon, see the next step.

**Desktop icon (optional, any OS):** cloning a repo can't place a desktop shortcut for you, so run this once after cloning to create one with the Sentinel Foundry icon:
```cmd
python install_sentinelfoundry.py
```
It detects your system and makes the right launcher — a `.lnk` on Windows, a `.desktop` entry on Linux, or a small `.app` bundle on macOS — all pointing at `sentinel_foundry.py`.

**Run a single tool directly (optional):** the launcher is the easy path, but you can also start a tool on its own — `python s1_pipeline_ui.py` (SAR) or `python s2_pipeline_ui.py` (Optical). On first run each script creates the shared `.venv` and installs its dependencies; afterwards `.venv\Scripts\python.exe <script>` (Windows) or `python3 <script>` (macOS / Linux) skips the bootstrap and starts faster.

---

## SAR Foundry — Sentinel-1 GRD → ARD → COG Indices

Converts raw Sentinel-1 GRD scenes into ARD (Analysis Ready Data): calibrated, speckle-filtered, geocoded to UTM, clipped to your AOI.

**Outputs — 5 single-band COG GeoTIFFs per scene, organised by orbit:**

```
output/
  ASC/
    S1_YYYYMMDD_SNAP_AOI_[label]_[speckle]_[dem]_[graph]_A_ASC_VV.tif
    S1_YYYYMMDD_SNAP_AOI_[label]_[speckle]_[dem]_[graph]_A_ASC_VH.tif
    S1_YYYYMMDD_SNAP_AOI_[label]_[speckle]_[dem]_[graph]_A_ASC_CR.tif    # VH/VV cross-ratio
    S1_YYYYMMDD_SNAP_AOI_[label]_[speckle]_[dem]_[graph]_A_ASC_RVI.tif   # 4·VH/(VV+VH)
    S1_YYYYMMDD_SNAP_AOI_[label]_[speckle]_[dem]_[graph]_A_ASC_DIFF.tif  # VH−VV (linear)
  DSC/
    ...
```

`[graph]` records the preprocessing chain (section 2): `sigma0` or `gamma0` for the built-in presets, or — for a custom graph — the imported XML's file name reduced to alphanumerics (e.g. `my_chain.xml` → `mychain`). So a σ⁰ run with no speckle filter on Copernicus 30 m DEM is tagged `none_cop30_sigma0`.

`[speckle]` is the speckle filter selected in section 2b:

| UI selection | Tag in filename |
|---|---|
| Lee Sigma 7×7 (default) | `lee` |
| Gamma Map 7×7 | `gamma` |
| Custom → Lee Sigma | `lee_sigma` |
| Custom → Refined Lee | `refined_lee` |
| Custom → Gamma Map | `gamma_map` |
| Custom → IDAN | `idan` |
| Custom → Boxcar / Median / Frost | `boxcar` / `median` / `frost` |
| Custom → None (no filter) | `none` |

All outputs: float32 linear power, nodata = −9999, DEFLATE compressed, 10 m pixel spacing.  
Optional dB copies (suffix `_dB`) can be enabled in section 9 of the GUI — for display only.

**Custom Bands:** up to 3 user-defined expressions evaluated per scene using numpy. Available variables: `VV`, `VH`, `CR`, `RVI`, `DIFF`, `np`, `NODATA` (all linear intensity). Examples: `VH/VV` · `(VV-VH)/(VV+VH)` · `np.sqrt(VH/VV)`. Custom outputs are always saved as `_lin.tif` and follow the same COG profile as preset bands.

**Pipeline tabs (GUI):**

| Tab | Section | Description |
|-----|---------|-------------|
| ⬇ Download | 1 | Sentinel-1 Source — download from CDSE S3, ASF, Copernicus CDSE, or use existing `.SAFE` folder |
| ⬇ Download | 3 | Area of Interest — `.shp`, `.gpkg`, `.geojson`, or draw on interactive map |
| ⬇ Download | 4 | Date Range — with optional calendar picker |
| ⬇ Download | 5 | Orbit Direction — ASC, DSC, or Both |
| ⬇ Download | 5b | Satellites — pick S1A / S1B / S1C (e.g. S1C-only to backfill; all sources incl. ASF now serve S1C — see below) |
| ⬇ Download | 6 | ASF Credentials — NASA Earthdata token or username/password |
| ⬇ Download | 6b | Copernicus CDSE Credentials — refresh/offline token only for CDSE download |
| ⬇ Download | **6c** | **CDSE S3 Keys — direct from object store (no unzip)** |
| ⬇ Download | **7** | **Parallel Downloads — 1–5 scenes simultaneously with real-time Mbps display** |
| ⚙ Processing | 2 | Preprocessing Graph — σ⁰ Standard, γ⁰ RTC, or custom XML |
| ⚙ Processing | 2b | Speckle Filter — Lee Sigma, Gamma Map, or fully configurable custom |
| ⚙ Processing | 2c | DEM for Terrain Correction — Copernicus 30m default |
| ⚙ Processing | 8 | Output Bands — toggle which preset bands to compute |
| ⚙ Processing | — | **Custom Bands — up to 3 user-defined numpy expressions (VV, VH, CR, RVI, DIFF)** |
| ⚙ Processing | 9 | Output Scale — Linear and/or dB |
| 📁 Output | 7 | Pipeline Steps — enable/disable steps; retry failed downloads |
| 📁 Output | 10 | Output Paths — Raw SAFE / SNAP tiles / COG indices folders |
| 📁 Output | 11 | Output CRS — AUTO (native UTM) or any EPSG code |
| 🧮 Raster Calc | — | Post-processing calculator — apply numpy expressions to existing COG outputs |

---

### Preprocessing Graph (section 2)

The GUI lets you choose between two built-in processing chains or supply your own SNAP GPT graph XML.

#### Option A — σ⁰ Standard  *(recommended for flat terrain)*

`snap_graphs/s1_sigma0_standard.xml`

```
Apply-Orbit-File
  → ThermalNoiseRemoval
  → Remove-GRD-Border-Noise
  → Calibration → σ⁰ (sigma-nought, linear)
  → Speckle-Filter: [configurable — see section 2b]
  → Range-Doppler Terrain Correction
      DEM: [configurable — see section 2c]  |  10 m  |  UTM 32N  |  aligned grid
      nodataValueAtSea = false
  → Subset to AOI  → Write (GeoTIFF)
```

Based on Filipponi (2019). No Terrain-Flattening. Correct for flat agricultural terrain (e.g. Po Valley). Simpler, faster, and matches the majority of SAR agricultural literature.

#### Option B — γ⁰ RTC  *(terrain-flattened, more rigorous)*

`snap_graphs/s1_gamma0_rtc.xml`

```
Apply-Orbit-File
  → ThermalNoiseRemoval
  → Remove-GRD-Border-Noise
  → Calibration → β⁰ (beta-nought, linear)
  → Speckle-Filter: [configurable — see section 2b]
  → Terrain-Flattening
      β⁰ → γ⁰  |  DEM: [configurable — see section 2c]  |  nodataValueAtSea = false
  → Range-Doppler Terrain Correction
      DEM: [configurable — see section 2c]  |  10 m  |  UTM 32N  |  aligned grid
      nodataValueAtSea = false
  → Subset to AOI  → Write (GeoTIFF)
```

Based on Small (2011). Radiometrically correct for sloped terrain. `nodataValueAtSea = false` in both TF and TC prevents water canals and drainage ditches from being incorrectly zeroed out.

> **Which to choose?** For flat areas (Po Valley, lowland agriculture) Option A is correct and sufficient. Option B adds value only when your AOI includes significant topographic variation.

#### Option C — Custom XML

Select "Custom XML" in section 2 and browse to any SNAP GPT graph XML on your computer. The pipeline edits the graph at runtime, so your file only needs to define the *processing*, not the I/O paths.

**What the pipeline patches automatically (do not hard-code these):**

- the **`Read`** node's `file` → set to each scene's `.SAFE` manifest;
- a **`Subset`** node → injected just before `Write` to clip to your AOI;
- the **`Write`** node's `file` and `formatName` → set to the output path and `GeoTIFF-BigTIFF`;
- the **`Speckle-Filter`** node's parameters (if the node exists) → replaced with your section-2b choice;
- the **`demName`** on any **`Terrain-Flattening`** / **`Terrain-Correction`** node → set to your section-2c DEM.

**Rules a custom graph MUST follow (or the run fails / produces wrong indices):**

1. **Exactly one `Read` node** (`<operator>Read</operator>`) with a `<parameters><file>` element. Put any placeholder path there — it gets overwritten. Avoid `ProductSet-Reader` (SNAP rejects it before execution).
2. **Exactly one `Write` node** whose `<sources>` points at your final processing step. Leave its `file`/`formatName` as anything — they're overwritten. The AOI `Subset` is spliced in between your last node and `Write`, so the last real operator is normally **Terrain-Correction**.
3. **Output must be 2 bands, in the order VH (band 1) then VV (band 2)**, as **linear** intensity (σ⁰, γ⁰ or β⁰ — not dB). Step 3 reads them positionally (`band 1 = VH`, `band 2 = VV`); if you swap the order or output dB, CR/RVI/DIFF come out wrong. Keep `selectedPolarisations` = `VH,VV` on the calibration/noise nodes (this is what the built-in graphs do).
4. Build it the easy way: open SNAP **Tools → Graph Builder**, assemble `Read → … → Terrain-Correction → Write`, and **Save** as XML. Don't add your own Subset (the pipeline adds it) and don't set absolute input/output paths.
5. A `Speckle-Filter` node is **optional** — include it only if you want speckle filtering; its settings are taken from section 2b, not from the file.

The simplest reliable starting point is to copy one of the built-in graphs in `snap_graphs/` (`s1_sigma0_standard.xml` or `s1_gamma0_rtc.xml`) and modify it — they already satisfy all of the above.

---

### Downloads (sections 1, 6c, 7)

There are three download sources. **CDSE S3 needs no unzip step** and should be the fastest, but a connection-pool starvation bug currently caps it below ASF (see benchmark below), so **ASF is the fastest reliable source today**.

| Source | Parallel support | Notes |
|--------|-----------------|-------|
| **CDSE S3** *(section 6c)* | ✅ configurable 1–16 files per scene (default 8) | Reads the `.SAFE` directly from CDSE's `eodata` object store — **bypasses the OData throttle** and delivers the extracted folder, so **no unzip**. Capped by CDSE at **~20 MB/s (160 Mbit/s)** and **12 TB/month** per S3 key, so ~8 parallel files usually saturates it; lower it if the link feels congested. |
| **ASF** | ❌ Sequential only (forced to 1 worker) | ASF's server truncates the VV band when multiple connections share the same user token simultaneously — a known server-side bug, not a bandwidth issue. |
| **Copernicus CDSE** (OData) | ✅ Up to 5 parallel workers | Serves via the OData `/$value` endpoint. Bearer JWTs are shared across threads, but the endpoint applies a **per-account fair-use throttle** that drops sustained transfers to single-digit Mbit/s once a rolling quota is hit. |

**Benchmark** (single machine, 1 Gbps line, one full IW GRDH scene, measured 2026):

| Method | Throughput | ~1 GB scene |
|--------|-----------|-------------|
| ASF (1 connection) | 48–67 Mbit/s | ~2–3 min |
| CDSE OData, 3 parallel workers | 21 Mbit/s (throttled) | ~6 min |
| CDSE OData, 1 connection (throttled) | 7 Mbit/s | ~19 min |
| CDSE S3 (parallel files) | currently slower than ASF — connection-pool starvation, fix pending | — |

> **Note:** CDSE S3 *should* lead this table — it bypasses the OData throttle and skips unzip — but a connection-pool starvation bug is currently holding it below ASF, and the earlier 112 Mbit/s figure was not reproducible. Until the fix lands, **ASF is the fastest reliable source**.

Once the S3 bug is fixed, you can go faster still by generating multiple S3 keys (each has its own 20 MB/s cap). Get keys at the [CDSE S3 keys manager](https://eodata-s3keysmanager.dataspace.copernicus.eu/).

The **parallel downloads** spinner (section 7) applies to **CDSE OData and CDSE S3** (2–5 scenes at once; ASF ignores values > 1). For CDSE S3 it stacks with the **"Parallel files / scene"** control (section 6c, 1–16, default 8): a single S3 scene rarely saturates the 20 MB/s per-key cap, so running 3–5 scenes at once fills it.

### Satellite selection & Sentinel-1C (section 5b)

Pick which satellites to fetch (S1A / S1B / S1C). Notes:

- **All sources return Sentinel-1C.** S1C became available in April 2025; ASF exposes it via `asf_search` **≥ 8.1.3** (this app now requires that — older 6.x could not see S1C), and CDSE / CDSE S3 have it too. In a like-for-like 4-month 2025 test, CDSE returned marginally more S1C than ASF (54 vs 46), so **CDSE / CDSE S3 is a good choice for S1C**, but ASF works.
- Same dual-pol VV+VH, same footprints — S1C simply adds revisit density.

> **⚠ Recent data — prefer CDSE.** ASF is a *mirror* of ESA's archive, so the newest scenes (roughly the last few days) can be missing from ASF until it catches up, and ASF only began ingesting Sentinel-1C on **30 April 2025** without backfilling the earliest S1C acquisitions. In a like-for-like 4-month 2025 test ASF returned a strict *subset* of CDSE (46 S1C vs 54) — the 8 it lacked were the earliest S1C scenes plus the single most-recent one. **If your date range includes very recent dates (or early-2025 S1C), use CDSE or CDSE S3** to be sure you get everything; ASF may silently return fewer scenes.

Use the filter to **backfill**: if an AOI was downloaded before S1C existed, select **S1C only** + source **CDSE S3** and re-run — it fetches just the missing S1C scenes (existing ones are skipped by the existing-file check).

**Authentication — CDSE:** refresh/offline token only — no email/password path exists in this app, so a CDSE password is never entered or stored. Paste the token in section 6b; the code exchanges it for a short-lived JWT Bearer token via Keycloak and renews it automatically. The pasted refresh token itself is valid ~60 min from when Copernicus issues it, so generate it right before starting a download. See [how to generate a CDSE refresh token](https://documentation.dataspace.copernicus.eu/APIs/Token.html) (the token-generation request returns a `refresh_token` field alongside the access token).

**Connection resilience (both sources):** each download session mounts a `urllib3` retry adapter (exponential backoff; retries on dropped connections, 429 and 5xx). On top of that, each scene is attempted several times with backoff:

- **ASF** — per-scene retry loop (no resume on ASF, so a partial `.zip` is removed between attempts to avoid leaving a truncated archive). Scenes that still fail are added to the end-of-run retry pass.
- **CDSE** — per-scene retry loop **with resume**: the partial download is written to a `.part` file and continued via an HTTP `Range` request, so a connection that drops at 90 % does not start over. CDSE failures are now retried in-loop (previously they were not).

**Integrity checking (around unzip):** corruption — including the silently-truncated VV band caused by parallel ASF downloads — is caught at two points so it never reaches SNAP:

1. **Before unzip** — a fast structural check of each `.zip` (valid archive, `manifest.safe` present, VV/VH bands present and sensibly sized). This reads only the zip index, *not* every byte, so it costs almost nothing.
2. **During unzip** — the extractor verifies each member's CRC (7-Zip/`tar` return non-zero, Python `zipfile.extractall` raises); a CRC failure marks the archive corrupt.
3. **After unzip** — `check_safe()` validates the extracted `.SAFE` (valid manifest, both bands present and not truncated, GeoTIFFs readable via rasterio).

Anything flagged `CORRUPT` is deleted and written to `pipeline_errors/`, so SNAP skips it and a re-run re-downloads what is missing. Heuristic `SUSPECT` cases (e.g. VV much smaller than VH) are kept but flagged in the log for inspection. The checks reuse `check_safe.py` (see below); if that file is absent the pipeline still runs, just without the extra checks.

**Parallel extraction:** unzipping is I/O-latency bound (a single `zipfile` stream leaves the disk mostly idle), so archives are extracted concurrently (`unzip_workers`, default 3). Each scene extracts into its own private temp dir and is then moved into place with a same-volume atomic rename, so SNAP never sees a half-extracted `.SAFE`. The pipeline prefers a **native extractor** — **7-Zip** (`7z`, auto-detected on `PATH` or under `C:\Program Files\7-Zip\`) or **bsdtar** on Windows/macOS — which is markedly faster than Python's `zipfile` on large Sentinel products; if neither is found it falls back to `zipfile` automatically (install 7-Zip to speed this up). Products extracted outside the app (e.g. manually with 7-Zip, which drops the `.SAFE` suffix) are normalised back to `*.SAFE` before SNAP discovery.

A real-time throughput monitor samples directory growth every second and displays current speed in **Mbps** in the progress bar. Average speed per scene is also logged on completion.

**Broken `.SAFE` auto-recovery (including *Use existing `.SAFE` folder* mode):** at the start of step 1 the pipeline deletes any `.SAFE` directory that has no `manifest.safe` — an empty or half-extracted product left behind by an interrupted download/extraction. Leaving one in place would both crash SNAP (`SNAP FAILED (rc=1) … manifest.safe does not exist`) and block recovery, because the download and unzip steps skip when a `.SAFE` of that name already exists. Even when download is otherwise off (existing-folder mode), the missing scenes are then **re-downloaded automatically** for just those acquisition dates, provided credentials (ASF token or CDSE login) and an AOI are set. The re-download is shown in the log (`⤓ RE-DOWNLOADING N broken .SAFE date(s): …`) and animates the **Download** progress bar. If no credentials/AOI are available it logs a warning and continues without those scenes.

#### Standalone integrity checker — `check_safe.py`

The same checks can be run manually on any folder of downloads (e.g. to vet an existing archive before processing):

```
python check_safe.py "E:\SNAP_Download"     # scan zips + .SAFE dirs (report only)
python check_safe.py . --delete             # also delete anything CORRUPT
python check_safe.py . --no-crc             # skip the slow per-byte zip CRC test
python check_safe.py . --no-deep            # skip the rasterio band-read test
```

With no path it auto-detects the `safe_dir` from `sar_foundry_config.json`. Exit code is `0` if all products are OK, `1` if any are `SUSPECT`/`CORRUPT`. Standard library only; uses `rasterio` for the deep band read if it is installed (run it with the project venv's Python to enable that).

---

### Speckle Filter (section 2b)

| Mode | Description |
|------|-------------|
| **Lee Sigma 7×7** | Default. Analysis window 7×7, target 3×3, σ=0.9, ENL estimated. |
| **Gamma Map 7×7** | Bayesian filter assuming Gamma-distributed backscatter. Better edge preservation. |
| **Custom** | Opens a configuration dialog with all 9 SNAP speckle filters. Settings are saved between sessions. |

---

### DEM for Terrain Correction (section 2c)

| DEM | Resolution | Notes |
|-----|-----------|-------|
| **Copernicus 30m Global DEM** | ~30 m | Default. GLO-30, best global coverage. |
| Copernicus 90m Global DEM | ~90 m | Faster download. |
| SRTM 1 arc-sec HGT | ~30 m | Classic global DEM. |
| SRTM 3 arc-sec | ~90 m | Broad coverage, older. |
| ASTER GDEM | ~30 m | Good for areas with SRTM voids. |

DEMs are auto-downloaded by SNAP on first use and cached locally. An internet connectivity indicator is shown next to the dropdown.

---

### Area of Interest (section 3)

**Browse for a file** — accepted formats: `.shp`, `.gpkg`, `.geojson`. Any CRS is accepted — reprojected to EPSG:4326 automatically. Multi-polygon files are unioned.

**Draw on map** — click **🗺 Draw on map** to open an interactive Leaflet map (requires `pywebview` — auto-install prompt). Basemaps: Google Hybrid (default), Google Satellite, OpenStreetMap. Drawn AOI saved as `drawn_aoi.geojson` and persists across sessions.

---

### Multi-tile mosaicking

When your AOI spans two or more adjacent Sentinel-1 frames, tiles are processed independently then merged via **seam interpolation**: TC edge artefact rows are trimmed, and the resulting gap is filled with vertical bilinear interpolation between the two bordering tiles. Final output is always a single `.tif` per date+orbit.

---

### Batch disk budget & incremental output

For large jobs whose full set of `.SAFE` products won't fit on the working drive, set a **batch disk budget** (`safe_scratch_gb`, section 2e) — a number of GB (e.g. `15`) or `auto` (80 % of the target drive's free space). The pipeline then works in **chunks**: extract ≤ budget GB of `.SAFE` → SNAP → compute COG indices → delete that chunk's `.SAFE` → next chunk, so no more than one budget's worth of `.SAFE` is ever on disk at once. Leave it blank / `0` to extract everything at once (old behaviour).

- **Separate / faster `.SAFE` drive** — set `safe_out_dir` to extract onto a fast internal drive (e.g. an NVMe `C:`) even when the zips download to a slower or external one. Exploding a `.SAFE`'s thousands of small files is IOPS-bound and far faster on a healthy internal disk; the budget is measured against this drive's free space.
- **Incremental finals** — in batch mode the COG indices are produced **per chunk**, not only at the end, so outputs appear as the run progresses and each chunk's SNAP GeoTIFFs are freed as they're consumed. The step-3 pass scans the whole SNAP folder, so GeoTIFFs left over from an interrupted earlier run are finalized too.
- **Resume-safe delete** — a chunk's `.SAFE` is removed only once a COG (or `.done` marker) for its date + orbit exists; a stopped or failed conversion keeps its `.SAFE` for the next run instead of silently dropping that date.
- **Progress / ETA** — after each batch the log prints elapsed + estimated time remaining, e.g. `est. 3h 18m left (13 batches to go)`.
- **Auto-sized parallel SNAP** — the default number of parallel SNAP jobs is chosen from *available* RAM (each JVM heap is large), leaving headroom for the OS and other apps so the machine doesn't page. Override it in section 2e.

---

### Removable-drive resilience

A run often spans several drives — downloads on one, `.SAFE` extraction on another, SNAP tiles and final COGs on others — and external drives sometimes drop out mid-run. The pipeline waits for **every** drive it reads or writes to reconnect, rather than failing:

- At the **start of a run and before each batch**, it verifies the download, extraction, SNAP and output drives are all writable. If any has dropped, it **pauses and re-checks every 2 minutes** (`⏸ … waiting for X: to reconnect`) until the drive returns (`▶ … back — resuming`), or you press Stop.
- If a drive stumbles **mid-extraction**, the affected `.zip` is **kept, never deleted** — a transient drive/IO fault is not treated as corruption (genuine corruption is still caught and removed by the pre/post integrity checks), so the scene is retried instead of lost.

This lets an unattended multi-drive run survive a drive that drops and comes back.

---

### Multiple-AOI batch runner

The **Batch** tab processes **many AOIs in one sequential run** — each AOI (and each pipeline variant) gets its own auto-named output folder, and AOIs run one after another.

1. **AOI files** (section 1) — add `.geojson` / `.shp` / `.gpkg` files individually or a whole folder. The list is saved on Run, so it reappears next launch; finished AOIs drop off automatically and unfinished ones stay and resume from disk.
2. **Pipeline assignment** (section 2) — three modes:
   - **Uniform** — one pipeline + speckle filter + DEM applied to *every* AOI. Use it to produce the same product across many study sites. **Jobs = number of AOIs** (one each).
   - **All combinations** — a full grid: every AOI × every ticked pipeline (Sigma0 / Gamma0) × every ticked speckle filter × every ticked DEM. Each combination is a separate job with its own output folder, so you can compare preprocessing choices side by side for the same site. **Jobs = AOIs × pipelines × speckles × DEMs** — this multiplies quickly, so tick deliberately. All combinations of a given AOI reuse a single download (see disk-budget note below). **Date range:** this mode has no date field — it uses the **Download tab's** Start/End for every combination (shown as a hint under the section).
   - **Per-AOI** — one row per AOI where you pick its pipeline, speckle, DEM **and its own date range** independently (📅 picker per row). Use it when sites differ in season, orbit, or desired product. **Jobs = number of AOIs** (one each). Each row also has a **⚙** button to **tune that AOI's speckle-filter parameters** (window size, etc.) for its selected filter — so two AOIs can run the *same* filter with *different* settings. The tuning persists across reopen and applies only while that row keeps the filter it was tuned for. The header has a **per-column "⇩ apply to all"** button (pipeline / speckle / DEM / start / end): each copies **row 1's value down that one column only**, leaving the other columns untouched (the speckle button also carries row 1's ⚙ tuning).
3. **Common settings** (section 3) — output bands and worker counts (unzip / SNAP jobs / index) are **shared across all AOIs** and bound to the same controls as the Download/Processing tabs, so you set them once. Source, credentials, satellites, orbit and scale (dB/linear) also come from those tabs.
4. **Output base** (section 4) — outputs go to `<base>/<AOI>/<pipeline_speckle_DEM>/`; each AOI's downloads and extraction live under `<base>/<AOI>/_safe/`.

Press **▶ Run Batch**. **Stop** halts after the current scene (partial downloads are kept). On a re-run, fully-finished AOIs drop off automatically, and within a still-unfinished AOI any date that already has a final (or `.done`) is **not re-downloaded or reprocessed** — so a stopped batch resumes cheaply from where it left off.

> **⚠ Batch field-clustering — BETA (new in v1.0.14, not yet live-tested).** With the **Cluster AOI** checkbox (section 3b) **on**, batch now crops SNAP to tight per-field clusters — like the single-AOI tool — instead of one whole-AOI bounding box. It's one global toggle for the whole batch, and it clusters from **each AOI's own polygons** (batch has no separate per-AOI fields file, so the AOI file itself *is* the field set — there's no extra field-mask step). Finals then carry a per-cluster tag, matching single-AOI output. With the box **off**, batch runs SNAP over the whole AOI bbox as before. This path is **new in v1.0.14 and has not yet been live-tested on a real multi-AOI batch** — verify your first clustered batch and please [report anything off](https://github.com/EnMaga/sentinel-foundry/issues).

> **Disk budget applies in batch mode too.** Set the section-2e **disk budget** (`safe_scratch_gb`) and each AOI is processed in chunks — extract ≤ budget GB of `.SAFE` → SNAP → index → delete — so no more than one budget's worth of `.SAFE` is ever on disk at once (same as a single-AOI run). An AOI that runs **multiple combinations** keeps its downloaded `.zip`s so each combination re-extracts chunks **without re-downloading**; a **single-combination** AOI (Uniform / Per-AOI) deletes each `.zip` as it is extracted to free space. Leave the budget blank / `0` to extract everything at once (old behaviour).

#### Output folder layout & reusing existing work

**Changed in v1.0.14 — pipeline-first merged layout.** Batch finals no longer sit in a per-AOI `<AOI>\<combo>\` folder. Every AOI's finals for a given recipe now land **together** in one **pipeline** folder (`<preset>_<speckle>`, DEM dropped from the folder name), kept apart by the AOI label already carried in each filename. Downloads and transient SNAP tiles stay per-AOI:

```
<out_base>\<preset>_<speckle>\                       finals — ALL AOIs merged here
        ├─ ASC\  DSC\                                final COGs (DEM stays in each filename)
        └─ Done_files_S1\                            .done resume-markers (shared folder, globbed per-AOI)
<out_base>\<AOI-stem>\_safe\                          downloads / .zip (per-AOI, shared across that AOI's combos)
<out_base>\<AOI-stem>\<preset>_<speckle>_<dem>\_snap\ transient SNAP tiles (per AOI + combo, DEM kept)
```

Example: `Arezzo.geojson` + `Bolgheri.geojson`, σ⁰, Lee Sigma, Copernicus 30 m → both AOIs' finals in `…\sigma0_lee\ASC\`, told apart by the `_Arezzo_` / `_Bolgheri_` label in each `.tif` name. The skip/marker globs are **AOI-label-scoped**, so merging AOIs in one folder can never make one AOI skip another's dates.

> **⚠ Old batch outputs (pre-v1.0.14) won't be found.** Because the finals path changed, batch runs will **not** see outputs written by v1.0.13 or earlier into the old `<AOI>\<combo>\` layout — those dates reprocess into the new merged folder (nothing is corrupted, just duplicated work). Move old data into the new layout first if you want it reused.

**Will a batch reuse data already on the disk?** Only if it sits at **exactly** the new paths above — same `out_base` + preset + speckle for finals, and same AOI stem for `_safe\`. When it matches, already-finished dates are **skipped** (finals + `.done`) and existing `.zip`/`.SAFE` in `_safe\` are **reused** (no re-download); the run just adds the missing dates. It will **not** reuse outputs from the **single-AOI tab** (a different layout). Things to know:

- **The `_safe\` match key is the AOI *file name*, not its geometry.** Same filename stem → same download folder; a renamed file → a fresh download. Never give two different areas the same filename.
- **Preset / speckle are part of the finals folder name**; **DEM is part of the `_snap` path** but not the finals folder. Changing preset or speckle creates a separate finals folder (by design, for side-by-side comparison). **⚙ per-AOI parameter tuning does *not* change the folder** — the suffix is the filter *name* (e.g. `lee_sigma`).

#### Per-AOI `.done` markers

The group-done sentinels (`.done`, see [Re-running](#re-running-a-range-vs-continue-downloading--check-missing-dates)) are written to a `Done_files_S1\` sub-folder inside the finals folder (the one holding `ASC/` `DSC/`) — *not* in the transient SNAP-tile folder. In the v1.0.14 merged layout several AOIs share that finals folder, so they share `Done_files_S1\` too — but every `.done` glob is **scoped to the AOI's label**, so one AOI's skip check can never be confused by another AOI's markers.

#### Batch progress bars

In batch/chunked mode the progress bars adapt (single-AOI, non-batch runs are unchanged):

- **AOI** — a top bar showing `k/N — <AOI name>`, appears **only** during a multi-AOI batch.
- **Batch** — the former "Unzip" bar, retitled; it tracks **whole chunks** as `chunk k/~N` rather than individual files. In the static (non-download) batch path `N` is exact; in the pipelined (download-while-processing) path `N` is an estimate (`~`) projected from the catalogue's per-scene byte sizes ÷ the chunk budget, firming up as downloads land.
- **Download** / **Processing** — unchanged (scenes `k/N` with live rate + ETA).

#### Completion reminder

After a **single-AOI** run finishes with no errors (and wasn't stopped), a dialog offers to **Run coverage check now** (this is the *Continue downloading / Check missing dates* action — it searches the source and reports/fetches any gaps), **Delete .done** (removes only that AOI's `Done_files_S1\*.done`; finals are never touched), or **Close**. A batch run instead logs a non-blocking reminder at the end (a modal per-AOI would stall the queue). Deleting the markers only affects future top-ups — zero-coverage dates would be re-checked once — so confirm coverage first.

---

### Stop button

Pressing **■ STOP** once is **graceful**: it stops launching new scenes — and, in batch mode, new batches — but lets the SNAP run(s) already in flight finish and publish, so you never lose minutes of compute or leave a half-done group. Scenes that finished are still turned into COG indices and their raw `.SAFE` cleaned; scenes not yet converted keep their `.SAFE` so a re-run resumes them. **Press STOP a second time to force-kill** the running SNAP GPT subprocess(es) (`Popen.kill()`) and abort any in-flight download immediately. The UI resets to ready state.

**Closing the window while processing:** if you click the window's ✕ (close) button while a run is still in progress, both SAR Foundry and Optical Foundry now ask *"The pipeline is still running — are you sure you want to quit?"* (default **No**). Confirming stops the run first (killing SNAP subprocesses on SAR / cancelling pending days on Optical) and then closes; declining leaves the window open. Closing while idle exits with no prompt.

---

### Retry failed downloads

If scenes fail to download, the pipeline logs the failure and continues. At the end of the run, if **Retry failed downloads at end** is enabled (section 7), the pipeline re-authenticates and retries the failed scenes.

Most download failures are *transient* — a dropped TLS connection or a 5xx from the archive (`Read failed`), not a missing scene. A scene that recovers on retry no longer leaves a stale log behind: the `pipeline_errors/…__download*.error.txt` file is deleted the moment that download succeeds.

**Manual retry (↻ Continue downloading / Check missing dates).** If a run still ends with download errors — e.g. the archive was down and every attempt failed — click **↻ Continue downloading / Check missing dates** in the footer. There's nothing to retry if `pipeline_errors/` has no download logs *and* every date in the range is already finished.

---

### Re-running a range vs. "Continue downloading / Check missing dates"

A common question: if I press **START** again over the same date range, will it re-download everything, or check what's already done?

**A plain re-run does *not* consult your finished outputs to decide what to download.** The download step only skips a scene whose `.zip` or `.SAFE` is still physically present in the download folder. So:

- If your previous run had **"After step 2: delete .SAFE source files"** ticked, those files are gone → a plain re-run **re-downloads the whole range** from scratch.
- If it was unticked, the `.zip`/`.SAFE` are still on disk → those scenes are skipped.

Either way the **SNAP and indices steps always skip dates that are already finished** — they key off the final `S1_<date>…_<ASC|DSC>_*.tif` products and the per-group `.done` markers (stored per-AOI in `<out_dir>\Done_files_S1\`) — so a plain re-run never *reprocesses*. The only wasted work is the re-download.

**To top up a run — fetch only the dates you're actually missing — press `↻ Continue downloading / Check missing dates`** (footer) instead of START. It searches your chosen source over the whole `[start, end]` range but skips every date that already exists at **any** stage: `.zip`, `.SAFE`, SNAP GeoTIFF, final product, *or* a `.done` marker. (The `.done` marker matters for **zero-coverage dates** — a scene whose footprint covers none of your AOI finishes cleanly but produces no `.tif`; without the marker it would be re-downloaded and discarded on every run.) Only dates truly absent everywhere are downloaded and processed, and this is correct whether or not the `.SAFE` were cleaned up — so it's the right choice for **extending or repairing a timeseries**. Make sure **SNAP + indices are ticked** if you want the fetched dates turned into finals. The same button also absorbs any leftover `pipeline_errors/…__download*.error.txt` failures, since those dates count as "missing" too.

---

### Opening the app (unsigned build)

The installers are **unsigned** (no paid Apple/Windows code-signing certificate), so your OS will warn you the first time you open the app. This is expected — the warning is about the *missing signature*, not about the app itself. Here's how to get past it, per OS. You only need to do this once.

**Windows** — SmartScreen shows *"Windows protected your PC."*
1. Click **More info**.
2. Click **Run anyway**.

**macOS** — Gatekeeper shows *"…cannot be opened because it is from an unidentified developer"* or *"Apple could not verify… is free of malware."*
1. **Right-click** (or Control-click) the app → **Open**.
2. Click **Open** in the dialog that appears.
3. If it's still blocked (recent macOS): **System Settings → Privacy & Security**, scroll to the message naming the app, click **Open Anyway**, then reopen.

   Still stuck? Clear the quarantine flag in Terminal:
   ```
   xattr -dr com.apple.quarantine "/Applications/Sentinel Foundry.app"
   ```

**Linux** — no signature prompt, but the file may not be marked executable:
1. `chmod +x "Sentinel Foundry"` (or: file manager → **Properties → Permissions → Allow executing file as program**).
2. Launch it.

---

**Requires manual install:**

| Tool | Windows | macOS | Linux |
|---|---|---|---|
| Python 3.11 or 3.12 | [python.org](https://www.python.org/downloads/) | `brew install python@3.12` | `sudo apt install python3.12` |
| ESA SNAP **(must include the Microwave Toolbox = Sentinel-1 Toolbox)** | [step.esa.int](https://step.esa.int/main/download/snap-download/) | same | same |
| GDAL | via [QGIS](https://qgis.org) or OSGeo4W | `brew install gdal` | `sudo apt install gdal-bin` |

**Installing SNAP correctly** — only three installer choices matter:

1. **Components:** the **Microwave Toolbox** *must* be ticked. (This is the SAR / Sentinel-1 toolbox — named **"Microwave Toolbox"** in SNAP 12 / 13, formerly **"Sentinel-1 Toolbox" / S1TBX** in SNAP ≤ 11.) It provides the `eu.esa.sar.*` modules the pipeline reads `.SAFE` files with. Simplest safe choice: leave **all** components ticked.
2. **Python / esa_snappy step:** leave it **unticked** — not needed. The pipeline drives SNAP via the `gpt` command line, not the Python binding.
3. **Final screen:** **"Extend my PATH"** is optional but recommended (lets you run `gpt` from any terminal).

After install, open SNAP once → **Help → Check for Updates → install all → restart**. Then verify in a new terminal:

```
gpt Calibration -h      (or  "C:\Program Files\esa-snap\bin\gpt.EXE" Calibration -h)
```

Clean operator help = good. A Java `eu.esa.sar...` stack trace = the Microwave Toolbox is missing → see Troubleshooting below. Without it, the `gpt` step fails on *every* scene at startup.

> The app self-checks this: the Dependencies panel runs `gpt Calibration -h` at startup, and if SNAP can't load the SAR operators it marks **SNAP GPT** as failed and **blocks the START button** with a clear message — so you find out before a run, not after every scene fails. (The check spins up the SNAP JVM, so it can take up to a minute on a cold first start.)

**Credentials:** NASA Earthdata account required → [Register here](https://urs.earthdata.nasa.gov/users/new)

**Authentication — Bearer token (recommended):**
1. Log in at [urs.earthdata.nasa.gov/user_tokens](https://urs.earthdata.nasa.gov/documentation/for_users/user_token)
2. Click **Generate Token**
3. Paste into the **Token** field in section 6

---

### Running several runs at once

The launcher (Sentinel Foundry) does **not** limit how many SAR Foundry windows you open — click **Open** as many times as you like, point each window at a different AOI / date range, and they run independently and concurrently. On a powerful machine this is a quick way to process several regions at once; on a modest one it will oversubscribe CPU and RAM and slow everything down, so size it deliberately.

What matters is memory. Each SAR Foundry window runs its own SNAP jobs, configured in section **2e "Parallel SNAP Jobs"** (number of parallel SNAP workers, and the JVM heap per worker). The total memory in flight across everything is roughly:

```
windows  ×  workers-per-window  ×  JVM-GB-per-worker
```

Keep that under ~70–80% of your physical RAM, and keep the **total** worker count near your CPU core count (Terrain Correction is CPU-bound). Example for a 32 GB / 8-core machine:

- **One run, fast:** 1 window · 2–3 workers · 8 GB heap (~16–24 GB).
- **Two runs in parallel:** 2 windows · 1 worker each · 8 GB heap (~16 GB).
- **More runs:** drop to 1 worker per window and lower the heap so `windows × workers × heap` still fits.

If you over-commit, you'll see heavy disk swapping, SNAP out-of-memory / "GC overhead" errors, and a net slowdown rather than a speedup. SAR Foundry's Dependencies panel warns when *one* window's `workers × heap` exceeds available RAM — but it cannot see the other windows, so when running several you must do that arithmetic across all of them yourself. Disk matters too: concurrent runs hammer I/O, so prefer an SSD and, where possible, point each run at a different output drive to avoid contention.

### Troubleshooting

**Every scene fails at SNAP with `ClassNotFoundException: eu.esa.sar...` (e.g. `eu.esa.sar.io.ceos.CEOSProductReaderPlugIn` or `eu.esa.sar.commons.polsar.PolBandUtils`).**

Cause: SNAP was installed **without the Microwave Toolbox** (called **"Sentinel-1 Toolbox" / S1TBX** in SNAP ≤ 11, renamed **"Microwave Toolbox"** in SNAP 12 / 13), so the `eu.esa.sar.*` modules (the S1 product readers and the core SAR classes the radar operators depend on) are missing. `gpt` then crashes while loading plugins — *before* reading any data — so the download/unzip steps succeed but every scene fails identically in step 2. This is purely a SNAP install problem; reverting the pipeline code does not help.

Confirm it:
```
"C:\Program Files\esa-snap\bin\gpt.EXE" Calibration -h
```
A Java stack trace mentioning `eu.esa.sar` = the toolbox is missing. You can also check directly — a healthy install has an `s1tbx` folder next to `rstb`:
```
dir /b "C:\Program Files\esa-snap"
```

Fix (clean reinstall):
1. Uninstall SNAP, then delete leftovers: `C:\Program Files\esa-snap`, `C:\Users\<you>\.snap`, `C:\Users\<you>\AppData\Roaming\SNAP`, `C:\Users\<you>\AppData\Local\SNAP`.
2. Reinstall using the **"ESA SNAP — All Toolboxes"** installer and keep **Sentinel-1 Toolbox** ticked.
3. Open SNAP once → **Help → Check for Updates → install all → restart**.
4. Re-verify with `gpt Calibration -h` (should print help, no stack trace), then re-run the pipeline — it skips already-downloaded scenes and resumes at SNAP.

> This commonly appears after reinstalling Windows or SNAP, when a partial component set is chosen.

**`ImportError: DLL load failed … An Application Control policy has blocked this file` (e.g. importing `rasterio`), or "Smart App Control has blocked part of this app".**

Cause: **Windows 11 Smart App Control** (part of Windows Security) blocks binaries it doesn't trust. The geo stack ships **unsigned native libraries** (rasterio's `_base.pyd`, the GDAL DLLs, etc.), so once Smart App Control is enforcing it can block them and every `import rasterio` fails — which the dependency panel then reports as `Missing: rasterio`. It tends to (re)appear after the packages are (re)installed, because the freshly written files get re-evaluated.

Fix: **Windows Security → App & browser control → Smart App Control → Off.** No reinstall of the packages is needed — the existing files load again immediately. Verify with:
```
.venv\Scripts\python.exe -c "import rasterio; print(rasterio.__version__)"
```

> Turning Smart App Control off used to be permanent (re-enabling required reinstalling Windows); since the April 2026 Windows 11 cumulative update it can be toggled off and back on freely. On a machine running scientific Python (rasterio/GDAL, SNAP, …) Smart App Control will keep flagging these unsigned libraries, so leaving it off is the practical choice. The only alternative is code-signing every native binary with a Microsoft-trusted certificate.

**Verify downloads independently** at any time with `check_safe.py` (see the Downloads section above).

---

## Optical Foundry — Sentinel-2 L2A → COG Indices + Biophysicals

Downloads Sentinel-2 L2A from **AWS EarthSearch** (public, no credentials), applies SCL cloud masking, and computes biophysical variables, spectral bands and indices as 10m COG GeoTIFFs.

> **Why AWS and not Copernicus Data Space (CDSE)?**
> The biophysical processor relies on [`satellitetools`](https://github.com/ollinevalainen/satellitetools), which is built on top of the AWS EarthSearch STAC API and AWS S3 public bucket (`sentinel-2-l2a-cogs`). Data access is **completely free and anonymous** — no Copernicus or ESA account needed. Switching to CDSE would require reimplementing the entire search + download layer. Since AWS and CDSE carry the same Sentinel-2 L2A products (ESA ground-truth), there is no scientific difference. The Raster Calculator tab works on any GeoTIFF output regardless of where the data was downloaded from.

### Output filenames

| Situation | Filename |
|---|---|
| Single MGRS tile | `S2_YYYYMMDD_000_[label]_[A/B/C]_[product].tif` |
| AOI spans two tiles → mosaic | `S2_YYYYMMDD_000_[label]_mosaic_[product].tif` |
| Scene Classification Layer | `S2_YYYYMMDD_000_[label]_[A/B/C]_SCL.tif` |

```
output/
  index=LAI/aoi=[label]/S2_YYYYMMDD_000_[label]_A_LAI.tif
  index=CCC/...   # Canopy Chlorophyll Content
  index=CWC/...   # Canopy Water Content
  index=FAPAR/...
  index=FCOVER/...
  index=B2/...    index=B3/...  index=B4/...  index=B5/...
  index=B6/...    index=B7/...  index=B8/...  index=B8A/...
  index=B11/...   index=B12/...
  index=NDVI/...  index=NDWI/... index=NDII/...
  index=MSAVI2/.. index=CIRE/.. index=EVI/...
  index=NDRE1/... index=MTCI/...
  index=SCL/...   # raw Scene Classification Layer for QA
```

All outputs: float32, nodata = −9999, DEFLATE compressed, 10m UTM.

**Custom Indices:** up to 3 user-defined expressions evaluated during the pipeline run, after preset spectral indices. Available variables: `B2`, `B3`, `B4`, `B5`, `B6`, `B7`, `B8`, `B8A`, `B11`, `B12`, `np`. All values are reflectance (0–1). Examples: `(B8-B4)/(B8+B4+1e-9)` · `(B8A-B5)/(B8A+B5)` · `B8/B5-1`. Custom outputs are saved under `index=<name>/aoi=<label>/` with the same COG profile as preset indices. Valid for spectral indices only — biophysical variables (LAI, CCC, etc.) always use AWS EarthSearch via `satellitetools`.

**Pipeline tabs (GUI):**

| Tab | Section | Description |
|-----|---------|-------------|
| ⬇ Download | 1 | Area of Interest — `.shp`, `.gpkg`, `.geojson`, or draw on interactive map |
| ⬇ Download | 2 | Date Range |
| ⬇ Download | 3 | Max Cloud Cover — scene-level threshold (0 = disabled) |
| ⬇ Download | 4 | **Parallel Workers — 1–8 days processed simultaneously** |
| ⬇ Download | 5 | **Network Error Handling — retry failed days (configurable 1–3 attempts, 10 s back-off)** |
| ⚙ Processing | — | Biophysical outputs — toggle with select-all |
| ⚙ Processing | — | Spectral Bands — toggle with select-all |
| ⚙ Processing | — | Spectral Indices — toggle with select-all |
| ⚙ Processing | — | **Custom Indices — up to 3 user-defined numpy expressions (B2–B12), computed during run** |
| 📁 Output | — | Output CRS — AUTO (native UTM) or any EPSG |
| 📁 Output | — | Output Folder |
| 📁 Output | — | **Existing Files — Skip (resume) or Overwrite** |
| 🧮 Raster Calc | — | Post-processing calculator — apply numpy expressions to existing COG outputs |

---

### Per-tile mosaicking (cross-MGRS-tile AOIs)

When an AOI straddles two MGRS tiles (e.g. 32TQN + 32TQP), `satellitetools` internally selects only one tile per acquisition, leaving the other half of the AOI as nodata on dates when the selected tile has a partial swath.

The pipeline now performs **explicit per-tile processing**:

1. A direct EarthSearch query discovers every `(tile_id, sat_letter)` pair available for the date.
2. For each tile, a thread-local monkey-patch filters the satellitetools search to that tile only — guaranteeing real data from both 32TQN and 32TQP.
3. Each tile is processed independently (bands, indices, biophysicals, SCL mask).
4. If two or more tile files exist for the same date and variable, `rasterio.merge(method="first")` produces a single mosaic covering the full AOI. Intermediate per-tile files are deleted.

The satellite letter (`A`, `B`, `C`) is extracted directly from the STAC item ID (e.g. `S2A_32TQN_20250325_0_L2A`) — no longer relies on the rarely-populated `satellite_id` coordinate.

---

### Parallel processing

Days are processed in parallel using `ThreadPoolExecutor` (configurable 1–8 workers). Each worker independently queries EarthSearch, downloads band data from AWS S3, applies the SCL mask, and writes COG outputs. All UI callbacks are thread-safe. A real-time ETA is shown based on average seconds per completed day.

---

### Retry on network errors

Failed days are collected during the run. If **Retry failed days** is enabled, the pipeline waits 10 seconds then re-attempts up to the configured number of times. Useful for transient AWS S3 or EarthSearch timeouts.

Nearly all of these are *transient* tile reads: a single MGRS tile drops mid-download (`… tile 34TGR/B fetch: Read failed`), so the whole day is re-queued rather than saving a mosaic with a hole in it. A day that recovers on retry no longer leaves a stale log — the day's `…__process.error.txt` is cleared at the start of each attempt and only rewritten if that attempt fails. (Previously the automatic retry could fix a day yet still list it under "days had errors" in the summary — the error log was never deleted.)

**Manual retry (↻ Retry failed days).** If a run still ends with day errors, click **↻ Retry failed days** in the footer. It reads the failed dates from the leftover `S2_YYYYMMDD__process.error.txt` logs and re-runs *only* those days with overwrite on (so a partially-written mosaic is rebuilt cleanly). Nothing to retry if `pipeline_errors/` is empty.

> **"no data" and "no usable pixels" are not errors — and they are two different things.** Sentinel-2 revisits a given spot only every ~5 days, and its swaths are ~290 km wide, so on most calendar dates one of these is expected:
>
> - **`<date>: no data`** — *nothing was acquired.* No S2 granule was even catalogued for that date over the AOI; the satellite did not image this area that day. This is the plain revisit gap, and it's the most common line by far (a ~5-day cycle means ~4 of every 5 days show it).
> - **`<date>: no usable pixels`** / **`footprint matched but no pixels cover this AOI on this date`** — *acquired, but nothing usable landed on your fields.* A granule **was** found — its 100 km MGRS-tile footprint clips the AOI's bounding box — but after cloud/SCL masking and clipping to the actual field shapes, zero valid pixels remained (the imaged swath grazed the tile edge, or every covering pixel was cloud/NODATA).
> - A **mixed** line like `c01: 28 outputs …; c02: footprint matched but no pixels …` just means one field cluster got usable data that day and another didn't.
>
> All three are skipped silently and never retried. Only a real fetch/processing failure (e.g. `tile … fetch: Read failed`) writes to `pipeline_errors/`.

---

### Skip / Overwrite existing files

- **Skip** (default): if a file already exists on disk, its path is registered (so the mosaic step can include it) but it is not re-downloaded or reprocessed. Efficient for extending a date range or resuming an interrupted run.
- **Overwrite**: all files are rewritten, including mosaics.

---

**No manual installs required** beyond Python. All packages installed automatically on first run from `requirements_s2.txt`.

---

## Repository layout

```
sentinel-foundry/
├─ sentinel_foundry.py          # launcher — opens SAR or Optical from one window
├─ s1_pipeline_ui.py            # SAR Foundry      (Sentinel-1 GRD → COG indices)
├─ s2_pipeline_ui.py            # Optical Foundry  (Sentinel-2 L2A → COG indices)
├─ check_safe.py                # download integrity checker (.zip / .SAFE)
├─ install_sentinelfoundry.py   # run once → desktop icon (Windows / macOS / Linux)
├─ requirements.txt             # Python packages for SAR Foundry
├─ requirements_s2.txt          # Python packages for Optical Foundry
├─ sentinel_foundry.ico / .png  # app icon
├─ snap_graphs/                 # built-in SNAP GPT graphs (σ⁰ standard, γ⁰ RTC)
└─ docs/                        # project website (GitHub Pages) — index.html + assets/
```

Each tool finds its resources (graphs, requirements, icon, config) next to itself, so the layout is intentionally flat — keep these files in the same folder. The shared `.venv/` and `sar_foundry_config.json` are created at runtime and are git-ignored.

---

## License

MIT — see [LICENSE](LICENSE)

## Funding

This software was developed during a PhD at the TESAF Department, University
of Padova, funded by the European Union — **NextGenerationEU** under the
Italian National Recovery and Resilience Plan (**PNRR**), Mission 4
"Education and Research", pursuant to Ministerial Decree no. 630/2024
(*Finanziato dall'Unione europea – NextGenerationEU*).

## 🚧 In development

- **One-click builds** — Windows, macOS and Linux builds are on the [Releases page](https://github.com/EnMaga/sentinel-foundry/releases) (macOS/Linux not yet field-tested — feedback welcome). Python, SNAP and GDAL remain external prerequisites.
- ~~**S1 parallel SNAP processing**~~ *(done — section 2e "Parallel SNAP Jobs": configurable workers + JVM heap; you can also open several SAR Foundry windows from the launcher — see "Running several runs at once")*
- ~~**Batch disk budget + incremental per-batch output (S1)**~~ *(done — section 2e; extract → SNAP → index → delete per chunk, COG indices produced per batch, resume-safe `.SAFE` delete, elapsed/ETA per batch, RAM-aware SNAP-worker default; see "Batch disk budget & incremental output")*
- ~~**Multiple-AOI batch runner (S1)**~~ *(done — Batch tab; process many AOIs sequentially with uniform / all-combinations / per-AOI pipeline assignment; now honours the section-2e disk budget per AOI, keeping zips for multi-combo AOIs so combinations don't re-download; see "Multiple-AOI batch runner")*
- ~~**Field-clustering in batch mode (S1)**~~ *(v1.0.14, **BETA / not yet live-tested** — Batch now honours the section-3b Cluster AOI toggle and crops to per-field clusters like the single-AOI tool; finals for all AOIs share a pipeline-first merged folder. See "Multiple-AOI batch runner".)*
- **S1-SliceAssembly** — ESA's recommended approach to eliminate tile seams: assemble adjacent GRD slices *before* calibration. Implementation complete but disabled due to JAI tile-cache `NullPointerException` in SNAP 12.
- **Copernicus CDSE for S2** — *not planned*. S2 uses AWS EarthSearch (free, anonymous, identical data). CDSE S2 would require replacing the entire `satellitetools` search/download layer with no scientific gain.
- ~~**Copernicus CDSE integration for S1**~~ *(done — Download tab, section 6b)*
- ~~**CDSE S3 direct download (S1)**~~ *(done — Download tab, section 6c; bypasses the OData throttle and skips unzip. Currently slower than ASF due to a connection-pool starvation bug (fix pending); see Downloads section.)*
- ~~**Raster Calculator (S1 & S2)**~~ *(done — 🧮 Raster Calc tab in both UIs)*
- ~~**Custom Bands (S1)**~~ *(done — Processing tab, numpy expressions with VV/VH/CR/RVI/DIFF)*
- ~~**Custom Indices (S2)**~~ *(done — Processing tab, numpy expressions with B2–B12)*
- ~~**Interactive AOI drawing (S1 & S2)**~~ *(done — 🗺 Draw on map button in Download tab)*
- ~~**Select / deselect all bands**~~
- ~~**DEM selection for Sentinel-1**~~
- ~~**Retry failed downloads**~~
- ~~**Configurable speckle filter**~~
- ~~**Per-tile mosaicking for cross-MGRS AOIs (S2)**~~
- ~~**Parallel day processing (S2)**~~
- ~~**Real-time download speed display (S1)**~~
- ~~**Parallel downloads (S1)**~~ *(disabled — ASF throttles parallel connections causing corrupted VV bands)*
- ~~**ETA / elapsed time display**~~
- ~~**Download connection resilience (S1)**~~ *(done — urllib3 retry adapter + per-scene backoff on ASF & CDSE; CDSE resume via HTTP Range)*
- ~~**Download integrity checks (S1)**~~ *(done — fast pre-unzip zip check + post-unzip `.SAFE` validation; corrupt products deleted & logged; standalone `check_safe.py`)*

---

## References & acknowledgements

**SAR Foundry** builds on the following tools and services:

| Component | Reference |
|-----------|-----------|
| ESA SNAP / GPT | Zuhlke et al. (2015). *SNAP — ESA Sentinel Application Platform*. ESA Living Planet Symposium. [step.esa.int](https://step.esa.int) |
| ASF / NASA Earthdata | Alaska Satellite Facility, NASA. [asf.alaska.edu](https://asf.alaska.edu) |
| asf_search (Python) | ASF Tools Team. [github.com/asfadmin/Discovery-asf_search](https://github.com/asfadmin/Discovery-asf_search) |
| rasterio | Gillies et al. (2013). *Rasterio: geospatial raster I/O for Python*. [github.com/rasterio/rasterio](https://github.com/rasterio/rasterio) |
| GDAL | GDAL/OGR contributors (2024). *GDAL/OGR Geospatial Data Abstraction software Library*. Open Source Geospatial Foundation. [gdal.org](https://gdal.org) |
| Copernicus DEM | ESA / Copernicus Programme. Copernicus DEM GLO-30. [spacedata.copernicus.eu](https://spacedata.copernicus.eu) |
| σ⁰ workflow (Option A) | Filipponi, F. (2019). Sentinel-1 GRD Preprocessing Workflow. *Proceedings*, 18(1), 11. [doi:10.3390/ECRS-3-06201](https://doi.org/10.3390/ECRS-3-06201) |
| γ⁰ RTC / Terrain-Flattening (Option B) | Small, D. (2011). Flattening Gamma: Radiometric Terrain Correction for SAR Imagery. *IEEE TGRS*, 49(8), 3081-3093. doi:10.1109/TGRS.2011.2120616 |

**Optical Foundry** builds on the following tools and services:

| Component | Reference |
|-----------|-----------|
| EarthSearch / AWS | Element 84. *EarthSearch STAC API*. [earth-search.aws.element84.com](https://earth-search.aws.element84.com) |
| sentinel-2-l2a-cogs | Cogeo-mosaic / AWS Open Data. [registry.opendata.aws/sentinel-2-l2a-cogs](https://registry.opendata.aws/sentinel-2-l2a-cogs) |
| satellitetools | Nevalainen, O. (2022). *ollinevalainen/satellitetools: v1.0.0* [software]. Zenodo. doi:10.5281/zenodo.5993292. [github.com/ollinevalainen/satellitetools](https://github.com/ollinevalainen/satellitetools) |
| Biophysical NNs (LAI/FAPAR/FCOVER) | Baret et al. (2007); Weiss et al. (2020). ESA SNAP bioph
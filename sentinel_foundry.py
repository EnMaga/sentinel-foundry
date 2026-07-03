"""
sentinel_foundry.py — launcher for the Sentinel Foundry suite.

One window, two tools:
  • SAR Foundry      → s1_pipeline_ui.py   (Sentinel-1 GRD, radar)
  • Optical Foundry  → s2_pipeline_ui.py   (Sentinel-2 L2A, optical)

The launcher owns the shared virtual environment: on first use of a tool it
creates .venv (if missing) and installs that tool's Python packages, then
launches the tool with the venv interpreter. A per-tool marker file means the
install only runs again if the requirements file changes.

Non-Python prerequisites (ESA SNAP with the Microwave Toolbox, and GDAL) are
NOT installed here — the launcher links to their download pages instead.

Run:  python sentinel_foundry.py     (or double-click "Sentinel Foundry.bat")
"""
import os
import sys
import threading
import subprocess
import shutil
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk

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

if getattr(sys, "frozen", False):
    _DIR = os.path.dirname(sys.executable)          # running as a PyInstaller .exe
else:
    _DIR = os.path.dirname(os.path.abspath(__file__))

# ── palette (matches the two apps) ──────────────────────────────────────────
BG, CARD = "#14161C", "#1A1A2E"
FG, MUTED = "#E8EAED", "#8A93A3"
SAR, SAR_H = "#FF5252", "#C62828"     # coral red  (Sentinel-1)
OPT, OPT_H = "#1E88E5", "#1565C0"     # blue       (Sentinel-2)
FONT = "Segoe UI"

TOOLS = {
    "sar": {"script": "s1_pipeline_ui.py", "req": "requirements.txt",
            "title": "SAR Foundry",
            "sub": "Sentinel-1 GRD  ·  radar backscatter & indices",
            "color": SAR, "hover": SAR_H, "icon": "📡"},
    "opt": {"script": "s2_pipeline_ui.py", "req": "requirements_s2.txt",
            "title": "Optical Foundry",
            "sub": "Sentinel-2 L2A  ·  cloud-masked bands & biophysicals",
            "color": OPT, "hover": OPT_H, "icon": "🛰"},
}

# Manual, non-Python prerequisites (cannot be pip-installed)
PREREQS = [
    ("ESA SNAP  (include the Microwave Toolbox)", "https://step.esa.int/main/download/snap-download/"),
    ("GDAL  (e.g. via QGIS)", "https://qgis.org/download/"),
    ("Python 3.11 or 3.12", "https://www.python.org/downloads/"),
]

# The scientific stack (numpy/rasterio/shapely/satellitetools) is only known to
# work on 3.11–3.12. Wrong versions hit an undebuggable pip/ABI wall, so refuse
# early with a clear message. Skip when frozen: the .exe bundles its own interp
# and the venv is built from a separate base Python found at runtime.
def _check_python():
    if getattr(sys, "frozen", False):
        return
    if not (3, 11) <= sys.version_info[:2] <= (3, 12):
        v = "%d.%d.%d" % sys.version_info[:3]
        msg = (f"Sentinel Foundry needs Python 3.11 or 3.12 (you have {v}).\n\n"
               "Install 3.12 from python.org and run it with that interpreter.")
        try:
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("Sentinel Foundry", msg); r.destroy()
        except Exception:
            print(msg, file=sys.stderr)
        sys.exit(1)


def _venv_python():
    win = os.path.join(_DIR, ".venv", "Scripts", "python.exe")
    nix = os.path.join(_DIR, ".venv", "bin", "python")
    if os.path.isfile(win):
        return win
    if os.path.isfile(nix):
        return nix
    return win if os.name == "nt" else nix


def _base_python():
    """A real Python interpreter to create the .venv (not the frozen .exe)."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    for c in ("py", "python", "python3"):
        exe = shutil.which(c)
        if exe:
            return exe
    return None


def _open_url(u):
    """Open a URL in the browser, ignoring failures (e.g. headless systems)."""
    try:
        webbrowser.open(u)
    except Exception:
        pass


def _find_gpt():
    """Locate SNAP gpt (PATH or common install locations). Returns path or None."""
    p = shutil.which("gpt") or shutil.which("gpt.bat")
    if p:
        return p
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            if not (root and os.path.isdir(root)):
                continue
            for e in sorted(os.listdir(root), reverse=True):
                if e.lower().startswith("esa-snap") or e.lower() == "snap":
                    for exe in ("gpt.exe", "gpt.EXE", "gpt.bat"):
                        c = os.path.join(root, e, "bin", exe)
                        if os.path.isfile(c):
                            return c
        return None
    for c in ("/Applications/snap/bin/gpt", "/Applications/esa-snap/bin/gpt",
              os.path.expanduser("~/snap/bin/gpt"),
              os.path.expanduser("~/esa-snap/bin/gpt"),
              "/usr/local/snap/bin/gpt", "/opt/snap/bin/gpt"):
        if os.path.isfile(c):
            return c
    return None


def _find_gdal():
    """Locate gdal_translate (PATH, QGIS/OSGeo4W, or a conda env). Returns path or None."""
    p = shutil.which("gdal_translate")
    if p:
        return p
    if os.name == "nt":
        cands = [r"C:\OSGeo4W\bin\gdal_translate.exe",
                 r"C:\OSGeo4W64\bin\gdal_translate.exe",
                 # conda / mamba env layout (relative to the running interpreter)
                 os.path.join(os.path.dirname(sys.executable), "Library", "bin", "gdal_translate.exe")]
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            if root and os.path.isdir(root):
                for e in sorted(os.listdir(root), reverse=True):
                    if e.lower().startswith("qgis"):
                        cands.insert(0, os.path.join(root, e, "bin", "gdal_translate.exe"))
        for c in cands:
            if os.path.isfile(c):
                return c
        return None
    # macOS / Linux: conda env next to the interpreter, or a Homebrew prefix
    # (Homebrew isn't always on a GUI-launched app's PATH).
    for c in (os.path.join(os.path.dirname(sys.executable), "bin", "gdal_translate"),
              "/opt/homebrew/bin/gdal_translate",     # Apple-silicon Homebrew
              "/usr/local/bin/gdal_translate"):        # Intel Homebrew / common
        if os.path.isfile(c):
            return c
    return None


# Key importable packages per tool — used to detect a broken/incomplete venv.
_PROBE = {"sar": ["rasterio", "asf_search", "geopandas"],
          "opt": ["rasterio", "satellitetools", "rioxarray"]}


def _venv_has_packages(venv_py, key):
    """Fast check (find_spec, no heavy import) that the tool's key packages are
    present in the venv — catches a copied repo / deleted site-packages where
    the marker would otherwise say 'installed'."""
    mods = _PROBE.get(key, [])
    if not mods:
        return True
    code = ("import importlib.util, sys; "
            "sys.exit(0 if all(importlib.util.find_spec(m) for m in %r) else 1)" % (mods,))
    try:
        return subprocess.run([venv_py, "-c", code],
                              capture_output=True, text=True, timeout=60).returncode == 0
    except Exception:
        return False


def _ensure_env(key, set_status):
    """Create the shared .venv if needed and install this tool's packages.
    The marker stores a HASH of the requirements file (robust to repo copy/
    clone, unlike mtime), and a quick import probe re-triggers install if the
    venv is missing the packages. Returns the venv python path. Raises on failure."""
    import hashlib
    venv_py = _venv_python()
    if not os.path.isfile(venv_py):
        set_status("Creating environment (.venv) …")
        base = _base_python()
        if not base:
            raise RuntimeError("No Python interpreter found to create the .venv.\n"
                               "Install Python 3.11 or 3.12 from python.org and try again.")
        try:
            r = subprocess.run([base, "-m", "venv", os.path.join(_DIR, ".venv")],
                               capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "Creating the virtual environment timed out (5 min).\n"
                "On macOS this usually means the Command Line Tools 'python3' stub "
                "is in use — install a real Python 3.12 from python.org.")
        if r.returncode != 0:
            raise RuntimeError("Could not create .venv:\n" + (r.stderr or "")[:400])
        venv_py = _venv_python()

    req = os.path.join(_DIR, TOOLS[key]["req"])
    marker = os.path.join(_DIR, ".venv", ".installed_" + key)

    def _write_marker(h):
        try:
            with open(marker, "w", encoding="utf-8") as mf:
                mf.write(h)
        except Exception:
            pass

    if os.path.isfile(req):
        req_hash = hashlib.sha256(open(req, "rb").read()).hexdigest()
        have = _venv_has_packages(venv_py, key)
        marker_exists = os.path.isfile(marker)
        marker_ok = False
        if marker_exists:
            try:
                marker_ok = (open(marker, encoding="utf-8").read().strip() == req_hash)
            except Exception:
                marker_ok = False

        if marker_ok:
            pass                                  # unchanged and already installed
        elif have and not marker_exists:
            # Copied/cloned repo: packages already importable, only the marker is
            # missing. DON'T reinstall — re-touching DLLs can trip Smart App
            # Control / antivirus (e.g. rasterio's GDAL DLLs). Just record the hash.
            _write_marker(req_hash)
        else:
            # Packages missing, OR requirements changed since last install (marker
            # present but hash differs — S6). (Re)install: pip is idempotent, so
            # satisfied packages are skipped and only new/updated ones are added.
            set_status(f"Installing {TOOLS[key]['title']} packages — "
                       "this can take a few minutes…")
            log_path = os.path.join(_DIR, ".venv", f"install_{key}.log")
            try:
                with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
                    # old pip in a fresh venv can fail on modern wheels — upgrade first
                    subprocess.run([venv_py, "-m", "pip", "install", "--upgrade", "pip",
                                    "--no-warn-script-location"],
                                   stdout=lf, stderr=subprocess.STDOUT, text=True,
                                   timeout=600)
                    lf.write("\n--- installing requirements ---\n"); lf.flush()
                    r = subprocess.run([venv_py, "-m", "pip", "install", "-r", req,
                                        "--no-warn-script-location"],
                                       stdout=lf, stderr=subprocess.STDOUT, text=True,
                                       timeout=1800)
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    "pip install timed out. Check your internet connection and "
                    f"the log:\n{log_path}")
            if r.returncode != 0:
                tail = ""
                try:
                    tail = open(log_path, encoding="utf-8", errors="replace").read()[-800:]
                except Exception:
                    pass
                raise RuntimeError(f"pip install failed — full log:\n{log_path}\n\n…{tail}")
            _write_marker(req_hash)
    return venv_py


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sentinel Foundry")
        self.configure(bg=BG)
        self.minsize(660, 480)
        self._busy = False
        try:
            self.eval("tk::PlaceWindow . center")
        except Exception:
            pass
        try:
            _ico = os.path.join(_DIR, "sentinel_foundry.ico")
            _png = os.path.join(_DIR, "sentinel_foundry.png")
            if os.name == "nt" and os.path.isfile(_ico):
                self.iconbitmap(default=_ico)
            elif os.path.isfile(_png):
                self._iconimg = tk.PhotoImage(file=_png)   # keep ref so it's not GC'd
                self.iconphoto(True, self._iconimg)
        except Exception:
            pass
        self._status = tk.StringVar(value="")
        self._build()

    def _build(self):
        head = tk.Frame(self, bg=BG); head.pack(fill=tk.X, pady=(24, 4))
        tk.Label(head, text="Sentinel Foundry", bg=BG, fg=FG,
                 font=(FONT, 24, "bold")).pack()
        tk.Label(head, text="Choose a tool — turn Sentinel imagery into analysis-ready COG indices",
                 bg=BG, fg=MUTED, font=(FONT, 10)).pack(pady=(4, 0))

        cards = tk.Frame(self, bg=BG); cards.pack(expand=True, fill=tk.BOTH, padx=22, pady=14)
        self._cards = {}
        for key in ("sar", "opt"):
            self._cards[key] = self._make_card(cards, key)

        tk.Label(self, textvariable=self._status, bg=BG, fg=MUTED,
                 font=(FONT, 9), wraplength=600).pack(pady=(0, 6))
        self._pbar = ttk.Progressbar(self, mode="indeterminate", length=320)
        self._pbar.pack(pady=(0, 10))

        # ── manual prerequisites ──────────────────────────────────────────
        pf = tk.Frame(self, bg=BG); pf.pack(fill=tk.X, padx=22, pady=(2, 14))
        tk.Frame(pf, bg="#2A2C3A", height=1).pack(fill=tk.X, pady=(0, 8))
        tk.Label(pf, text="Prerequisites to install manually (not Python packages):",
                 bg=BG, fg=MUTED, font=(FONT, 9, "bold")).pack(anchor="w")
        tk.Label(pf, text="SNAP and GDAL are required by SAR Foundry.",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(0, 4))
        row = tk.Frame(pf, bg=BG); row.pack(anchor="w")
        for label, url in PREREQS:
            lk = tk.Label(row, text="↗ " + label, bg=BG, fg="#69B7FF",
                          font=(FONT, 9, "underline"), cursor="hand2")
            lk.pack(side=tk.LEFT, padx=(0, 16))
            lk.bind("<Button-1>", lambda e, u=url: _open_url(u))

    def _make_card(self, parent, key):
        info = TOOLS[key]
        f = tk.Frame(parent, bg=CARD, cursor="hand2",
                     highlightthickness=1, highlightbackground="#2A2C3A")
        f.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=10, pady=4, ipadx=10, ipady=14)
        tk.Frame(f, bg=info["color"], height=4).pack(fill=tk.X, side=tk.TOP)
        tk.Label(f, text=info["icon"], bg=CARD, fg=info["color"], font=(FONT, 28)).pack(pady=(20, 4))
        tk.Label(f, text=info["title"], bg=CARD, fg=FG, font=(FONT, 16, "bold")).pack()
        tk.Label(f, text=info["sub"], bg=CARD, fg=MUTED, font=(FONT, 9),
                 wraplength=230, justify=tk.CENTER).pack(pady=(4, 14), padx=14)
        btn = tk.Label(f, text="  Open  ", bg=info["color"], fg="#FFFFFF",
                       font=(FONT, 11, "bold"), padx=10, pady=6, cursor="hand2")
        btn.pack(pady=(0, 8))

        def _go(_=None): self._open(key)
        def _enter(_=None):
            if not self._busy:
                f.configure(highlightbackground=info["color"]); btn.configure(bg=info["hover"])
        def _leave(_=None):
            f.configure(highlightbackground="#2A2C3A"); btn.configure(bg=info["color"])
        for w in (f, btn) + tuple(f.winfo_children()):
            w.bind("<Button-1>", _go)
        f.bind("<Enter>", _enter); f.bind("<Leave>", _leave)
        btn.bind("<Enter>", _enter); btn.bind("<Leave>", _leave)
        return f

    def _set_status(self, text):
        self.after(0, lambda: self._status.set(text))

    def _open(self, key):
        if self._busy:
            return
        info = TOOLS[key]
        script = os.path.join(_DIR, info["script"])
        if not os.path.isfile(script):
            messagebox.showerror("Sentinel Foundry", f"Script not found:\n{script}")
            return
        if key == "sar":
            missing = []
            if not _find_gpt():
                missing.append("ESA SNAP  (gpt — install with the Microwave Toolbox)")
            if not _find_gdal():
                missing.append("GDAL  (gdal_translate)")
            if missing:
                go = messagebox.askyesno(
                    "Prerequisites not found",
                    "SAR Foundry needs these to process scenes, but they were not found:\n\n"
                    "  - " + "\n  - ".join(missing) +
                    "\n\nInstall them from the links at the bottom of the launcher window.\n"
                    "(SNAP must include the Microwave Toolbox.)\n\nOpen SAR Foundry anyway?")
                if not go:
                    return
        self._busy = True
        self._pbar.start(12)
        threading.Thread(target=self._worker, args=(key, script), daemon=True).start()

    def _worker(self, key, script):
        info = TOOLS[key]
        try:
            venv_py = _ensure_env(key, self._set_status)
            self._set_status(f"Opening {info['title']} …")
            # Launch with the windowed interpreter (pythonw) so no empty
            # console window appears. Fall back to python + CREATE_NO_WINDOW.
            exe, kw = venv_py, {"cwd": _DIR}
            if os.name == "nt":
                pw = os.path.join(os.path.dirname(venv_py), "pythonw.exe")
                if os.path.isfile(pw):
                    exe = pw
                else:
                    kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # Capture the child's stderr — with pythonw a crash on import shows no
            # window and no console, so without this it fails completely silently.
            child_log = os.path.join(_DIR, f"launch_{key}.log")
            lf = open(child_log, "w", encoding="utf-8", errors="replace")
            proc = subprocess.Popen([exe, script], stdout=lf,
                                    stderr=subprocess.STDOUT, **kw)
            # If it dies almost immediately, it crashed on startup — surface it.
            try:
                if proc.wait(timeout=3) != 0:
                    tail = ""
                    try:
                        lf.flush()
                        tail = open(child_log, encoding="utf-8",
                                    errors="replace").read()[-800:]
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"{info['title']} exited on startup.\nLog: {child_log}\n\n…{tail}")
            except subprocess.TimeoutExpired:
                pass   # still running after 3 s → launched fine
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                "Sentinel Foundry", f"Could not start {info['title']}:\n\n{e}"))
            self._set_status("")
        finally:
            self._busy = False
            self.after(0, self._pbar.stop)


def main():
    _check_python()
    App().mainloop()


if __name__ == "__main__":
    main()

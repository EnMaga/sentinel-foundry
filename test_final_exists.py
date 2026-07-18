"""Self-check for _final_exists (date+orbit parsing → final-product match).
Run in-venv:  .venv\\Scripts\\python test_final_exists.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s1_pipeline_ui import _final_exists


def touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()


def test():
    with tempfile.TemporaryDirectory() as out:
        # a finished ASC date (20250711), stored in the ASC subfolder
        touch(os.path.join(out, "ASC",
              "S1_20250711_SNAP_tile_35TMK_fields_c06_refined_lee_cop30_sigma0_C_ASC_VV.tif"))

        # scene name with hour 16 -> ASC -> matches the existing final
        asc_name = "S1C_IW_GRDH_1SDV_20250711T161608_20250711T161633_003175_0066DA_BB1A"
        assert _final_exists(out, asc_name) is True, "should match the ASC final"

        # same date but hour 04 -> DSC -> no DSC final -> not skipped
        dsc_name = "S1C_IW_GRDH_1SDV_20250711T041239_20250711T041304_003182_00670D_C4B9"
        assert _final_exists(out, dsc_name) is False, "DSC has no final -> must download"

        # a different date entirely -> no final
        other = "S1A_IW_GRDH_1SDV_20250806T160107_20250806T160132_059980_07737E_E135"
        assert _final_exists(out, other) is False, "20250806 has no final"

        # malformed name / empty out_dir -> False, never crash
        assert _final_exists(out, "no_date_here") is False
        assert _final_exists("", asc_name) is False
    print("  ok: _final_exists matches date+orbit, ignores other dates/orbits")


def test_already_have():
    from s1_pipeline_ui import _already_have
    with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as snap:
        cfg = {"out_dir": out, "snap_dir": snap}
        # date A finalized (in out_dir), date B only SNAP'd (in snap_dir)
        touch(os.path.join(out, "ASC",
              "S1_20250711_SNAP_tile_35TMK_C_ASC_VV.tif"))          # final, ASC
        touch(os.path.join(snap,
              "S1_20250806_SNAP_tile_35TMK_C_DSC_sc01.tif"))        # snap tile, DSC
        a = "S1C_IW_GRDH_1SDV_20250711T161608_x_x_x_x"              # ASC (hr16)
        b = "S1A_IW_GRDH_1SDV_20250806T041239_x_x_x_x"              # DSC (hr04)
        c = "S1C_IW_GRDH_1SDV_20250901T161608_x_x_x_x"              # neither
        assert _already_have(cfg, a) is True,  "final in out_dir -> have"
        assert _already_have(cfg, b) is True,  "SNAP tile in snap_dir -> have"
        assert _already_have(cfg, c) is False, "absent everywhere -> download"
    print("  ok: _already_have covers both SNAP GeoTIFFs and finals")


if __name__ == "__main__":
    test()
    test_already_have()
    print("ALL PASS")

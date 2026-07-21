"""AOI-scoping guard for the pipeline-first batch layout: many AOIs share one
out_dir / Done_files_S1, so the skip globs must NOT match another AOI's date.
Run: python test_aoi_scoping.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
from s1_pipeline_ui import _final_exists, _group_done_marker

SCENE = "S1A_IW_GRDH_1SDV_20240715T163030_20240715T163055_054xyz.SAFE"  # -> 20240715 ASC


def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()


def demo():
    with tempfile.TemporaryDirectory() as d:
        # one merged out_dir holding AOI_A's final only
        _touch(os.path.join(d, "ASC",
               "S1_20240715_SNAP_AOI_A_c01_refined_lee_cop30_sigma0_A_ASC_VV.tif"))

        assert _final_exists(d, SCENE, "AOI_A") is True,  "AOI_A must see its own final"
        assert _final_exists(d, SCENE, "AOI_B") is False, "AOI_B must NOT see AOI_A's final"
        assert _final_exists(d, SCENE, "") is True,       "blank label = legacy broad match"

        # .done markers in a shared Done_files_S1 (out_dir/Done_files_S1)
        dd = os.path.join(d, "Done_files_S1")
        _touch(os.path.join(dd,
               "S1_20240715_SNAP_AOI_A_refined_lee_cop30_sigma0_A_ASC.done"))
        cfg_a = {"out_dir": d, "aoi_label": "AOI_A"}
        cfg_b = {"out_dir": d, "aoi_label": "AOI_B"}
        assert _group_done_marker(cfg_a, SCENE) is True,  "AOI_A must see its own .done"
        assert _group_done_marker(cfg_b, SCENE) is False, "AOI_B must NOT see AOI_A's .done"
    print("OK: AOI scoping holds — no cross-AOI skip/delete in a merged folder")


if __name__ == "__main__":
    demo()

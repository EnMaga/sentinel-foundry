"""Regression test for the cross-zone "0% valid seam" bug.

A reprojected SCL window can carry a small island of valid pixels (classes 4/5/6)
ringed by NODATA fill whose value is NOT 0 — satellitetools stamps 99 and a
cross-zone reproject_match adds -32768. The old mask lumped that fill into
"cloud"; binary_fill_holes then swallowed the whole island, yielding 0 valid
pixels and a spurious "no usable pixels" for a day that clearly has data.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
import numpy as np
from s2_pipeline_ui import _scl_valid_mask


def test_valid_island_survives_nonzero_fill():
    # 200x200 window that is almost entirely upstream fill (99 + reproject -32768),
    # with a solid 60x60 island of vegetation/bare-soil — the exact live shape
    # from tile 34TGR on 2024-09-24 (uniq=[-32768, 4, 5, 6, 99]).
    scl = np.full((200, 200), 99, dtype=np.int16)
    scl[:40, :] = -32768                      # triangular reproject fill
    scl[70:130, 70:130] = 4                   # vegetation island
    scl[90:110, 90:110] = 5                   # bare soil inside it

    m = _scl_valid_mask(scl)
    assert m.sum() > 0, "valid field island wiped out by non-zero NODATA fill"
    # the island is real data → most of it must be kept
    assert m[70:130, 70:130].mean() > 0.9, "island should be almost fully kept"
    # the fill sea must NOT be kept
    assert not m[:40, :].any(), "reproject fill (-32768) must be masked"
    assert not m[150:, :5].any(), "satellitetools fill (99) must be masked"


def test_real_clouds_still_masked():
    scl = np.full((100, 100), 4, dtype=np.int16)   # all vegetation
    scl[40:60, 40:60] = 9                            # a solid cloud block
    m = _scl_valid_mask(scl)
    assert not m[45:55, 45:55].any(), "cloud core must be masked"
    assert m[:20, :20].all(), "clear vegetation must be kept"


if __name__ == "__main__":
    test_valid_island_survives_nonzero_fill()
    test_real_clouds_still_masked()
    print("test_scl_mask OK")

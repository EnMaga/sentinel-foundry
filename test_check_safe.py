"""Minimal self-checks for the B7 (single-pol not-CORRUPT) and pol-detection
guards in check_safe.py. Run: python test_check_safe.py"""
import check_safe as cs


def test_expected_pols():
    dv = "S1A_IW_GRDH_1SDV_20240926T170657_..._B85C.SAFE"
    sv = "S1A_IW_GRDH_1SSV_20240926T170657_..._B85C.zip"
    dh = "S1A_IW_GRDH_1SDH_20240926T170657_..._B85C.SAFE"   # HH/HV — not tracked
    assert cs._expected_pols(dv) == {"vv", "vh"}
    assert cs._expected_pols(sv) == {"vv"}
    assert cs._expected_pols(dh) is None
    assert cs._expected_pols("random_name.zip") is None


def test_single_pol_is_not_corrupt():
    # A valid single-pol (VV-only) scene must be OK, never CORRUPT (would be deleted).
    big = cs.MIN_BAND_BYTES + 1
    issues = []
    cs._check_band_pair({"vv": big}, issues, expected={"vv"})
    status, _ = cs._worst(issues)
    assert status == cs.OK, status


def test_dual_pol_missing_band_is_suspect_not_corrupt():
    big = cs.MIN_BAND_BYTES + 1
    issues = []
    cs._check_band_pair({"vv": big}, issues, expected={"vv", "vh"})
    status, _ = cs._worst(issues)
    assert status == cs.SUSPECT, status   # SUSPECT never auto-deletes


def test_truncated_present_band_is_corrupt():
    issues = []
    cs._check_band_pair({"vv": 10, "vh": cs.MIN_BAND_BYTES + 1}, issues,
                        expected={"vv", "vh"})
    status, _ = cs._worst(issues)
    assert status == cs.CORRUPT, status   # a truncated band IS deletable


if __name__ == "__main__":
    test_expected_pols()
    test_single_pol_is_not_corrupt()
    test_dual_pol_missing_band_is_suspect_not_corrupt()
    test_truncated_present_band_is_corrupt()
    print("all check_safe guards OK")

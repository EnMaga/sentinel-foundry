"""Self-check for the S1/S4 pure helpers (_validate_expr, _safe_float) without
importing the GUI modules (which self-bootstrap a venv on import). We extract the
real function source by name and exec it in a clean namespace.
Run: python test_expr.py"""
import ast

WANT = {"_EXPR_NODES", "_validate_expr", "_safe_float", "_cdse_auth_data"}


def _load(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    keep = [n for n in tree.body
            if (isinstance(n, (ast.FunctionDef,)) and n.name in WANT)
            or (isinstance(n, ast.Assign) and any(
                getattr(t, "id", None) in WANT for t in n.targets))]
    ns = {}
    exec("import ast as _ast", ns)
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), ns)
    return ns


def _check(path):
    ns = _load(path)
    v, sf = ns["_validate_expr"], ns["_safe_float"]
    # valid expressions
    v("(VV - VH) / (VV + VH + 1e-9)", {"VV", "VH"})
    v("np.log10(VV)", {"VV"})
    v("np.where(VV > 0, VV, 0)", {"VV"})
    # each of these must be rejected
    for bad in ["__import__('os')", "open('x')", "VV.__class__.__mro__",
                "foo + VV", "().__class__"]:
        try:
            v(bad, {"VV"})
            raise AssertionError(f"{path}: allowed bad expr: {bad}")
        except ValueError:
            pass
    # _safe_float never raises
    assert sf("abc", 5.0) == 5.0
    assert sf("", 5.0) == 5.0
    assert sf(None, 5.0) == 5.0
    assert sf("2.5", 5.0) == 2.5
    print(f"{path}: validator + _safe_float OK")

    # CDSE: username/password for the initial grant only; renewals use the
    # rotating refresh token elsewhere (_refresh_token), never this payload.
    if "_cdse_auth_data" in ns:
        cad = ns["_cdse_auth_data"]
        assert cad({"cdse_user": "a", "cdse_pass": "b"}) == {
            "grant_type": "password", "username": "a", "password": "b"}
        for bad_cfg in [{}, {"cdse_user": "  ", "cdse_pass": ""},
                        {"cdse_user": "a"}, {"cdse_pass": "b"}]:
            try:
                cad(bad_cfg)
                raise AssertionError(f"{path}: accepted cfg without credentials: {bad_cfg}")
            except RuntimeError as e:
                assert "credentials missing" in str(e).lower()
        print(f"{path}: CDSE initial-grant guard OK")


if __name__ == "__main__":
    _check("s1_pipeline_ui.py")
    _check("s2_pipeline_ui.py")
    print("all expression guards OK")

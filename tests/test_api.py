"""Web API tests on the synthetic phantom. No real imaging; Modal is stubbed.

Each test runs the FastAPI app in-process with a temporary WORK_DIR, logs in
with a test password, and drives it the way the browser does.
"""

import gzip
import io
import json
import time
import zipfile

import nibabel as nib
import numpy as np
import pytest
from fastapi.testclient import TestClient

from stoneplanner import phantom
from stoneplanner_api.app import create_app
from stoneplanner_api.config import Settings

PASSWORD = "test-password"
FAST = {"target_mode": "stone", "n_entry_points": 400}


def nii_gz(arr, affine=phantom.AFFINE, descrip=b""):
    img = nib.Nifti1Image(arr, affine)
    img.header["descrip"] = descrip
    return gzip.compress(img.to_bytes())


@pytest.fixture(scope="module")
def phantom_files():
    ct, organs, stones = phantom.make_phantom()
    return {"ct": nii_gz(ct.astype(np.int16), descrip=b"DOE^JOHN MRN12345"),
            "organs": nii_gz(organs.astype(np.uint8)),
            "stones": nii_gz(stones.astype(np.int16))}


def make_client(tmp_path, segmenter=None, **overrides):
    settings = Settings(secret_key="k" * 32, app_password=PASSWORD, work_dir=tmp_path / "work",
                        cookie_secure=False, **overrides)
    return TestClient(create_app(settings, segmenter=segmenter))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        yield c


def upload(client, files, keys=("ct", "organs", "stones"), name="case_007.nii.gz"):
    payload = {k: (name if k == "ct" else f"{k}.nii.gz", files[k], "application/gzip")
               for k in keys}
    return client.post("/api/cases", files=payload)


def wait(client, case_id, kind, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/cases/{case_id}").json()["jobs"].get(kind, {})
        if state.get("state") in ("done", "error"):
            return state
        time.sleep(0.2)
    raise AssertionError(f"{kind} did not finish")


# ---------------------------------------------------------------------------
# Auth


def test_health_is_open_and_cases_need_login(tmp_path):
    with make_client(tmp_path) as c:
        health = c.get("/api/health").json()
        assert health == {"ok": True, "auth_configured": True, "segmentation_configured": False}
        assert c.get("/api/params").status_code == 401
        assert c.post("/api/cases").status_code == 401
        assert c.get("/api/session").json()["authenticated"] is False


def test_wrong_password_and_rate_limit(tmp_path):
    with make_client(tmp_path) as c:
        for _ in range(10):
            assert c.post("/api/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_no_password_configured_fails_closed(tmp_path):
    settings = Settings(secret_key="k" * 32, app_password="", work_dir=tmp_path / "w",
                        cookie_secure=False)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").json()["auth_configured"] is False
        assert c.post("/api/login", json={"password": ""}).status_code == 401


def test_cookie_flags(tmp_path):
    with make_client(tmp_path) as c:
        cookie = c.post("/api/login", json={"password": PASSWORD}).headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert c.get("/api/session").json()["authenticated"] is True


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# Uploads


def test_upload_rewrites_header_and_builds_stone_table(client, phantom_files):
    r = upload(client, phantom_files)
    assert r.status_code == 200, r.text
    case = r.json()
    assert case["case_id"] == "case_007"
    assert case["shape"] == [phantom.N] * 3
    assert case["has"]["organs"] and case["has"]["stones"]
    assert len(case["stones"]) == 1 and case["stones"][0]["laterality"] == "right"
    ct = client.get(f"/api/cases/{case['id']}/files/ct")
    assert ct.status_code == 200
    assert b"MRN12345" not in gzip.decompress(ct.content)       # fresh header


def test_organ_display_map_is_compact(client, phantom_files):
    from stoneplanner_api.overlay import DISPLAY_GROUPS

    case_id = upload(client, phantom_files).json()["id"]
    r = client.get(f"/api/cases/{case_id}/files/organs_display")
    data = np.asanyarray(nib.Nifti1Image.from_bytes(gzip.decompress(r.content)).dataobj)
    groups = client.get("/api/labels").json()["display_groups"]
    assert groups == list(DISPLAY_GROUPS)
    present = {groups[v - 1] for v in np.unique(data) if v > 0}
    assert present == {"kidney", "colon", "rib", "vertebrae", "hip"}


def test_rejects_dicom_and_junk(client):
    dicom = b"\0" * 128 + b"DICM" + b"\0" * 400
    r = client.post("/api/cases", files={"ct": ("x.dcm", dicom)})
    assert r.status_code == 400 and "DICOM is not accepted" in r.json()["detail"]
    r = client.post("/api/cases", files={"ct": ("x.nii.gz", gzip.compress(b"hello" * 100))})
    assert r.status_code == 400
    assert client.post("/api/cases").status_code == 400


def test_rejects_grid_mismatch_and_half_segmentation(client, phantom_files):
    _, organs, _ = phantom.make_phantom()
    shifted = nii_gz(organs.astype(np.uint8), affine=phantom.AFFINE_FLIPPED)
    files = {**phantom_files, "organs": shifted}
    r = upload(client, files)
    assert r.status_code == 400 and "grid" in r.json()["detail"]
    r = upload(client, phantom_files, keys=("ct", "organs"))
    assert r.status_code == 400


def test_upload_size_limit(tmp_path, phantom_files):
    with make_client(tmp_path, max_upload_mb=0) as c:
        c.post("/api/login", json={"password": PASSWORD})
        assert upload(c, phantom_files).status_code == 413


def test_cases_are_private_to_their_session(tmp_path, phantom_files):
    with make_client(tmp_path) as a:
        a.post("/api/login", json={"password": PASSWORD})
        case_id = upload(a, phantom_files).json()["id"]
        b = TestClient(a.app)
        b.post("/api/login", json={"password": PASSWORD})
        assert b.get(f"/api/cases/{case_id}").status_code == 404
        assert b.get(f"/api/cases/{case_id}/files/ct").status_code == 404
        assert a.get(f"/api/cases/{case_id}").status_code == 200


def test_parallel_requests_never_lose_the_case(client, phantom_files):
    """The viewer loads several files at once; each request touches case.json."""
    from concurrent.futures import ThreadPoolExecutor

    case_id = upload(client, phantom_files).json()["id"]
    urls = [f"/api/cases/{case_id}/files/{k}" for k in ("ct", "organs", "stones")] * 20
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda u: client.get(u).status_code, urls))
    assert codes == [200] * len(urls)


# ---------------------------------------------------------------------------
# Planning, files, bundles


def test_plan_job_files_and_overlay(client, phantom_files):
    case_id = upload(client, phantom_files).json()["id"]
    r = client.post(f"/api/cases/{case_id}/plan", json={"side": "auto", "params": FAST})
    assert r.status_code == 202
    state = wait(client, case_id, "plan")
    assert state["state"] == "done", state
    case = client.get(f"/api/cases/{case_id}").json()
    plan = case["plan"]
    assert plan["side"] == "right"
    assert plan["tracts"][0]["reachable_fraction"] == pytest.approx(1.0)
    assert plan["params"]["n_entry_points"] == 400
    assert case["has"]["overlay"]

    overlay = nib.Nifti1Image.from_bytes(gzip.decompress(
        client.get(f"/api/cases/{case_id}/files/overlay").content))
    assert set(np.unique(np.asanyarray(overlay.dataobj))) == {0, 1, 2, 3}

    csv_r = client.get(f"/api/cases/{case_id}/files/tracts_csv")
    assert 'attachment; filename="case_007_tracts.csv"' in csv_r.headers["content-disposition"]
    assert csv_r.text.startswith("rank,")
    assert client.get(f"/api/cases/{case_id}/files/secret").status_code == 404


def test_plan_parameter_validation(client, phantom_files):
    case_id = upload(client, phantom_files).json()["id"]
    url = f"/api/cases/{case_id}/plan"
    assert client.post(url, json={"params": {"w_colonn": 1}}).status_code == 400
    assert client.post(url, json={"params": {"w_colon": "lots"}}).status_code == 400
    assert client.post(url, json={"params": {"organ_labels": {}}}).status_code == 400
    assert client.post(url, json={"params": {"target_mode": "x"}}).status_code == 400
    assert client.post(url, json={"side": "middle"}).status_code == 400
    assert client.post(url, json={"params": {"n_entry_points": 10**7}}).status_code == 400


def test_plan_needs_segmentation(client, phantom_files):
    case_id = upload(client, phantom_files, keys=("ct",)).json()["id"]
    client.post(f"/api/cases/{case_id}/plan", json={"params": FAST})
    state = wait(client, case_id, "plan")
    assert state["state"] == "error" and "segment" in state["message"]


def test_bundle_round_trip_skips_segmentation(client, phantom_files):
    case_id = upload(client, phantom_files).json()["id"]
    client.post(f"/api/cases/{case_id}/plan", json={"params": FAST})
    wait(client, case_id, "plan")
    bundle = client.get(f"/api/cases/{case_id}/bundle")
    assert bundle.headers["content-disposition"] == 'attachment; filename="case_007_case.zip"'
    names = set(zipfile.ZipFile(io.BytesIO(bundle.content)).namelist())
    assert {"case.json", "ct.nii.gz", "organs.nii.gz", "stones.nii.gz", "tracts.json"} <= names

    client.delete(f"/api/cases/{case_id}")
    assert client.get(f"/api/cases/{case_id}").status_code == 404
    reopened = client.post("/api/cases",
                           files={"bundle": ("case_007_case.zip", bundle.content)}).json()
    assert reopened["case_id"] == "case_007"
    assert reopened["has"] == {"organs": True, "stones": True, "tracts_json": True,
                               "overlay": True}
    assert reopened["plan"]["tracts"][0]["reachable_fraction"] == pytest.approx(1.0)


def test_bundle_rejects_foreign_zip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd", "x")
    r = client.post("/api/cases", files={"bundle": ("x.zip", buf.getvalue())})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Segmentation through the (stubbed) GPU service


def test_segmentation_via_service(tmp_path, phantom_files):
    calls = []

    def fake_modal(ct_bytes: bytes) -> bytes:
        calls.append(len(ct_bytes))
        return phantom_files["organs"]

    # The phantom's stone exists only in its label map; give it stone HU in the
    # CT so thresholding after segmentation has something to find.
    ct, _, stones = phantom.make_phantom()
    ct[stones > 0] = 1000
    files = {**phantom_files, "ct": nii_gz(ct.astype(np.int16))}
    with make_client(tmp_path, segmenter=fake_modal) as c:
        c.post("/api/login", json={"password": PASSWORD})
        assert c.get("/api/health").json()["segmentation_configured"] is True
        case_id = upload(c, files, keys=("ct",)).json()["id"]
        assert c.post(f"/api/cases/{case_id}/segment").status_code == 202
        state = wait(c, case_id, "segment")
        assert state["state"] == "done", state
        case = c.get(f"/api/cases/{case_id}").json()
        assert calls and case["has"]["stones"] and len(case["stones"]) == 1


def test_segmentation_not_configured(client, phantom_files):
    case_id = upload(client, phantom_files, keys=("ct",)).json()["id"]
    r = client.post(f"/api/cases/{case_id}/segment")
    assert r.status_code == 503 and "Segment locally" in r.json()["detail"]


def test_segmentation_service_failure_is_reported(tmp_path, phantom_files):
    def broken(_ct: bytes) -> bytes:
        raise RuntimeError("GPU quota exceeded")

    with make_client(tmp_path, segmenter=broken) as c:
        c.post("/api/login", json={"password": PASSWORD})
        case_id = upload(c, phantom_files, keys=("ct",)).json()["id"]
        c.post(f"/api/cases/{case_id}/segment")
        state = wait(c, case_id, "segment")
        assert state["state"] == "error" and "GPU quota exceeded" in state["message"]


# ---------------------------------------------------------------------------
# Expiry and the front end


def test_idle_cases_expire(client, phantom_files):
    case_id = upload(client, phantom_files).json()["id"]
    store = client.app.state.store
    assert store.cleanup(now=time.time() + 60) == 0
    assert store.cleanup(now=time.time() + store.ttl_s + 1) == 1
    assert client.get(f"/api/cases/{case_id}").status_code == 404


def test_work_dir_wiped_at_startup(tmp_path):
    leftover = tmp_path / "work" / "old_case"
    leftover.mkdir(parents=True)
    with make_client(tmp_path):
        assert not leftover.exists()


def test_params_listing(client):
    fields = {f["name"]: f["default"] for f in client.get("/api/params").json()["fields"]}
    assert fields["w_colon"] == 0.5 and fields["target_mode"] == "both"
    assert "organ_labels" not in fields


def test_serves_spa_with_fallback(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "main.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("no")
    with make_client(tmp_path, frontend_dist=dist) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/cases/whatever").text == "<html>app</html>"
        assert c.get("/assets/main.js").text == "console.log(1)"
        assert c.get("/../secret.txt").text == "<html>app</html>"
        assert c.get("/api/nothing").status_code == 404
        assert json.loads(c.get("/api/health").text)["ok"]

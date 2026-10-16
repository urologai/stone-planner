"""Local ingest page tests: clinical form, case folders, MRN key, full loop.

Synthetic DICOM only (tests/dicom_series.py). The full-loop test ingests the
planner phantom as DICOM, segments it through a stubbed GPU service, plans,
saves the results to the case folder, and runs scripts/validate.py on the
data folder the page produced.
"""

import csv
import gzip
import importlib.util
import json
import time
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from dicom_series import write_phantom_series, write_radiograph, write_series
from fastapi.testclient import TestClient

from stoneplanner import phantom
from stoneplanner_api.app import create_app
from stoneplanner_api.config import Settings
from stoneplanner_api.library import Library, LibraryError, validate_clinical

PASSWORD = "pw"
MRN = "MRN12345"


def clinical(**changes):
    form = {
        "ct": {"position": "supine", "site": "outside"},
        "operative": {"laterality": "right", "or_position": "prone",
                      "tracts": [{"calyx": "lower", "approach": "subcostal"},
                                 {"calyx": "upper", "approach": "supracostal"}],
                      "sheath_fr": 30, "guidance": "fluoroscopy"},
        "stone": {"location": ["renal pelvis", "lower calyx"], "staghorn": "partial",
                  "largest_mm": 28, "manual_volume_mm3": 90},
        "postop": {"stone_free": False, "residual_mm": 4},
        "complications": {"pleural_injury": False, "colon_injury": False,
                          "transfusion": True},
        "demographics": {"age": 95, "sex": "female", "bmi": 31.5},
    }
    for path, value in changes.items():
        section, key = path.split(".")
        form[section][key] = value
    return form


# ---------------------------------------------------------------------------
# Form validation


def test_clinical_form_normalised():
    rec = validate_clinical(clinical())
    assert rec["demographics"]["age"] == 90            # 90 and over recorded as 90
    assert rec["operative"]["tracts"][1] == {"calyx": "upper", "approach": "supracostal"}
    assert rec["postop"]["residual_mm"] == 4.0


@pytest.mark.parametrize("change, message", [
    ({"operative.tracts": []}, "1 to 6 tracts"),
    ({"operative.laterality": "both"}, "laterality"),
    ({"stone.location": ["kidney of John Doe"]}, "stone location"),
    ({"stone.largest_mm": "big"}, "number"),
    ({"postop.residual_mm": None}, "residual"),
    ({"complications.colon_injury": "no"}, "yes or no"),
    ({"demographics.bmi": 5}, "BMI"),
])
def test_clinical_form_errors(change, message):
    with pytest.raises(LibraryError, match=message):
        validate_clinical(clinical(**change))


def test_stone_free_needs_no_residual():
    rec = validate_clinical(clinical(**{"postop.stone_free": True, "postop.residual_mm": None}))
    assert rec["postop"]["residual_mm"] is None


# ---------------------------------------------------------------------------
# Library guards


def test_key_file_must_be_outside_data_dir(tmp_path):
    with pytest.raises(RuntimeError, match="outside DATA_DIR"):
        Library(tmp_path / "data", tmp_path / "data" / "key.csv")


def test_staging_is_private_and_leftovers_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    stale = tmp_path / "stoneplanner-staging-old"
    (stale / "tok").mkdir(parents=True)
    lib = Library(tmp_path / "data", tmp_path / "key" / "key.csv")
    assert not stale.exists()
    assert oct(lib.staging_root.stat().st_mode & 0o777) == "0o700"


def test_refuses_to_run_on_render(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError, match="Render"):
        Library(tmp_path / "data", tmp_path / "key" / "key.csv")


# ---------------------------------------------------------------------------
# Through the API


def _organs_for(ct_bytes: bytes) -> bytes:
    """Stub GPU service: the phantom's organ map on the grid of the CT sent."""
    img = nib.Nifti1Image.from_bytes(gzip.decompress(ct_bytes))
    _, organs, _ = phantom.make_phantom(affine=img.affine)
    return gzip.compress(nib.Nifti1Image(organs.astype(np.uint8), img.affine).to_bytes())


@pytest.fixture
def local(tmp_path):
    settings = Settings(secret_key="k" * 32, app_password=PASSWORD, cookie_secure=False,
                        work_dir=tmp_path / "work", data_dir=tmp_path / "data",
                        key_file=tmp_path / "private" / "mrn_key.csv")
    with TestClient(create_app(settings, segmenter=_organs_for)) as client:
        client.post("/api/login", json={"password": PASSWORD})
        yield client, tmp_path


def _scan(client, folder: Path) -> dict:
    files = [("files", (f"x{i}", p.read_bytes())) for i, p in
             enumerate(sorted(folder.rglob("*.dcm")))]
    r = client.post("/api/ingest/scan", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def _wait(client, case_id, kind):
    for _ in range(600):
        state = client.get(f"/api/cases/{case_id}").json()["jobs"].get(kind, {})
        if state.get("state") in ("done", "error"):
            return state
        time.sleep(0.2)
    raise AssertionError(kind)


def test_ingest_disabled_without_data_dir(tmp_path):
    settings = Settings(secret_key="k" * 32, app_password=PASSWORD, cookie_secure=False,
                        work_dir=tmp_path / "work")
    with TestClient(create_app(settings)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        assert c.get("/api/ingest").json() == {"enabled": False}
        assert c.post("/api/ingest/scan", files=[("files", ("a", b"x"))]).status_code == 404


def test_scan_lists_series_and_picks_axial_ct(local):
    client, tmp = local
    study = tmp / "study"
    write_phantom_series(study / "axial")
    write_series(study / "cor", series_uid="1.3.6.1.4.1.99999.7.11", series_number=4,
                 description="COR", orientation="coronal")
    scan = _scan(client, study)
    assert scan["recommended_ct"] == "1.3.6.1.4.1.99999.7.666"
    assert {s["orientation"] for s in scan["series"]} == {"axial", "coronal"}
    junk = client.post("/api/ingest/scan", files=[("files", ("a.txt", b"hello"))])
    assert junk.status_code == 400


def test_full_local_loop(local):
    client, tmp = local
    write_phantom_series(tmp / "study" / "axial")
    write_radiograph(tmp / "postop")
    status = client.get("/api/ingest").json()
    assert status["enabled"] and status["next_case_id"] == "case_001"

    pre = _scan(client, tmp / "study")
    post = _scan(client, tmp / "postop")
    r = client.post("/api/ingest/cases", json={
        "mrn": MRN, "clinical": clinical(),
        "preop": {"staging": pre["staging"], "series_uid": pre["recommended_ct"]},
        "postop": {"staging": post["staging"], "series_uid": post["recommended_radiograph"]},
    })
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["case_id"] == "case_001" and result["next_case_id"] == "case_002"
    assert any("postoperative images by eye" in n for n in result["notes"])

    data, key = tmp / "data", tmp / "private" / "mrn_key.csv"
    folder = data / "case_001"
    assert {p.name for p in folder.iterdir()} >= {
        "case_001.nii.gz", "case_001.json", "case_001_clinical.json", "postop"}
    rows = list(csv.DictReader(key.open()))
    assert rows[0]["case_id"] == "case_001" and rows[0]["mrn"] == MRN
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    for path in data.rglob("*"):                        # the MRN is only in the key file
        if path.is_file():
            raw = path.read_bytes()
            raw = gzip.decompress(raw) if path.suffix == ".gz" else raw
            assert MRN.encode() not in raw and b"DOE^JOHN" not in raw, path

    manual = list(csv.DictReader((data / "manual.csv").open()))
    assert manual[0]["operative_calyx"] == "lower"      # first tract entered
    assert manual[0]["tracts"] == "lower/subcostal;upper/supracostal"
    assert manual[0]["postop_imaging"] == "kub" and manual[0]["age"] == "90"

    again = client.post("/api/ingest/cases", json={
        "mrn": MRN, "clinical": clinical(),
        "preop": {"staging": pre["staging"], "series_uid": pre["recommended_ct"]}})
    assert again.status_code == 400 and "expired" in again.json()["detail"]

    # Open in the planner, segment (stubbed GPU), plan, save back.
    case = client.post("/api/ingest/cases/case_001/open").json()
    assert case["source_case"] == "case_001" and not case["has"]["organs"]
    client.post(f"/api/cases/{case['id']}/segment")
    assert _wait(client, case["id"], "segment")["state"] == "done"
    client.post(f"/api/cases/{case['id']}/plan",
                json={"params": {"target_mode": "stone", "n_entry_points": 400}})
    assert _wait(client, case["id"], "plan")["state"] == "done"
    saved = client.post(f"/api/cases/{case['id']}/save-to-library").json()["written"]
    assert "case_001_tracts.json" in saved and "case_001_stones.csv" in saved
    listed = client.get("/api/ingest").json()["cases"][0]
    assert listed["segmented"] and listed["planned"]
    assert listed["postop_burned_in_suspected"] and not listed["burned_in_suspected"]

    # Reopening brings the saved segmentation and plan back without recomputing.
    reopened = client.post("/api/ingest/cases/case_001/open").json()
    assert reopened["has"]["organs"] and reopened["plan"] is not None

    # The data folder is exactly what validate.py reads.
    spec = importlib.util.spec_from_file_location(
        "validate", Path(__file__).resolve().parents[1] / "scripts" / "validate.py")
    validate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validate)
    assert validate.main([str(data), str(data / "manual.csv"), str(tmp / "report")]) == 0
    row = next(csv.DictReader((tmp / "report" / "validation.csv").open()))
    assert row["case_id"] == "case_001" and row["auto_volume_mm3"] != ""
    assert row["top_tract_group"] in ("upper", "mid", "lower", "pelvis")
    plan = json.loads((folder / "case_001_tracts.json").read_text())
    assert plan["case_id"] == "case_001"


def test_bad_form_leaves_no_case_and_keeps_upload(local):
    client, tmp = local
    write_phantom_series(tmp / "study")
    pre = _scan(client, tmp / "study")
    body = {"mrn": MRN, "clinical": clinical(**{"operative.tracts": []}),
            "preop": {"staging": pre["staging"], "series_uid": pre["recommended_ct"]}}
    r = client.post("/api/ingest/cases", json=body)
    assert r.status_code == 400
    assert not (tmp / "data" / "case_001").exists()
    assert not (tmp / "private" / "mrn_key.csv").exists()
    body["clinical"] = clinical()
    assert client.post("/api/ingest/cases", json=body).status_code == 200

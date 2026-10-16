"""Admin page: settings store, provider choice, connection check and test, the
GPU worker, and the linked workbook.

The GPU worker runs for real (uvicorn on a free port) with a stub in place of
TotalSegmentator that returns the phantom's organ map, so the planner's HTTP
client, the token check and the round trip are all exercised.
"""

import gzip
import json
import socket
import threading
import time
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
import uvicorn
from fastapi.testclient import TestClient
from test_library import PASSWORD, local  # noqa: F401  (local is a fixture)
from test_workbook import COMPLETE, fill

from gpu_worker.server import create_app as create_worker
from stoneplanner import phantom
from stoneplanner_api.admin import AdminStore
from stoneplanner_api.app import create_app
from stoneplanner_api.config import Settings
from stoneplanner_api.library import LibraryError

TOKEN = "w" * 40


def _phantom_organs(ct_path: Path, out_dir: Path, device: str) -> Path:
    img = nib.load(str(ct_path))
    _, organs, _ = phantom.make_phantom(affine=img.affine)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "organs.nii.gz"
    nib.save(nib.Nifti1Image(organs.astype(np.uint8), img.affine), str(out))
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def worker_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        create_worker(token=TOKEN, device="cpu", run=_phantom_organs),
        host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def admin_client(tmp_path):
    """A local install with no test segmenter: the admin page decides."""
    settings = Settings(secret_key="k" * 32, app_password=PASSWORD, cookie_secure=False,
                        work_dir=tmp_path / "work", data_dir=tmp_path / "data",
                        key_file=tmp_path / "private" / "mrn_key.csv")
    with TestClient(create_app(settings)) as client:
        client.post("/api/login", json={"password": PASSWORD})
        yield client, tmp_path


def _phantom_ct() -> bytes:
    ct, _, _ = phantom.make_phantom()
    return gzip.compress(nib.Nifti1Image(ct.astype(np.int16), phantom.AFFINE).to_bytes())


# ---------------------------------------------------------------------------
# Settings store


def test_store_keeps_secrets_private(tmp_path):
    store = AdminStore(tmp_path / "private" / "settings.json", tmp_path / "data")
    store.update({"segmentation_provider": "remote", "remote_url": "http://home-pc:8100",
                  "remote_token": TOKEN})
    path = tmp_path / "private" / "settings.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    public = store.load().public()
    assert public["has_remote_token"] and TOKEN not in json.dumps(public)
    store.update({"remote_token": "", "segmentation_provider": "remote"})   # blank keeps it
    assert store.load().remote_token == TOKEN
    store.update({"segmentation_provider": "none", "clear_remote_token": True})
    assert store.load().remote_token == ""


def test_store_must_be_outside_data_dir(tmp_path):
    with pytest.raises(RuntimeError, match="outside DATA_DIR"):
        AdminStore(tmp_path / "data" / "settings.json", tmp_path / "data")


@pytest.mark.parametrize("patch, message", [
    ({"segmentation_provider": "gpu"}, "choose one of"),
    ({"remote_url": "home-pc:8100"}, "worker address"),
    ({"remote_url": "http://home-pc:8100/segment"}, "nothing after"),
    ({"segmentation_provider": "remote"}, "address and its token"),
    ({"workbook_path": "/x/cases.xls"}, "end in .xlsx"),
    ({"local_device": "cuda"}, "cpu or mps"),
])
def test_store_rejects_bad_settings(tmp_path, patch, message):
    store = AdminStore(tmp_path / "s.json", tmp_path / "data")
    with pytest.raises(LibraryError, match=message):
        store.update(patch)


# ---------------------------------------------------------------------------
# Through the API


def test_admin_page_hides_secrets_and_switches_provider(admin_client, worker_url):
    client, _ = admin_client
    view = client.get("/api/admin").json()
    assert view["settings"]["segmentation_provider"] == "none"
    assert view["paths"]["settings_file"].endswith("private/stoneplanner-settings.json")
    assert client.get("/api/session").json()["segmentation_configured"] is False

    r = client.put("/api/admin", json={"segmentation_provider": "remote",
                                       "remote_url": worker_url, "remote_token": TOKEN})
    assert r.status_code == 200, r.text
    assert TOKEN not in r.text and r.json()["settings"]["has_remote_token"]
    assert client.get("/api/session").json()["segmentation_configured"] is True

    check = client.post("/api/admin/segmentation/check").json()
    assert check["ok"] is False and "no GPU" in check["detail"]   # the CPU stub worker

    assert client.post("/api/admin/segmentation/test").status_code == 202
    for _ in range(200):
        state = client.get("/api/admin/segmentation/test").json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done", state
    assert state["source"] == "synthetic phantom" and "on the grid of the CT" in state["detail"]
    assert state["worker_seconds"]


def test_case_segments_through_the_home_pc_worker(admin_client, worker_url):
    client, _ = admin_client
    client.put("/api/admin", json={"segmentation_provider": "remote", "remote_url": worker_url,
                                   "remote_token": TOKEN})
    case = client.post("/api/cases", files={"ct": ("case_w.nii.gz", _phantom_ct())}).json()
    client.post(f"/api/cases/{case['id']}/segment")
    for _ in range(300):
        job = client.get(f"/api/cases/{case['id']}").json()["jobs"].get("segment", {})
        if job.get("state") in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["state"] == "done", job
    assert client.get(f"/api/cases/{case['id']}").json()["has"]["organs"]


def test_wrong_worker_token_is_reported(admin_client, worker_url):
    client, _ = admin_client
    client.put("/api/admin", json={"segmentation_provider": "remote", "remote_url": worker_url,
                                   "remote_token": "x" * 40})
    check = client.post("/api/admin/segmentation/check").json()
    assert check["ok"] is False and "401" in check["detail"]


def test_local_provider_without_totalsegmentator(admin_client, monkeypatch):
    client, _ = admin_client
    import builtins

    real_import = builtins.__import__

    def no_totalseg(name, *args, **kwargs):
        if name == "totalsegmentator":
            raise ImportError("not here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_totalseg)
    client.put("/api/admin", json={"segmentation_provider": "local"})
    check = client.post("/api/admin/segmentation/check").json()
    assert check["ok"] is False and "segment extra" in check["detail"]


def test_modal_needs_a_token(admin_client, monkeypatch):
    client, _ = admin_client
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    client.put("/api/admin", json={"segmentation_provider": "modal"})
    check = client.post("/api/admin/segmentation/check").json()
    assert check["ok"] is False and "token" in check["detail"]
    assert client.get("/api/session").json()["segmentation_configured"] is False
    client.put("/api/admin", json={"modal_token_id": "ak-1", "modal_token_secret": "as-1"})
    assert client.get("/api/session").json()["segmentation_configured"] is True


def test_linked_workbook(admin_client):
    client, tmp = admin_client
    assert client.get("/api/ingest").json()["workbook_linked"] is False
    r = client.get("/api/ingest/workbook/linked")
    assert r.status_code == 400 and "Admin page" in r.json()["detail"]

    book = tmp / "OneDrive" / "cases.xlsx"
    book.parent.mkdir()
    client.put("/api/admin", json={"workbook_path": str(book)})
    assert client.get("/api/ingest").json()["workbook_linked"] is True
    r = client.get("/api/ingest/workbook/linked")
    assert r.status_code == 400 and "no file at" in r.json()["detail"]

    book.write_bytes(fill({"case_001": "M1"}, [{"case_id": "case_001", **COMPLETE}]))
    before = book.read_bytes()
    out = client.get("/api/ingest/workbook/linked").json()
    assert out["path"] == str(book)
    assert [(r["case_id"], r["status"]) for r in out["rows"]] == [("case_001", "ready")]
    assert book.read_bytes() == before                      # read, never written


def test_admin_routes_need_the_local_install(tmp_path):
    settings = Settings(secret_key="k" * 32, app_password=PASSWORD, cookie_secure=False,
                        work_dir=tmp_path / "work")
    with TestClient(create_app(settings)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        assert c.get("/api/admin").status_code == 404
        assert c.put("/api/admin", json={}).status_code == 404


def test_admin_routes_need_login(admin_client):
    client, _ = admin_client
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/admin").status_code == 401


# ---------------------------------------------------------------------------
# The worker on its own


def test_worker_refuses_a_short_token():
    with pytest.raises(RuntimeError, match="WORKER_TOKEN"):
        create_worker(token="short", run=_phantom_organs)


def test_worker_checks_token_and_input():
    c = TestClient(create_worker(token=TOKEN, device="cpu", run=_phantom_organs))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/health").status_code == 401
    assert c.get("/health", headers={"Authorization": "Bearer nope"}).status_code == 401
    health = c.get("/health", headers=auth).json()
    assert health["ok"] and health["busy"] is False
    assert c.post("/segment", content=b"plain bytes", headers=auth).status_code == 400
    assert c.post("/segment", content=gzip.compress(b"x" * 400), headers=auth).status_code == 400
    r = c.post("/segment", content=_phantom_ct(), headers=auth)
    assert r.status_code == 200 and float(r.headers["x-seconds"]) >= 0
    out = nib.Nifti1Image.from_bytes(gzip.decompress(r.content))
    assert out.shape == phantom.make_phantom()[0].shape

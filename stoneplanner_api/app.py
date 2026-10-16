"""FastAPI app: login, cases, jobs, files, and the built front end.

Run locally:
    APP_PASSWORD=dev COOKIE_SECURE=false uvicorn stoneplanner_api.app:app --reload

Routes (all under /api need a session except health and login):
    GET    /api/health
    POST   /api/login                  {"password": "..."}
    POST   /api/logout
    GET    /api/session
    GET    /api/params                 editable PlanParams fields and defaults
    GET    /api/labels                 organ groups of the viewer's compact organ map
    POST   /api/cases                  multipart: ct (+ organs, stones) or bundle
    GET    /api/cases/{id}             summary, stones, plan, job states
    DELETE /api/cases/{id}
    POST   /api/cases/{id}/segment     start GPU segmentation
    POST   /api/cases/{id}/plan        {"side": "auto", "params": {...}}
    GET    /api/cases/{id}/files/{key} ct, organs, organs_display, stones, overlay,
                                       tracts_json, tracts_csv, stones_csv
    GET    /api/cases/{id}/bundle      saved-case zip for the user's disk

Local ingest page, only when DATA_DIR and KEY_FILE are set (never on Render):
    GET    /api/ingest                 enabled?, next case ID, ingested cases
    POST   /api/ingest/scan            multipart "files": a DICOM folder; lists series
    POST   /api/ingest/cases           {mrn, preop, postop?, clinical, case_id?}
    GET    /api/ingest/manual.csv      the validation table
    POST   /api/ingest/cases/{case_id}/open   open a library case in the planner
    POST   /api/cases/{id}/save-to-library    planner results into the case folder
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import threading
import time
from pathlib import Path

import nibabel as nib
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from stoneplanner_api.admin import AdminStore, build_segmenter
from stoneplanner_api.admin import check as check_segmentation
from stoneplanner_api.auth import COOKIE_NAME, Auth
from stoneplanner_api.cases import FILES, Case, CaseError, CaseStore, case_label, load_nifti
from stoneplanner_api.config import Settings
from stoneplanner_api.jobs import (
    DISPLAY_TOP_N,
    JobBusy,
    Jobs,
    display_organs,
    ensure_stone_table,
    param_fields,
    plan_params,
    run_plan,
    run_segmentation,
    write_overlay,
)
from stoneplanner_api.library import STONE_LOCATIONS, Library, LibraryError
from stoneplanner_api.overlay import DISPLAY_GROUPS
from stoneplanner_api.segmentation import Segmenter, modal_segmenter

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_WORKBOOK_BYTES = 20 * 1024 * 1024
SNAPSHOT_RETRY_S = 2.0


def read_snapshot(path: Path) -> bytes:
    """Bytes of a workbook that a sync client (OneDrive) may be rewriting.

    Reads twice and keeps a read only if the size held still; one retry after a
    pause. The file is opened read-only and never written.
    """
    for attempt in range(2):
        try:
            before = path.stat().st_size
            if before > MAX_WORKBOOK_BYTES:
                raise LibraryError("the linked workbook is larger than 20 MB")
            data = path.read_bytes()
            if before and len(data) == before == path.stat().st_size:
                return data
        except FileNotFoundError as exc:
            raise LibraryError(f"no file at {path}. Check the path on the Admin page.") from exc
        except PermissionError as exc:
            raise LibraryError(f"cannot read {path}: permission denied") from exc
        except OSError as exc:
            if attempt:
                raise LibraryError(
                    f"could not read {path} ({exc}). If it is in OneDrive, set the folder to "
                    "Always keep on this device.") from exc
        time.sleep(SNAPSHOT_RETRY_S)
    raise LibraryError(f"{path} kept changing while being read (OneDrive may be syncing); "
                       "try again in a moment")

SERVED = {"ct", "organs", "organs_display", "stones", "overlay", "tracts_json", "tracts_csv",
          "stones_csv"}
DOWNLOAD_NAMES = {"tracts_json": "{}_tracts.json", "tracts_csv": "{}_tracts.csv",
                  "stones_csv": "{}_stones.csv"}
CLEANUP_INTERVAL_S = 300

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; "
        "connect-src 'self' blob: data:; frame-ancestors 'none'; base-uri 'none'"
    ),
}


def create_app(settings: Settings | None = None, segmenter: Segmenter | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    auth = Auth(settings.secret_key, settings.app_password, settings.session_hours)
    store = CaseStore(settings.work_dir, settings.case_ttl_minutes)
    jobs = Jobs(settings.plan_workers)
    env_segmenter = None
    if segmenter is None and settings.modal_configured:
        env_segmenter = modal_segmenter(settings.modal_app_name, settings.modal_function_name)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    library = admin = None
    if settings.data_dir is not None and settings.key_file is not None:
        library = Library(settings.data_dir, settings.key_file)
        admin = AdminStore(settings.settings_file or
                           Path(settings.key_file).parent / "stoneplanner-settings.json",
                           settings.data_dir)

    def current_segmenter() -> Segmenter | None:
        """A segmenter passed in (tests) wins; then the admin page's choice on a local
        install; then the environment (hosted)."""
        if segmenter is not None:
            return segmenter
        if admin is not None:
            return build_segmenter(admin.load())
        return env_segmenter

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.reset()

        async def sweep():
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL_S)
                store.cleanup()

        task = asyncio.create_task(sweep())
        yield
        task.cancel()

    app = FastAPI(title="Stone planner", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.store, app.state.jobs, app.state.settings = store, jobs, settings

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(CaseError)
    async def case_error(_request: Request, exc: CaseError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(LibraryError)
    async def library_error(_request: Request, exc: LibraryError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def session(request: Request) -> str:
        return auth.require(request)

    def owned(case_token: str, sid: str = Depends(session)) -> Case:
        case = store.get(case_token, sid)
        if case is None:
            raise HTTPException(404, "No such case (it may have expired; open your saved copy).")
        return case

    async def read_upload(upload: UploadFile, what: str) -> bytes:
        chunks, size = [], 0
        while chunk := await upload.read(1 << 20):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, f"{what} is larger than {settings.max_upload_mb} MB")
            chunks.append(chunk)
        return b"".join(chunks)

    def summary(case: Case) -> dict:
        img = nib.load(str(case.file("ct")))
        out = {
            "id": case.token,
            "case_id": case.case_id,
            "shape": [int(n) for n in img.shape],
            "spacing_mm": [round(float(z), 3) for z in img.header.get_zooms()[:3]],
            "has": {k: case.has(k) for k in ("organs", "stones", "tracts_json", "overlay")},
            "stones": [],
            "plan": None,
            "jobs": jobs.status(case.token),
            "source_case": case.meta.get("source_case"),
        }
        if case.has("stones_csv"):
            import csv

            with case.file("stones_csv").open(newline="") as f:
                out["stones"] = list(csv.DictReader(f))
        if case.has("tracts_json"):
            plan = json.loads(case.file("tracts_json").read_text())
            out["plan"] = {"side": plan["side"], "params": plan["params"],
                           "tracts": plan["tracts"][:DISPLAY_TOP_N],
                           "best_by_group": plan.get("best_by_group", {})}
        return out

    # -- open routes -----------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "auth_configured": auth.configured,
                "segmentation_configured": current_segmenter() is not None}

    @app.post("/api/login")
    async def login(request: Request, response: Response):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        token = auth.check_password(request, str(body.get("password", "")))
        response.set_cookie(COOKIE_NAME, token, max_age=auth.max_age_s, httponly=True,
                            secure=settings.cookie_secure, samesite="strict", path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/session")
    def get_session(request: Request):
        return {"authenticated": auth.session_id(request) is not None,
                "segmentation_configured": current_segmenter() is not None}

    # -- cases -----------------------------------------------------------------

    @app.get("/api/params")
    def params(_sid: str = Depends(session)):
        return {"fields": param_fields()}

    @app.get("/api/labels")
    def labels(_sid: str = Depends(session)):
        return {"display_groups": list(DISPLAY_GROUPS)}

    @app.post("/api/cases")
    async def create_case(sid: str = Depends(session),
                          ct: UploadFile | None = File(None),
                          organs: UploadFile | None = File(None),
                          stones: UploadFile | None = File(None),
                          bundle: UploadFile | None = File(None)):
        store.cleanup()
        if bundle is not None:
            if ct is not None:
                raise CaseError("send either a saved case or a CT, not both")
            case = store.open_bundle(sid, await read_upload(bundle, "saved case"), max_bytes)
            if case.has("tracts_json"):
                write_overlay(case)
        elif ct is not None:
            images = {"ct": load_nifti(await read_upload(ct, "CT"), "CT")}
            for key, upload in (("organs", organs), ("stones", stones)):
                if upload is not None:
                    images[key] = load_nifti(await read_upload(upload, key), key)
            if ("organs" in images) != ("stones" in images):
                raise CaseError("upload organ and stone maps together, or neither")
            case = store.create(sid, case_label(ct.filename or "case"), images["ct"],
                                images.get("organs"), images.get("stones"))
            ensure_stone_table(case)
        else:
            raise CaseError("upload a CT (.nii.gz) or a saved case (.zip)")
        return summary(case)

    @app.get("/api/cases/{case_token}")
    def get_case(case: Case = Depends(owned)):
        return summary(case)

    @app.delete("/api/cases/{case_token}")
    def delete_case(case: Case = Depends(owned)):
        jobs.forget(case.token)
        store.delete(case)
        return {"ok": True}

    @app.post("/api/cases/{case_token}/segment", status_code=202)
    def segment_case(case: Case = Depends(owned)):
        seg = current_segmenter()
        if seg is None:
            how = ("Choose where it runs on the Admin page, or segment elsewhere and upload "
                   "the organ and stone maps." if admin is not None else
                   "Segment locally and upload the organ and stone maps.")
            raise HTTPException(503, f"GPU segmentation is not configured on this server. {how}")
        try:
            jobs.submit(case.token, "segment", run_segmentation, store, case, seg)
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        return summary(case)

    @app.post("/api/cases/{case_token}/plan", status_code=202)
    async def plan_case(request: Request, case: Case = Depends(owned)):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        side = body.get("side", "auto")
        if side not in ("auto", "left", "right"):
            raise CaseError("side must be auto, left or right")
        params = plan_params(body.get("params") or {})
        if jobs.status(case.token).get("segment", {}).get("state") == "running":
            raise HTTPException(409, "segmentation is still running")
        try:
            jobs.submit(case.token, "plan", run_plan, store, case, side, params)
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        return summary(case)

    @app.get("/api/cases/{case_token}/files/{key}")
    def get_file(key: str, case: Case = Depends(owned)):
        if key == "organs_display":
            display_organs(case)
        if key not in SERVED or not case.has(key):
            raise HTTPException(404, "No such file for this case.")
        name = DOWNLOAD_NAMES.get(key, "{}_" + FILES[key]).format(case.case_id)
        disposition = "attachment" if key in DOWNLOAD_NAMES else "inline"
        return FileResponse(case.file(key), filename=name, content_disposition_type=disposition)

    @app.get("/api/cases/{case_token}/bundle")
    def get_bundle(case: Case = Depends(owned)):
        data = store.bundle(case)
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{case.case_id}_case.zip"'})

    # -- local ingest page -------------------------------------------------------

    def need_library() -> Library:
        if library is None:
            raise HTTPException(404, "The ingest page runs only on a local install "
                                     "(DATA_DIR and KEY_FILE set).")
        return library

    @app.get("/api/ingest")
    def ingest_status(_sid: str = Depends(session)):
        if library is None:
            return {"enabled": False}
        return {"enabled": True, "next_case_id": library.next_case_id(),
                "cases": library.list_cases(), "stone_locations": list(STONE_LOCATIONS),
                "workbook_linked": bool(admin and admin.load().workbook_path)}

    @app.post("/api/ingest/scan")
    async def ingest_scan(request: Request, _sid: str = Depends(session)):
        lib = need_library()
        form = await request.form(max_files=50_000, max_fields=10)
        token, staging = lib.new_staging()
        limit, total, n = settings.max_ingest_mb * 1024 * 1024, 0, 0
        try:
            for upload in form.getlist("files"):
                if not hasattr(upload, "read"):
                    continue
                data = await upload.read()
                total += len(data)
                if total > limit:
                    raise HTTPException(413, f"upload is larger than {settings.max_ingest_mb} MB")
                # File names from the browser are ignored: they can carry MRNs
                # and path tricks. Only the bytes are kept, under a counter.
                (staging / f"{n:06d}.dcm").write_bytes(data)
                n += 1
            from stoneplanner import ingest as ingest_mod

            series = ingest_mod.list_series(staging)
        except ValueError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise LibraryError(f"no usable DICOM in that folder ({exc})") from exc
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            await form.close()
        return {"staging": token, "n_files": n, "series": series,
                "recommended_ct": ingest_mod.recommend_series(series, "ct"),
                "recommended_radiograph": ingest_mod.recommend_series(series, "radiograph")}

    @app.post("/api/ingest/cases")
    def ingest_case(body: dict, _sid: str = Depends(session)):
        lib = need_library()
        result = lib.ingest(str(body.get("mrn", "")), body.get("preop") or {},
                            body.get("clinical") or {}, body.get("postop") or None,
                            body.get("case_id") or None)
        return {**result, "cases": lib.list_cases(), "next_case_id": lib.next_case_id()}

    @app.get("/api/ingest/workbook.xlsx")
    def ingest_workbook_template(_sid: str = Depends(session)):
        need_library()
        from stoneplanner_api import workbook

        return Response(workbook.build_template(), media_type=XLSX, headers={
            "Content-Disposition": 'attachment; filename="stone-planner-cases.xlsx"'})

    @app.post("/api/ingest/workbook")
    async def ingest_workbook(file: UploadFile = File(...), _sid: str = Depends(session)):
        """Read a filled case workbook. Nothing is kept: the rows go back to the page."""
        lib = need_library()
        from stoneplanner_api import workbook

        data = await file.read(MAX_WORKBOOK_BYTES + 1)
        if len(data) > MAX_WORKBOOK_BYTES:
            raise HTTPException(413, "workbook is larger than 20 MB")
        return with_status(lib, workbook.parse_workbook(data))

    @app.get("/api/ingest/workbook/linked")
    def ingest_workbook_linked(_sid: str = Depends(session)):
        """Read the workbook at the path set on the Admin page (the OneDrive copy)."""
        lib = need_library()
        from stoneplanner_api import workbook

        path = admin.load().workbook_path
        if not path:
            raise LibraryError("no workbook is linked; set its path on the Admin page")
        parsed = workbook.parse_workbook(read_snapshot(Path(path)))
        return {**with_status(lib, parsed), "path": path}

    def with_status(lib: Library, parsed: dict) -> dict:
        existing = set(lib.case_ids())
        for row in parsed["rows"]:
            conflict = row["mrn"] and lib.key_conflict(row["case_id"], row["mrn"])
            if conflict:
                row["errors"].insert(0, conflict)
                row["status"] = "conflict"
            elif row["case_id"] in existing:
                row["status"] = "ingested"
            else:
                row["status"] = "incomplete" if row["errors"] else "ready"
        return parsed

    # -- admin page (local install only) ---------------------------------------

    test_state: dict = {"state": "idle"}
    test_lock = threading.Lock()

    def need_admin() -> AdminStore:
        need_library()
        return admin

    def admin_view() -> dict:
        s = admin.load()
        return {"settings": s.public(),
                "paths": {"data_dir": str(library.data_dir), "key_file": str(library.key_file),
                          "settings_file": str(admin.path)},
                "segmentation_configured": current_segmenter() is not None,
                "segmenter_overridden": segmenter is not None}

    @app.get("/api/admin")
    def admin_get(_sid: str = Depends(session)):
        need_admin()
        return admin_view()

    @app.put("/api/admin")
    def admin_put(body: dict, _sid: str = Depends(session)):
        need_admin().update(body)
        return admin_view()

    @app.post("/api/admin/segmentation/check")
    def admin_check(_sid: str = Depends(session)):
        if segmenter is not None:
            return {"ok": True, "detail": "A test segmenter is in use (development only)."}
        return check_segmentation(need_admin().load())

    def test_ct() -> tuple[bytes, str]:
        """The newest ingested case's de-identified CT, or the synthetic phantom."""
        for case_id in reversed(library.case_ids()):
            path = library.case_dir(case_id) / f"{case_id}.nii.gz"
            if path.exists():
                return path.read_bytes(), case_id
        import gzip

        from stoneplanner import phantom

        ct, _, _ = phantom.make_phantom()
        img = nib.Nifti1Image(ct.astype("int16"), phantom.AFFINE)
        return gzip.compress(img.to_bytes()), "synthetic phantom"

    def run_test(seg: Segmenter) -> None:
        start = time.monotonic()
        try:
            data, source = test_ct()
            test_state.update(source=source)
            out = load_nifti(seg(data), "segmentation result")
            ct = load_nifti(data, "test CT")
            if out.shape != ct.shape:
                raise CaseError(f"result shape {out.shape} does not match the CT {ct.shape}")
            import numpy as np

            labels = np.unique(np.asanyarray(out.dataobj))
            n = int((labels > 0).sum())
            test_state.update(state="done", seconds=round(time.monotonic() - start, 1),
                              detail=f"{n} labels in the result, on the grid of the CT.",
                              **{k: v for k, v in getattr(seg, "last_stats", {}).items() if v})
        except Exception as exc:  # noqa: BLE001  the page shows what went wrong
            test_state.update(state="error", seconds=round(time.monotonic() - start, 1),
                              detail=str(exc)[:500])

    @app.post("/api/admin/segmentation/test", status_code=202)
    def admin_test_start(_sid: str = Depends(session)):
        need_admin()
        seg = current_segmenter()
        if seg is None:
            raise HTTPException(409, "segmentation is not set up; save a provider first")
        with test_lock:
            if test_state.get("state") == "running":
                raise HTTPException(409, "a test is already running")
            test_state.clear()
            test_state.update(state="running", started=time.time(),
                              provider=admin.load().segmentation_provider)
        threading.Thread(target=run_test, args=(seg,), daemon=True).start()
        return test_state

    @app.get("/api/admin/segmentation/test")
    def admin_test_status(_sid: str = Depends(session)):
        need_admin()
        return test_state

    @app.get("/api/ingest/manual.csv")
    def ingest_manual(_sid: str = Depends(session)):
        lib = need_library()
        return FileResponse(lib.write_manual_csv(), filename="manual.csv",
                            content_disposition_type="attachment")

    @app.post("/api/ingest/cases/{case_id}/open")
    def ingest_open(case_id: str, sid: str = Depends(session)):
        lib = need_library()
        folder = lib.case_dir(case_id)
        ct_path = folder / f"{case_id}.nii.gz"
        if not ct_path.exists():
            raise HTTPException(404, "No such case in the library.")
        images = {"ct": nib.load(str(ct_path))}
        for key in ("organs", "stones"):
            path = lib.result_path(case_id, f"{key}.nii.gz")
            if path.exists():
                images[key] = nib.load(str(path))
        if ("organs" in images) != ("stones" in images):
            images.pop("organs", None)
            images.pop("stones", None)
        case = store.create(sid, case_id, images["ct"], images.get("organs"),
                            images.get("stones"), extra_meta={"source_case": case_id})
        ensure_stone_table(case)
        tracts = lib.result_path(case_id, "tracts.json")
        if tracts.exists() and case.has("stones"):
            shutil.copyfile(tracts, case.file("tracts_json"))
            write_overlay(case)
        return summary(case)

    @app.post("/api/cases/{case_token}/save-to-library")
    def save_to_library(case: Case = Depends(owned)):
        lib = need_library()
        source = case.meta.get("source_case")
        if not source:
            raise HTTPException(400, "This case was not opened from the library.")
        written = []
        for key, suffix in (("organs", "organs.nii.gz"), ("stones", "stones.nii.gz"),
                            ("stones_csv", "stones.csv"), ("tracts_json", "tracts.json"),
                            ("tracts_csv", "tracts.csv")):
            if case.has(key):
                target = lib.result_path(source, suffix)
                shutil.copyfile(case.file(key), target)
                written.append(target.name)
        lib.write_manual_csv()
        return {"written": written}

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
    def api_not_found(rest: str):
        raise HTTPException(404, "Not found.")

    # -- front end ---------------------------------------------------------------

    dist = settings.frontend_dist
    if dist is not None and (dist / "index.html").exists():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and Path(dist).resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


def __getattr__(name: str):
    # `uvicorn stoneplanner_api.app:app` builds the app from the environment on
    # first access, so importing this module (e.g. in tests) has no side effects.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)

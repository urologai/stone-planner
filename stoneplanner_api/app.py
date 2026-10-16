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
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import nibabel as nib
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
from stoneplanner_api.overlay import DISPLAY_GROUPS
from stoneplanner_api.segmentation import Segmenter, modal_segmenter

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
    if segmenter is None and settings.modal_configured:
        segmenter = modal_segmenter(settings.modal_app_name, settings.modal_function_name)
    max_bytes = settings.max_upload_mb * 1024 * 1024

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
                "segmentation_configured": segmenter is not None}

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
                "segmentation_configured": segmenter is not None}

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
        if segmenter is None:
            raise HTTPException(503, "GPU segmentation is not configured on this server. "
                                     "Segment locally and upload the organ and stone maps.")
        try:
            jobs.submit(case.token, "segment", run_segmentation, store, case, segmenter)
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

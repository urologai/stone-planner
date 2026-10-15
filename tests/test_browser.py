"""End-to-end browser test: the built front end against the real API server.

Starts uvicorn with the built frontend (frontend/dist) and drives it with
headless Chromium through Playwright: log in, upload the synthetic phantom,
plan, check the ranked tracts and that the viewer drew something, save the
case, close it, and reopen the saved copy without re-planning. Also fails on
any page error or Content-Security-Policy violation.

Skipped unless frontend/dist exists (npm run build) and Playwright and a
Chromium binary are available. Set E2E_BASE_URL to run against a server that
is already up (e.g. the Docker image) instead of starting one.
"""

import gzip
import io
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from stoneplanner import phantom

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
PASSWORD = "e2e-password"
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_BASE_URL") and not (DIST / "index.html").exists(),
    reason="frontend not built (cd frontend && npm run build)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    if os.environ.get("E2E_BASE_URL"):
        yield os.environ["E2E_BASE_URL"].rstrip("/")
        return
    port = _free_port()
    env = {**os.environ, "APP_PASSWORD": PASSWORD, "COOKIE_SECURE": "false",
           "FRONTEND_DIST": str(DIST), "SECRET_KEY": "e2e" * 12,
           "WORK_DIR": str(tmp_path_factory.mktemp("work"))}
    env.pop("MODAL_TOKEN_ID", None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "stoneplanner_api.app:app",
                             "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def case_files(tmp_path_factory):
    ct, organs, stones = phantom.make_phantom()
    ct[stones > 0] = 1000
    folder = tmp_path_factory.mktemp("upload")
    paths = {}
    for name, arr in (("ct", ct.astype(np.int16)), ("organs", organs.astype(np.uint8)),
                      ("stones", stones.astype(np.int16))):
        path = folder / ("case_e2e.nii.gz" if name == "ct" else f"{name}.nii.gz")
        path.write_bytes(gzip.compress(nib.Nifti1Image(arr, phantom.AFFINE).to_bytes()))
        paths[name] = path
    return paths


@pytest.fixture
def page(base_url):
    if not Path(CHROMIUM).exists():
        pytest.skip(f"no Chromium at {CHROMIUM}")
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROMIUM,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                  "--ignore-gpu-blocklist"])
        context = browser.new_context(accept_downloads=True, viewport={"width": 1400,
                                                                       "height": 1000})
        pg = context.new_page()
        pg.problems = []
        pg.on("pageerror", lambda exc: pg.problems.append(f"page error: {exc}"))
        pg.on("console", lambda msg: pg.problems.append(f"console: {msg.text}")
              if msg.type == "error" else None)
        pg.goto(base_url)
        yield pg
        browser.close()


def _overlay_colours(pg) -> dict[str, float]:
    """Pixel counts of each overlay colour, and the share of label-1 tint.

    green: rank 1 tract and target; red: stone; kidney: organ overlay; tint:
    pixels where empty space has picked up a label colour (a NiiVue label
    table off by one turns every zero voxel the colour of label 1).
    """
    from PIL import Image

    png = pg.get_by_test_id("viewer-canvas").screenshot()
    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=int)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return {
        "green": int(((g > 150) & (r < 90) & (b < 90)).sum()),
        "red": int(((r > 180) & (g < 90) & (b < 90)).sum()),
        "kidney": int(((r - b > 40) & (r > 100) & (g > 60) & (g < r)).sum()),
        "tint": float(((r - g > 12) & (b - g > 12)).mean()),
    }


def test_plan_save_and_reopen(page, case_files, tmp_path):
    expect = playwright_api.expect
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()

    page.get_by_test_id("ct-input").set_input_files(case_files["ct"])
    page.get_by_test_id("organs-input").set_input_files(case_files["organs"])
    page.get_by_test_id("stones-input").set_input_files(case_files["stones"])
    page.get_by_role("button", name="Upload").click()
    expect(page.get_by_text("case_e2e")).to_be_visible()
    expect(page.get_by_text("1 stone (")).to_be_visible()

    page.get_by_text("Advanced").click()
    page.get_by_label("Candidate entry points").fill("400")
    page.get_by_role("button", name="Plan tracts").click()
    expect(page.get_by_test_id("plan-status")).to_contain_text("Plan done", timeout=120_000)
    first_row = page.get_by_test_id("tracts-table").locator("tbody tr").first
    expect(first_row).to_contain_text("100%")

    expect(page.get_by_test_id("viewer-status")).to_have_count(0, timeout=30_000)
    page.wait_for_timeout(500)        # one more frame after the last volume lands
    box = page.get_by_test_id("viewer-canvas").bounding_box()
    assert box["height"] >= 300, box
    colours = _overlay_colours(page)
    assert colours["green"] >= 20, colours        # rank 1 target and tract drawn
    assert colours["red"] >= 5, colours           # stone drawn
    assert colours["kidney"] >= 500, colours      # organ labels coloured, not grey
    assert colours["tint"] < 0.01, colours        # empty space stays uncoloured
    first_row.click()                 # moves the crosshair; must not throw

    with page.expect_download() as download:
        page.get_by_role("link", name="Save case").click()
    saved = tmp_path / "saved.zip"
    download.value.save_as(saved)
    assert {"ct.nii.gz", "tracts.json"} <= set(zipfile.ZipFile(saved).namelist())

    page.get_by_role("button", name="Close case").click()
    page.get_by_test_id("bundle-input").set_input_files(saved)
    expect(page.get_by_test_id("tracts-table").locator("tbody tr").first).to_contain_text(
        "100%")

    csp = [p for p in page.problems if "Content Security Policy" in p]
    assert not csp, csp
    errors = [p for p in page.problems if p.startswith("page error")]
    assert not errors, errors

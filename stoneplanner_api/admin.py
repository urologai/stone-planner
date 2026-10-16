"""Admin settings for a local install: where segmentation runs, and the workbook.

Kept in one JSON file (SETTINGS_FILE, default next to KEY_FILE), outside
DATA_DIR and readable only by its owner, because it holds the Modal token and
the GPU worker token. The admin page reads public() and never gets a secret
back: it sees only whether each one is set. Saving with a secret left blank
keeps the stored one.

The admin page exists only on a local install (DATA_DIR and KEY_FILE set),
like the ingest page. A hosted server keeps taking its settings from the
environment.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from stoneplanner_api import segmentation
from stoneplanner_api.library import LibraryError

SECRETS = ("modal_token_id", "modal_token_secret", "remote_token")
LOCAL_DEVICES = ("cpu", "mps")
URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(:\d+)?/?$")


@dataclass
class AdminSettings:
    segmentation_provider: str = "none"
    modal_app_name: str = "stone-planner-segment"
    modal_function_name: str = "segment_organs"
    modal_token_id: str = ""
    modal_token_secret: str = ""
    remote_url: str = ""
    remote_token: str = ""
    local_device: str = "cpu"
    workbook_path: str = ""

    def public(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if k not in SECRETS}
        out.update({f"has_{k}": bool(getattr(self, k)) for k in SECRETS})
        return out


class AdminStore:
    def __init__(self, path: Path, data_dir: Path):
        self.path = Path(path).resolve()
        data_dir = Path(data_dir).resolve()
        if self.path == data_dir or data_dir in self.path.parents:
            raise RuntimeError("SETTINGS_FILE must be outside DATA_DIR: it holds tokens")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> AdminSettings:
        if not self.path.exists():
            return AdminSettings()
        raw = json.loads(self.path.read_text())
        known = {f.name for f in fields(AdminSettings)}
        return AdminSettings(**{k: v for k, v in raw.items() if k in known})

    def save(self, s: AdminSettings) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(s), f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def update(self, patch: dict) -> AdminSettings:
        """Apply a change from the admin page. Blank secrets keep the stored value;
        clear_<name>: true removes one."""
        s = self.load()
        for f in fields(AdminSettings):
            if f.name not in patch:
                continue
            value = patch[f.name]
            if not isinstance(value, str):
                raise LibraryError(f"{f.name}: expected text")
            value = value.strip()
            if f.name in SECRETS and value == "":
                continue
            setattr(s, f.name, value)
        for name in SECRETS:
            if patch.get(f"clear_{name}") is True:
                setattr(s, name, "")
        validate(s)
        self.save(s)
        return s


def validate(s: AdminSettings) -> None:
    if s.segmentation_provider not in segmentation.PROVIDERS:
        raise LibraryError(f"segmentation: choose one of {', '.join(segmentation.PROVIDERS)}")
    if s.local_device not in LOCAL_DEVICES:
        raise LibraryError("local device: cpu or mps")
    if s.remote_url and not URL_RE.match(s.remote_url):
        raise LibraryError("GPU worker address: http://name-or-address:port, nothing after "
                           "the port (for example http://home-pc:8100)")
    if s.workbook_path and not s.workbook_path.lower().endswith(".xlsx"):
        raise LibraryError("workbook: the path must end in .xlsx")
    if s.segmentation_provider == "remote" and not (s.remote_url and s.remote_token):
        raise LibraryError("home PC GPU: enter the worker's address and its token")


def build_segmenter(s: AdminSettings) -> segmentation.Segmenter | None:
    """The segmenter for the saved choice, or None for "none" or a choice not set up."""
    p = s.segmentation_provider
    if p == "modal":
        saved = s.modal_token_id and s.modal_token_secret
        env = os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")
        if not (saved or env):
            return None
        return segmentation.modal_segmenter(s.modal_app_name, s.modal_function_name,
                                            s.modal_token_id or None,
                                            s.modal_token_secret or None)
    if p == "remote" and s.remote_url and s.remote_token:
        return segmentation.remote_segmenter(s.remote_url, s.remote_token)
    if p == "local":
        return segmentation.local_segmenter(s.local_device)
    return None


def check(s: AdminSettings) -> dict:
    """Is the saved choice ready? Fast: no segmentation is run."""
    p = s.segmentation_provider
    try:
        if p == "none":
            return {"ok": False, "detail": "Segmentation is off. Organ and stone maps have to "
                                           "be uploaded."}
        if p == "modal":
            if not (s.modal_token_id and s.modal_token_secret) and not (
                    os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")):
                return {"ok": False, "detail": "Enter the Modal token ID and secret."}
            return {"ok": True, "detail": segmentation.modal_check(
                s.modal_app_name, s.modal_function_name, s.modal_token_id or None,
                s.modal_token_secret or None)}
        if p == "remote":
            health = segmentation.remote_check(s.remote_url, s.remote_token)
            gpu = health.get("gpu") or "no GPU"
            mem = health.get("gpu_memory_gb")
            return {"ok": bool(health.get("gpu")), "health": health,
                    "detail": f"GPU worker answered: {gpu}"
                              + (f", {mem:.0f} GB" if mem else "")
                              + ("" if health.get("gpu") else " (it would run on its CPU)")}
        return {"ok": True, "detail": segmentation.local_check(s.local_device)}
    except segmentation.SegmentationUnavailable as exc:
        return {"ok": False, "detail": str(exc)}

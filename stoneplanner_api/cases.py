"""Scratch storage for cases being worked on, and the save/open bundle.

A case is a folder WORK_DIR/<token>/ holding fixed file names (FILES). The
token is random and unguessable; case.json records the owner session and the
user-facing case_id. Folders idle for longer than the TTL are deleted, and
the whole WORK_DIR is wiped at start-up, so nothing outlives its use.

Every NIfTI accepted (upload or bundle) is checked and rewritten with a
fresh header: 3D only, integer labels for organ and stone maps, and the
same grid as the CT. DICOM, zip-in-place-of-NIfTI and other formats are
refused, because only de-identified NIfTI may reach the server.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

FILES = {
    "ct": "ct.nii.gz",
    "organs": "organs.nii.gz",
    "stones": "stones.nii.gz",
    "stones_csv": "stones.csv",
    "tracts_json": "tracts.json",
    "tracts_csv": "tracts.csv",
    "overlay": "tracts_overlay.nii.gz",
    "organs_display": "organs_display.nii.gz",
}
META = "case.json"
BUNDLE_FILES = ("ct", "organs", "stones", "stones_csv", "tracts_json", "tracts_csv")
PLAN_FILES = ("tracts_json", "tracts_csv", "overlay")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
GZIP_MAGIC = b"\x1f\x8b"


class CaseError(ValueError):
    """A problem with what the user sent; reported back as a 400."""


def case_label(filename: str) -> str:
    """User-facing case ID from an upload's file name, letters, digits, _ and - only."""
    name = Path(filename or "case").name
    for suffix in (".nii.gz", ".nii", ".zip"):
        name = name.removesuffix(suffix)
    for suffix in ("_case", "_bundle"):
        name = name.removesuffix(suffix)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")[:64]
    return name or "case"


# ---------------------------------------------------------------------------
# NIfTI checking


def _sniff(raw: bytes) -> str:
    if raw[:2] == GZIP_MAGIC:
        return "gzip"
    if len(raw) > 132 and raw[128:132] == b"DICM":
        return "dicom"
    if raw[:4] == b"PK\x03\x04":
        return "zip"
    if len(raw) >= 348 and raw[344:348] in (b"n+1\x00", b"ni1\x00"):
        return "nifti"
    return "unknown"


def load_nifti(raw: bytes, what: str) -> nib.Nifti1Image:
    """Parse uploaded bytes as a 3D NIfTI image, or raise CaseError."""
    kind = _sniff(raw)
    if kind == "dicom":
        raise CaseError(f"{what}: DICOM is not accepted. De-identify and convert it locally "
                        "with stoneplanner-ingest, then upload the .nii.gz.")
    if kind == "gzip":
        try:
            header = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(352)
        except (OSError, EOFError) as exc:
            raise CaseError(f"{what}: not a readable .nii.gz file") from exc
        if len(header) < 348 or header[344:348] not in (b"n+1\x00", b"ni1\x00"):
            raise CaseError(f"{what}: the gzip file is not a NIfTI image")
        suffix = ".nii.gz"
    elif kind == "nifti":
        suffix = ".nii"
    else:
        raise CaseError(f"{what}: expected a NIfTI file (.nii or .nii.gz)")
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(raw)
        tmp.flush()
        try:
            img = nib.load(tmp.name)
            data = _array(img)
        except Exception as exc:   # any parse failure is a problem with the user's file
            raise CaseError(f"{what}: could not read the NIfTI image ({exc})") from exc
    if data.ndim == 4 and data.shape[3] == 1:
        data = data[..., 0]
    if data.ndim != 3:
        raise CaseError(f"{what}: expected a 3D image, got shape {data.shape}")
    return nib.Nifti1Image(data, img.affine)


def _array(img) -> np.ndarray:
    """Voxel values with scaling applied, without a float64 copy when there is none."""
    slope, inter = img.header.get_slope_inter()
    if slope in (None, 1.0) and inter in (None, 0.0):
        return np.asanyarray(img.dataobj)
    return np.asarray(img.get_fdata(dtype=np.float32))


def clean_image(img: nib.Nifti1Image, labels: bool) -> nib.Nifti1Image:
    """Fresh header, compact dtype. Label maps must hold whole numbers."""
    data = np.asanyarray(img.dataobj)
    if labels:
        if not np.issubdtype(data.dtype, np.integer):
            if not np.all(np.round(data) == data):
                raise CaseError("label map holds non-integer values")
            data = np.round(data)
        if data.min() < 0 or data.max() > 32767:
            raise CaseError("label map values out of range")
        data = data.astype(np.uint8 if data.max() <= 255 else np.int16)
    else:
        info = np.iinfo(np.int16)
        whole = np.issubdtype(data.dtype, np.integer) or np.all(np.round(data) == data)
        if whole and data.min() >= info.min and data.max() <= info.max:
            data = data.astype(np.int16)
        if not np.issubdtype(data.dtype, np.integer):
            data = data.astype(np.float32)
    out = nib.Nifti1Image(data, img.affine)
    out.set_qform(img.affine, code=1)
    out.set_sform(img.affine, code=1)
    out.header.set_xyzt_units("mm", "sec")
    return out


def check_grid(ct: nib.Nifti1Image, other: nib.Nifti1Image, what: str) -> None:
    if other.shape != ct.shape or not np.allclose(other.affine, ct.affine, atol=1e-3):
        raise CaseError(f"{what} is not on the CT's grid (shape or orientation differs)")


# ---------------------------------------------------------------------------
# Store


@dataclass
class Case:
    token: str
    path: Path
    meta: dict

    @property
    def case_id(self) -> str:
        return self.meta["case_id"]

    def file(self, key: str) -> Path:
        return self.path / FILES[key]

    def has(self, key: str) -> bool:
        return self.file(key).exists()


class CaseStore:
    def __init__(self, root: Path, ttl_minutes: int):
        self.root = Path(root)
        self.ttl_s = ttl_minutes * 60

    def reset(self) -> None:
        """Start-up: nothing from a previous process survives."""
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _write_meta(self, case: Case) -> None:
        # Atomic: the viewer fetches several files at once, and every request
        # touches the case, so a reader must never see a half-written file.
        tmp = case.path / f".{META}.{secrets.token_hex(4)}"
        tmp.write_text(json.dumps(case.meta))
        os.replace(tmp, case.path / META)

    def touch(self, case: Case) -> None:
        case.meta["last_used"] = time.time()
        self._write_meta(case)

    def create(self, owner: str, case_id: str, ct: nib.Nifti1Image,
               organs: nib.Nifti1Image | None = None,
               stones: nib.Nifti1Image | None = None, extra_meta: dict | None = None) -> Case:
        ct = clean_image(ct, labels=False)
        for img, what in ((organs, "organ map"), (stones, "stone map")):
            if img is not None:
                check_grid(ct, img, what)
        token = secrets.token_urlsafe(18)
        path = self.root / token
        path.mkdir(parents=True)
        case = Case(token, path, {"case_id": case_id, "owner": owner,
                                  "created": time.time(), "last_used": time.time(),
                                  **(extra_meta or {})})
        nib.save(ct, str(case.file("ct")))
        if organs is not None:
            nib.save(clean_image(organs, labels=True), str(case.file("organs")))
        if stones is not None:
            nib.save(clean_image(stones, labels=True), str(case.file("stones")))
        self._write_meta(case)
        return case

    def get(self, token: str, owner: str) -> Case | None:
        if not TOKEN_RE.match(token or ""):
            return None
        path = self.root / token
        try:
            meta = json.loads((path / META).read_text())
        except (OSError, ValueError):
            return None
        if not secrets.compare_digest(str(meta.get("owner", "")), owner):
            return None
        case = Case(token, path, meta)
        self.touch(case)
        return case

    def delete(self, case: Case) -> None:
        shutil.rmtree(case.path, ignore_errors=True)

    def clear_plan(self, case: Case) -> None:
        for key in PLAN_FILES:
            case.file(key).unlink(missing_ok=True)

    def cleanup(self, now: float | None = None) -> int:
        """Delete folders idle beyond the TTL. Returns how many went."""
        now = time.time() if now is None else now
        removed = 0
        if not self.root.exists():
            return 0
        for path in self.root.iterdir():
            try:
                last = json.loads((path / META).read_text())["last_used"]
            except (OSError, ValueError, KeyError):
                last = path.stat().st_mtime
            if now - last > self.ttl_s:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        return removed

    # -- bundles ---------------------------------------------------------------

    def bundle(self, case: Case) -> bytes:
        """Zip of the case for the user's disk. Open it again with open_bundle."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            manifest = {"format": "stoneplanner-case", "version": 1, "case_id": case.case_id,
                        "files": [FILES[k] for k in BUNDLE_FILES if case.has(k)]}
            zf.writestr(META, json.dumps(manifest, indent=2))
            for key in BUNDLE_FILES:
                if case.has(key):
                    zf.write(case.file(key), FILES[key])
        return buf.getvalue()

    def open_bundle(self, owner: str, raw: bytes, max_bytes: int) -> Case:
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise CaseError("not a saved case (.zip) file") from exc
        allowed = {FILES[k] for k in BUNDLE_FILES} | {META}
        infos = {i.filename: i for i in zf.infolist()}
        if META not in infos or set(infos) - allowed:
            raise CaseError("not a stone planner case bundle")
        if sum(i.file_size for i in infos.values()) > 2 * max_bytes:
            raise CaseError("case bundle is too large")
        manifest = json.loads(zf.read(META))
        if manifest.get("format") != "stoneplanner-case":
            raise CaseError("not a stone planner case bundle")
        if FILES["ct"] not in infos:
            raise CaseError("case bundle has no CT")
        images = {}
        for key in ("ct", "organs", "stones"):
            if FILES[key] in infos:
                images[key] = load_nifti(zf.read(FILES[key]), f"bundle {FILES[key]}")
        case = self.create(owner, case_label(str(manifest.get("case_id", "case"))),
                           images["ct"], images.get("organs"), images.get("stones"))
        # Plan and stone tables are re-derivable text; keep them if present and
        # the image files they belong to came along too.
        for key in ("stones_csv", "tracts_json", "tracts_csv"):
            if FILES[key] in infos and case.has("stones"):
                case.file(key).write_bytes(zf.read(FILES[key]))
        return case

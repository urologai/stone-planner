"""Local case library: the ingest page's storage. Runs on the user's machine only.

The ingest page takes what README.md lists for each case (preoperative CT as
DICOM, operative details, stone description, postoperative outcome,
complications, demographics, and the MRN for the study key) and turns it
into a de-identified case folder plus one line in a separate MRN key file.

Where things go:
    DATA_DIR/<case_id>/<case_id>.nii.gz        de-identified preop CT (RAS, HU)
    DATA_DIR/<case_id>/<case_id>.json          CT metadata from stoneplanner.ingest
    DATA_DIR/<case_id>/<case_id>_clinical.json structured clinical fields
    DATA_DIR/<case_id>/postop/                 de-identified postop CT or KUB (DICOM)
    DATA_DIR/<case_id>/<case_id>_postop.nii.gz postop CT as NIfTI, when it is a CT
    DATA_DIR/manual.csv                        regenerated after every change; the
                                               input scripts/validate.py reads
    KEY_FILE                                   case_id, mrn, date ingested. Must be
                                               outside DATA_DIR, so the data folder
                                               can be shared without the key.

Plans and segmentations saved from the planner land in the case folder with
the CLI's names (<case_id>_organs.nii.gz, _stones.nii.gz, _stones.csv,
_tracts.json, _tracts.csv), which is the layout validate.py expects.

Rules that keep identifiers out of the data folder:
  - The MRN goes only to KEY_FILE. Nothing else stores it.
  - The clinical record is structured fields and numbers only. There is no
    free-text box, so a name or date cannot be typed into it by habit. The
    operative report itself is not stored, only the fields taken from it.
  - Age is stored as years, with 90 and over recorded as 90 (HIPAA safe
    harbor). No dates of birth, surgery or imaging; DICOM dates become the
    year only in ingest.
  - The library refuses to start on Render (RENDER is set there): raw DICOM
    and MRNs must never reach the hosted service.

Uploaded DICOM is staged in the system temp folder, not DATA_DIR, and the
staging copy is deleted once the case is ingested or after the TTL.
"""

from __future__ import annotations

import csv
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from stoneplanner import ingest

GROUPS = ("upper", "mid", "lower", "pelvis")
STONE_LOCATIONS = ("upper calyx", "mid calyx", "lower calyx", "renal pelvis", "UPJ",
                   "proximal ureter")
CASE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
STAGING_TTL_S = 2 * 3600
STAGING_PREFIX = "stoneplanner-staging-"
AGE_CAP = 90

MANUAL_COLUMNS = [
    # Read by scripts/validate.py.
    "case_id", "manual_stone_volume_mm3", "laterality", "operative_calyx",
    "operative_supracostal", "stone_free", "residual_mm",
    # Everything else from the form, for the analysis.
    "n_tracts", "tracts", "or_position", "sheath_fr", "access_guidance",
    "ct_position", "ct_site", "ct_slice_thickness_mm", "stone_location", "staghorn",
    "stone_largest_mm", "postop_imaging", "pleural_injury", "colon_injury", "transfusion",
    "age", "sex", "bmi", "burned_in_suspected",
]


class LibraryError(ValueError):
    """A problem with what the user entered; reported back as a 400."""


def _choice(value, allowed, name, required=True):
    if value in (None, "") and not required:
        return None
    if value not in allowed:
        raise LibraryError(f"{name}: choose one of {', '.join(map(str, allowed))}")
    return value


def _number(value, name, lo, hi, required=True):
    if value in (None, "") and not required:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError) as exc:
        raise LibraryError(f"{name}: enter a number") from exc
    if not lo <= x <= hi:
        raise LibraryError(f"{name}: expected {lo} to {hi}")
    return x


def _flag(value, name):
    if isinstance(value, bool):
        return value
    raise LibraryError(f"{name}: answer yes or no")


def validate_clinical(raw: dict) -> dict:
    """Normalize the ingest form. Every field is a choice, a number or yes/no."""
    if not isinstance(raw, dict):
        raise LibraryError("clinical data missing")
    ct = raw.get("ct") or {}
    op = raw.get("operative") or {}
    stone = raw.get("stone") or {}
    post = raw.get("postop") or {}
    comp = raw.get("complications") or {}
    demo = raw.get("demographics") or {}

    tracts = op.get("tracts") or []
    if not isinstance(tracts, list) or not 1 <= len(tracts) <= 6:
        raise LibraryError("operative tracts: enter 1 to 6 tracts")
    clean_tracts = [{
        "calyx": _choice(t.get("calyx"), GROUPS, f"tract {i} calyceal group"),
        "approach": _choice(t.get("approach"), ("subcostal", "supracostal"),
                            f"tract {i} approach"),
    } for i, t in enumerate(tracts, start=1)]

    locations = stone.get("location") or []
    if not isinstance(locations, list) or not locations:
        raise LibraryError("stone location: pick at least one")
    for loc in locations:
        _choice(loc, STONE_LOCATIONS, "stone location")

    stone_free = _flag(post.get("stone_free"), "postop stone free")
    residual = None if stone_free else _number(post.get("residual_mm"),
                                               "residual fragment size (mm)", 0, 200)
    age = _number(demo.get("age"), "age", 0, 120)
    return {
        "ct": {
            "position": _choice(ct.get("position"), ("supine", "prone", "other"),
                                "CT patient position"),
            "site": _choice(ct.get("site"), ("here", "outside"), "CT done here or outside"),
        },
        "operative": {
            "laterality": _choice(op.get("laterality"), ("left", "right"), "laterality"),
            "or_position": _choice(op.get("or_position"), ("prone", "supine"), "OR position"),
            "tracts": clean_tracts,
            "sheath_fr": _number(op.get("sheath_fr"), "sheath size (Fr)", 6, 40, required=False),
            "guidance": _choice(op.get("guidance"), ("fluoroscopy", "ultrasound", "combined"),
                                "access guidance", required=False),
        },
        "stone": {
            "location": list(locations),
            "staghorn": _choice(stone.get("staghorn"), ("none", "partial", "complete"),
                                "staghorn"),
            "largest_mm": _number(stone.get("largest_mm"), "largest stone dimension (mm)",
                                  1, 200),
            "manual_volume_mm3": _number(stone.get("manual_volume_mm3"),
                                         "manual stone volume (mm3)", 1, 1e6, required=False),
        },
        "postop": {
            "stone_free": stone_free,
            "residual_mm": residual,
        },
        "complications": {
            "pleural_injury": _flag(comp.get("pleural_injury"), "pleural injury"),
            "colon_injury": _flag(comp.get("colon_injury"), "colon injury"),
            "transfusion": _flag(comp.get("transfusion"), "transfusion"),
        },
        "demographics": {
            "age": int(min(age, AGE_CAP)),
            "sex": _choice(demo.get("sex"), ("female", "male", "other"), "sex"),
            "bmi": _number(demo.get("bmi"), "BMI", 10, 90),
        },
    }


def _validate_mrn(mrn: str) -> str:
    mrn = (mrn or "").strip()
    if not mrn or len(mrn) > 64 or any(c in mrn for c in ",\n\r\""):
        raise LibraryError("MRN: required, up to 64 characters, no commas or quotes")
    return mrn


class Library:
    def __init__(self, data_dir: Path, key_file: Path):
        if os.environ.get("RENDER"):
            raise RuntimeError("the local case library must not run on Render")
        self.data_dir = Path(data_dir).resolve()
        self.key_file = Path(key_file).resolve()
        if self.key_file == self.data_dir or self.data_dir in self.key_file.parents:
            raise RuntimeError("KEY_FILE must be outside DATA_DIR, so the data folder can "
                               "be shared without the MRN key")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        # Uploaded DICOM is identifiable until de-identified: stage it in a
        # private (0700) folder, and clear this user's leftovers from runs that
        # did not shut down cleanly.
        tmp = Path(tempfile.gettempdir())
        for old in tmp.glob(STAGING_PREFIX + "*"):
            if not old.is_symlink() and old.is_dir() and old.stat().st_uid == os.getuid():
                shutil.rmtree(old, ignore_errors=True)
        self.staging_root = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX))

    # -- staging uploaded DICOM -------------------------------------------------

    def new_staging(self) -> tuple[str, Path]:
        self.cleanup_staging()
        token = secrets.token_urlsafe(12)
        path = self.staging_root / token
        path.mkdir()
        return token, path

    def staging_path(self, token: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", token or ""):
            raise LibraryError("unknown upload; scan the folder again")
        path = self.staging_root / token
        if not path.is_dir():
            raise LibraryError("upload expired; scan the folder again")
        return path

    def cleanup_staging(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for path in self.staging_root.iterdir():
            if now - path.stat().st_mtime > STAGING_TTL_S:
                shutil.rmtree(path, ignore_errors=True)

    # -- cases -------------------------------------------------------------------

    def case_dir(self, case_id: str) -> Path:
        if not CASE_ID_RE.match(case_id or ""):
            raise LibraryError("case ID: start with a letter; letters, digits, _ or - only")
        return self.data_dir / case_id

    def case_ids(self) -> list[str]:
        return sorted(p.name for p in self.data_dir.iterdir()
                      if p.is_dir() and (p / f"{p.name}_clinical.json").exists())

    def next_case_id(self) -> str:
        numbers = [int(m.group(1)) for c in self.case_ids()
                   if (m := re.fullmatch(r"case_(\d+)", c))]
        return f"case_{(max(numbers) + 1) if numbers else 1:03d}"

    def _key_rows(self) -> list[dict]:
        if not self.key_file.exists():
            return []
        with self.key_file.open(newline="") as f:
            return list(csv.DictReader(f))

    def key_conflict(self, case_id: str, mrn: str) -> str | None:
        """Why case_id cannot be used for this MRN, if the key file links it elsewhere."""
        linked = {r["case_id"]: r["mrn"] for r in self._key_rows()}
        if case_id in linked and linked[case_id] != mrn:
            return (f"this computer's key file already links {case_id} to a different MRN; "
                    "the workbook and the key disagree, so check which patient it is")
        return None

    def _append_key(self, case_id: str, mrn: str) -> None:
        new = not self.key_file.exists()
        with self.key_file.open("a", newline="") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(["case_id", "mrn", "ingested"])
            writer.writerow([case_id, mrn, datetime.now().astimezone().date().isoformat()])
        os.chmod(self.key_file, 0o600)

    def ingest(self, mrn: str, preop: dict, clinical: dict, postop: dict | None = None,
               case_id: str | None = None) -> dict:
        """De-identify and file one case. preop/postop: {"staging": token, "series_uid": uid}."""
        mrn = _validate_mrn(mrn)
        record = validate_clinical(clinical)
        case_id = case_id or self.next_case_id()
        folder = self.case_dir(case_id)
        if folder.exists():
            raise LibraryError(f"{case_id} already exists")
        conflict = self.key_conflict(case_id, mrn)
        if conflict:
            raise LibraryError(conflict)
        notes = []
        if any(row["mrn"] == mrn for row in self._key_rows()):
            linked = [r["case_id"] for r in self._key_rows() if r["mrn"] == mrn]
            notes.append(f"This MRN is already linked to {', '.join(linked)}.")

        pre_src = self.staging_path(preop.get("staging", ""))
        post_src = self.staging_path(postop["staging"]) if postop else None
        folder.mkdir(parents=True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                deid = ingest.deidentify_series(pre_src, Path(tmp) / "pre",
                                                series_uid=preop.get("series_uid"))
                burned = ingest.detect_burned_in_text(deid)
                nifti = ingest.convert_to_nifti(deid, folder / f"{case_id}.nii.gz")
                ingest.write_metadata(deid, nifti, case_id, folder / f"{case_id}.json",
                                      burned_in=burned)
                meta = json.loads((folder / f"{case_id}.json").read_text())
                postop_imaging, post_burned = "none", False
                if post_src is not None:
                    postop_imaging, post_burned = self._ingest_postop(
                        post_src, postop.get("series_uid"), folder, case_id, Path(tmp))
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

        record["postop"]["imaging"] = postop_imaging
        record["flags"] = {"burned_in_suspected": bool(burned),
                           "postop_burned_in_suspected": bool(post_burned)}
        (folder / f"{case_id}_clinical.json").write_text(json.dumps(
            {"case_id": case_id, **record}, indent=2))
        self._append_key(case_id, mrn)
        for src in (pre_src, post_src):
            if src is not None:
                shutil.rmtree(src, ignore_errors=True)
        self.write_manual_csv()

        if meta.get("slice_thickness_mm") and meta["slice_thickness_mm"] > 3:
            notes.append(f"Slice thickness {meta['slice_thickness_mm']} mm is thicker than "
                         "the 1 to 3 mm asked for; small stones will read low.")
        if meta.get("contrast_agent_tag_present"):
            notes.append("The DICOM header names a contrast agent; this should be a "
                         "noncontrast CT.")
        if burned:
            notes.append("Burned-in annotation suspected on the CT: look at the images "
                         "before sharing them.")
        if post_burned:
            notes.append("Check the postoperative images by eye for burned-in labels before "
                         "sharing them.")
        return {"case_id": case_id, "notes": notes, "metadata": meta}

    def _ingest_postop(self, src: Path, series_uid: str | None, folder: Path, case_id: str,
                       tmp: Path) -> tuple[str, bool]:
        series = [s for s in ingest.list_series(src)
                  if series_uid is None or s["series_uid"] == series_uid]
        if not series:
            raise LibraryError("postoperative series not found in the upload")
        kind = series[0]["kind"]
        if kind not in ("ct", "radiograph"):
            raise LibraryError("postoperative imaging must be a CT or a plain radiograph")
        deid = ingest.deidentify_series(src, tmp / "post", series_uid=series_uid,
                                        kinds=(kind,))
        burned = ingest.detect_burned_in_text(deid, kinds=(kind,))
        shutil.copytree(deid, folder / "postop")
        if kind == "ct":
            ingest.convert_to_nifti(deid, folder / f"{case_id}_postop.nii.gz")
            return "ct", burned
        return "kub", burned

    # -- listing and the validation table ----------------------------------------

    def clinical(self, case_id: str) -> dict:
        return json.loads((self.case_dir(case_id) / f"{case_id}_clinical.json").read_text())

    def list_cases(self) -> list[dict]:
        rows = []
        for case_id in self.case_ids():
            rec = self.clinical(case_id)
            folder = self.case_dir(case_id)
            rows.append({
                "case_id": case_id,
                "laterality": rec["operative"]["laterality"],
                "tracts": [f"{t['calyx']} {t['approach']}" for t in rec["operative"]["tracts"]],
                "stone_free": rec["postop"]["stone_free"],
                "postop_imaging": rec["postop"]["imaging"],
                "burned_in_suspected": rec["flags"]["burned_in_suspected"],
                "postop_burned_in_suspected": rec["flags"]["postop_burned_in_suspected"],
                "segmented": (folder / f"{case_id}_organs.nii.gz").exists(),
                "planned": (folder / f"{case_id}_tracts.json").exists(),
            })
        return rows

    def manual_row(self, case_id: str) -> dict:
        rec = self.clinical(case_id)
        meta_path = self.case_dir(case_id) / f"{case_id}.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        op, st, po = rec["operative"], rec["stone"], rec["postop"]
        first = op["tracts"][0]

        def blank(v):
            return "" if v is None else v

        def yes(v):
            return int(bool(v))

        return {
            "case_id": case_id,
            "manual_stone_volume_mm3": blank(st["manual_volume_mm3"]),
            "laterality": op["laterality"],
            # validate.py scores one operative tract: the first one entered.
            "operative_calyx": first["calyx"],
            "operative_supracostal": yes(first["approach"] == "supracostal"),
            "stone_free": yes(po["stone_free"]),
            "residual_mm": blank(po["residual_mm"]),
            "n_tracts": len(op["tracts"]),
            "tracts": ";".join(f"{t['calyx']}/{t['approach']}" for t in op["tracts"]),
            "or_position": op["or_position"],
            "sheath_fr": blank(op["sheath_fr"]),
            "access_guidance": blank(op["guidance"]),
            "ct_position": rec["ct"]["position"],
            "ct_site": rec["ct"]["site"],
            "ct_slice_thickness_mm": blank(meta.get("slice_thickness_mm")),
            "stone_location": ";".join(st["location"]),
            "staghorn": st["staghorn"],
            "stone_largest_mm": st["largest_mm"],
            "postop_imaging": po["imaging"],
            "pleural_injury": yes(rec["complications"]["pleural_injury"]),
            "colon_injury": yes(rec["complications"]["colon_injury"]),
            "transfusion": yes(rec["complications"]["transfusion"]),
            "age": rec["demographics"]["age"],
            "sex": rec["demographics"]["sex"],
            "bmi": rec["demographics"]["bmi"],
            "burned_in_suspected": yes(rec["flags"]["burned_in_suspected"]),
        }

    def write_manual_csv(self) -> Path:
        path = self.data_dir / "manual.csv"
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MANUAL_COLUMNS)
            writer.writeheader()
            for case_id in self.case_ids():
                writer.writerow(self.manual_row(case_id))
        os.replace(tmp, path)
        return path

    # -- planner results ---------------------------------------------------------

    def result_path(self, case_id: str, suffix: str) -> Path:
        """Where the planner saves a result, with the CLI's name for it."""
        return self.case_dir(case_id) / f"{case_id}_{suffix}"

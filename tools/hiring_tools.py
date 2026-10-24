"""Hiring: resume intake and anonymising.

Every function returns a dict and never raises (extract_text and anonymise return a str).
Resumes live in config.DATA_DIR / "resumes" / <opening_id>; dropping a .txt/.pdf there is enough
to register a candidate on the next call.
"""
import io
import re
from pathlib import Path

import config
import store

STAGES = ["Applied", "Shortlisted", "Interview", "Offer", "Hired", "Rejected"]
RESUME_TYPES = (".txt", ".pdf")


def extract_text(filename, data):
    """Plain text from a resume. str passes through, .pdf goes through pypdf, anything else is utf-8."""
    if isinstance(data, str):
        return data
    try:
        if str(filename).lower().endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        return bytes(data).decode("utf-8", errors="ignore")
    except Exception:
        return ""


_PERSONAL = [
    re.compile(r"\bGender\s*:\s*\w+", re.I),
    re.compile(r"\b(?:fe)?male\b", re.I),
    re.compile(r"\bAge\s*:?\s*\d{1,3}\b", re.I),
    re.compile(r"\bDOB\s*:?[^|\n]*", re.I),
]
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d \-()]{6,}\d")


def _drop_phone(match):
    # Only real phone numbers (10+ digits), so "Stage 2", "page 3" or "2019 - 2024" stay.
    digits = sum(ch.isdigit() for ch in match.group())
    return "" if digits >= 10 else match.group()


def anonymise(text):
    """Remove the name, contact details, gender, age and DOB. Skills and experience stay as they are."""
    try:
        lines = str(text or "").strip().splitlines()
        if not lines:
            return ""
        name, body = lines[0], "\n".join(lines[1:])
        # Order matters: personal details first, or a DOB would be eaten as a phone number.
        for pattern in _PERSONAL:
            body = pattern.sub("", body)
        body = _EMAIL.sub("", body)
        body = _PHONE.sub(_drop_phone, body)
        for word in set(re.findall(r"[A-Za-z]{2,}", name)):
            body = re.sub(rf"\b{re.escape(word)}\b", "", body, flags=re.I)
        return body.strip()
    except Exception:
        return ""


# ---- data access ----

def _openings():
    return store.read_json("openings.json")


def _folder(opening_id):
    return config.DATA_DIR / "resumes" / opening_id


def _find_opening(opening_id):
    key = str(opening_id or "").strip().lower()
    return next((o for o in _openings() if o["id"].lower() == key), None)


def _sync():
    """Register any resume file in an opening's folder that isn't a candidate yet."""
    candidates = store.read_json("candidates.json")
    known = {(c["opening_id"], c["file"]) for c in candidates}
    added = False
    for opening in _openings():
        folder = _folder(opening["id"])
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir(), key=lambda p: p.name):
            if not path.is_file() or path.suffix.lower() not in RESUME_TYPES:
                continue
            if (opening["id"], path.name) in known:
                continue
            text = extract_text(path.name, path.read_bytes())
            name = next((line.strip() for line in text.splitlines() if line.strip()), path.stem)
            candidates.append({
                "id": f"C{len(candidates) + 1}",
                "opening_id": opening["id"],
                "name": name,
                "file": path.name,
                "stage": "Applied",
            })
            known.add((opening["id"], path.name))
            added = True
    if added:
        store.write_json("candidates.json", candidates)
    return candidates


# ---- intake ----

def list_openings():
    try:
        candidates = _sync()
        return {"openings": [
            {
                "id": o["id"],
                "title": o["title"],
                "team": o.get("team", ""),
                "candidates": sum(1 for c in candidates if c["opening_id"] == o["id"]),
            }
            for o in _openings()
        ]}
    except Exception as e:
        return {"error": f"Could not list openings: {e}"}


def _skill_list(value):
    if isinstance(value, str):
        value = value.split(",")
    return [str(s).strip() for s in value or [] if str(s).strip()]


def create_opening(title, must_have, nice_to_have=None, min_years=0, jd_text="", team=""):
    try:
        title = str(title or "").strip()
        if not title:
            return {"error": "An opening needs a title."}
        openings = _openings()
        numbers = [int(o["id"][2:]) for o in openings if o["id"][2:].isdigit()]
        opening_id = f"OP{max(numbers, default=0) + 1}"
        openings.append({
            "id": opening_id,
            "title": title,
            "team": str(team or "").strip(),
            "must_have": _skill_list(must_have),
            "nice_to_have": _skill_list(nice_to_have),
            "min_years": min_years or 0,
            "jd_text": str(jd_text or "").strip(),
        })
        store.write_json("openings.json", openings)
        _folder(opening_id).mkdir(parents=True, exist_ok=True)
        return {"opening_id": opening_id, "title": title}
    except Exception as e:
        return {"error": f"Could not create the opening: {e}"}


def add_resume(opening_id, filename, data):
    try:
        opening = _find_opening(opening_id)
        if opening is None:
            return {"error": f"No opening {opening_id}."}
        filename = Path(str(filename or "")).name
        if not filename.lower().endswith(RESUME_TYPES):
            return {"error": "Resumes must be .txt or .pdf files."}
        folder = _folder(opening["id"])
        folder.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            (folder / filename).write_text(data, encoding="utf-8")
        else:
            (folder / filename).write_bytes(bytes(data))
        cand = next(c for c in _sync() if c["opening_id"] == opening["id"] and c["file"] == filename)
        return {"candidate_id": cand["id"], "name": cand["name"]}
    except Exception as e:
        return {"error": f"Could not add the resume: {e}"}

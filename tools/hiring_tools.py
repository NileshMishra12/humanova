"""Hiring: resume intake, anonymising and screening.

Every function returns a dict and never raises (extract_text and anonymise return a str).
Resumes live in config.DATA_DIR / "resumes" / <opening_id>; dropping a .txt/.pdf there is enough
to register a candidate on the next call.
"""
import hashlib
import io
import re
from pathlib import Path

import config
import llm
import store

STAGES = ["Applied", "Shortlisted", "Interview", "Offer", "Hired", "Rejected"]
RESUME_TYPES = (".txt", ".pdf")
NEXT_STEPS = ("interview", "hold", "reject")

SCORING_PROMPT = """You are screening a resume for a job opening.
Judge ONLY skills and experience. Ignore name, gender, age, location and background completely.

Opening: {title}
Job description: {jd}
Must-have skills: {must}
Nice-to-have skills: {nice}
Minimum years of experience: {min_years}

Anonymised resume:
---
{resume}
---

Reply with JSON only:
{{"score": <0-100>, "matched": [skills from the lists that the resume shows],
"missing": [skills from the lists that the resume does not show], "years_experience": <number>,
"reason": "<one sentence>", "next_step": "interview" | "hold" | "reject"}}"""


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


def _find_opening(opening_id_or_title):
    key = str(opening_id_or_title or "").strip().lower()
    return next((o for o in _openings() if key in (o["id"].lower(), o["title"].lower())), None)


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


# ---- scoring ----

def _has_skill(text, skill):
    # "Angular" must not match "AngularJS"; "Node.js" must match "Node.js".
    return re.search(rf"(?<![\w.]){re.escape(skill)}(?!\w)", text, re.I) is not None


def _years(text):
    found = re.findall(r"(\d+(?:\.\d+)?)\s*\+?\s*years?\b", text, re.I)
    return max((float(n) for n in found), default=0.0)


def _fmt_years(years):
    return f"{float(years):g}"


def _next_step(score):
    return "interview" if score >= 75 else "hold" if score >= 50 else "reject"


def _rule_score(opening, text):
    must = _skill_list(opening.get("must_have"))
    nice = _skill_list(opening.get("nice_to_have"))
    min_years = float(opening.get("min_years") or 0)
    must_found = [s for s in must if _has_skill(text, s)]
    nice_found = [s for s in nice if _has_skill(text, s)]
    years = _years(text)

    score = 60 * len(must_found) / len(must) if must else 60
    score += 20 * len(nice_found) / len(nice) if nice else 20
    score += 20 * (min(years / min_years, 1) if min_years else 1)
    score = min(round(score), 98)

    reason = f"Matches {len(must_found)}/{len(must)} must-have skills"
    if must_found:
        reason += f" ({', '.join(must_found)})"
    reason += f"; {_fmt_years(years)} years experience vs {_fmt_years(min_years)} required."
    return {
        "score": score,
        "matched": must_found + nice_found,
        "missing": [s for s in must + nice if s not in must_found + nice_found],
        "years_experience": years,
        "reason": reason,
        "next_step": _next_step(score),
    }


def _score(opening, text):
    """Claude's score when online; the rule score when offline or on any error."""
    rules = _rule_score(opening, text)
    try:
        reply = llm.complete_json(SCORING_PROMPT.format(
            title=opening["title"],
            jd=opening.get("jd_text", ""),
            must=", ".join(_skill_list(opening.get("must_have"))),
            nice=", ".join(_skill_list(opening.get("nice_to_have"))) or "none",
            min_years=_fmt_years(opening.get("min_years") or 0),
            resume=text,
        ), max_tokens=400)
        score = max(0, min(100, round(float(reply["score"]))))
        next_step = str(reply.get("next_step", "")).strip().lower()
        return {
            "score": score,
            "matched": _skill_list(reply["matched"]) if "matched" in reply else rules["matched"],
            "missing": _skill_list(reply["missing"]) if "missing" in reply else rules["missing"],
            "years_experience": float(reply.get("years_experience") or rules["years_experience"]),
            "reason": str(reply.get("reason") or "").strip() or rules["reason"],
            "next_step": next_step if next_step in NEXT_STEPS else _next_step(score),
        }
    except Exception:
        return rules


def _read_resume(cand):
    path = _folder(cand["opening_id"]) / cand["file"]
    try:
        return extract_text(cand["file"], path.read_bytes())
    except OSError:
        return ""


def screen_candidates(opening_id):
    try:
        opening = _find_opening(opening_id)
        if opening is None:
            return {"error": f"No opening {opening_id}."}
        candidates = _sync()
        changed = False
        for cand in candidates:
            if cand["opening_id"] != opening["id"]:
                continue
            text = _read_resume(cand)
            digest = hashlib.md5(text.encode("utf-8")).hexdigest()
            if cand.get("hash") == digest and "score" in cand:
                continue  # same CV as last time: no tokens spent again
            cand.update(_score(opening, anonymise(text)))
            cand["hash"] = digest
            changed = True
        if changed:
            store.write_json("candidates.json", candidates)
        ranked = sorted(
            (
                {
                    "candidate_id": c["id"],
                    "name": c["name"],
                    "score": c["score"],
                    "matched": c["matched"],
                    "missing": c["missing"],
                    "years_experience": c["years_experience"],
                    "reason": c["reason"],
                    "next_step": c["next_step"],
                    "stage": c["stage"],
                }
                for c in candidates if c["opening_id"] == opening["id"]
            ),
            key=lambda c: c["score"],
            reverse=True,
        )
        return {"opening": opening["title"], "opening_id": opening["id"], "ranked": ranked}
    except Exception as e:
        return {"error": f"Could not screen candidates: {e}"}

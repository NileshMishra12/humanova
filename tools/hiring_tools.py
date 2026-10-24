"""Hiring: resume intake, anonymising, screening, stage approvals, email drafts, interview questions.

Every function returns a dict and never raises (extract_text and anonymise return a str).
Resumes live in config.DATA_DIR / "resumes" / <opening_id>; dropping a .txt/.pdf there is enough
to register a candidate on the next call.
"""
import hashlib
import io
import re
from datetime import date, timedelta
from pathlib import Path

import config
import llm
import store

STAGES = ["Applied", "Shortlisted", "Interview", "Offer", "Hired", "Rejected"]
RESUME_TYPES = (".txt", ".pdf")
NEXT_STEPS = ("interview", "hold", "reject")
EMAIL_STATUS = "draft - not sent"

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


_SECTIONS = r"\b(experience|education|skills?|projects?|summary|objective|employment|qualifications?|certifications?|achievements?)\b"
_ROLES = r"\b(developer|engineer|manager|analyst|designer|consultant|intern|architect|administrator|executive|lead|tester|specialist|officer)\b"
_DEGREES = r"\b(b\.?tech|m\.?tech|b\.?e\b|b\.?sc|m\.?sc|bca|mca|mba|bachelor|master|diploma|degree|university|college)\b"
_NOT_A_CV = r"\b(invoice|report generated|statement|folio|total amount|balance|receipt|purchase order|market value)\b"


def looks_like_resume(text, skills=()):
    """(True, '') for something that reads like a CV, else (False, why). Cheap rules, no AI: it only has to
    stop invoices, statements and other reports from being scored as candidates. skills are the opening's
    skills: naming two or more of them is good evidence, so a very short but real CV still passes."""
    text = text or ""
    named = [s for s in skills if _has_skill(text, s)]
    if len(text.strip()) < (30 if len(named) >= 2 else 120):
        return False, "there is almost no readable text (a scanned image PDF can't be read yet)"
    low = text.lower()
    letters, digits = sum(c.isalpha() for c in text), sum(c.isdigit() for c in text)
    score = 2 if len(named) >= 2 else 0
    sections = len(set(re.findall(_SECTIONS, low)))
    score += 2 if sections >= 2 else sections
    score += 1 if re.search(r"\b\d+(\.\d+)?\+?\s*years?\b|\b(19|20)\d{2}\s*[-–]\s*((19|20)\d{2}|present|current)", low) else 0
    score += 1 if re.search(_ROLES, low) else 0
    score += 1 if re.search(_DEGREES, low) else 0
    score += 1 if re.search(r"\S+@\S+|\+?\d[\d\s-]{8,}\d", text) else 0
    score -= 2 if digits > 0.25 * max(letters, 1) else 0
    score -= 2 if len(re.findall(_NOT_A_CV, low)) >= 2 else 0
    if score >= 3:
        return True, ""
    return False, "it doesn't read like a CV: no skills, experience or education sections were found"


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
        ok, why = looks_like_resume(extract_text(filename, data),
                                    list(opening.get("must_have", [])) + list(opening.get("nice_to_have", [])))
        if not ok:
            return {"error": f"{filename} was not added: {why}. Upload a resume with skills, experience and education."}
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


# ---- candidates and stage changes (always through a human approval) ----

def _find_candidate(candidates, candidate_id_or_name):
    key = str(candidate_id_or_name or "").strip().lower()
    return next((c for c in candidates if key in (c["id"].lower(), c["name"].lower())), None)


def get_candidate(candidate_id):
    try:
        cand = _find_candidate(_sync(), candidate_id)
        if cand is None:
            return {"error": f"No candidate {candidate_id}."}
        return {"candidate": {k: v for k, v in cand.items() if k != "hash"}}
    except Exception as e:
        return {"error": f"Could not read the candidate: {e}"}


def _stage(stage):
    stage = str(stage or "").strip().title()
    return stage if stage in STAGES else None


def request_stage_change(candidate_id, stage, requested_by="HumaNova"):
    try:
        new_stage = _stage(stage)
        if new_stage is None:
            return {"error": f"Unknown stage {stage}. Use one of: {', '.join(STAGES)}."}
        cand = _find_candidate(_sync(), candidate_id)
        if cand is None:
            return {"error": f"No candidate {candidate_id}."}
        summary = f"Move {cand['name']} ({cand['id']}) from {cand['stage']} to {new_stage}"
        approval = store.create_approval(
            "candidate_stage", cand["id"], {"stage": new_stage}, requested_by, summary
        )
        return {"approval_id": approval["id"], "status": "pending", "summary": summary}
    except Exception as e:
        return {"error": f"Could not request the stage change: {e}"}


def apply_stage_change(candidate_id, stage):
    try:
        new_stage = _stage(stage)
        if new_stage is None:
            return {"error": f"Unknown stage {stage}. Use one of: {', '.join(STAGES)}."}
        candidates = _sync()
        cand = _find_candidate(candidates, candidate_id)
        if cand is None:
            return {"error": f"No candidate {candidate_id}."}
        cand["stage"] = new_stage
        store.write_json("candidates.json", candidates)
        return {"candidate_id": cand["id"], "stage": new_stage}
    except Exception as e:
        return {"error": f"Could not change the stage: {e}"}


# ---- drafts (never sent from here) ----

EMAIL_PROMPT = """Write a short, warm email from our Talent Team to a job candidate.
Candidate first name: {first_name}
Role: {title}
{instructions}
Sign it "Talent Team". Reply with JSON only: {{"subject": "...", "body": "..."}}"""

QUESTIONS_PROMPT = """Write exactly 5 interview questions for a candidate for the role "{title}".
Skills the candidate showed: {matched}
Skills the candidate is missing: {missing}
At least 2 questions must be about the missing skills (if there are any). Keep each question to one sentence.
Reply with JSON only: {{"questions": ["...", "...", "...", "...", "..."]}}"""

GENERAL_QUESTIONS = [
    "Tell us about a bug that took you a long time to find and how you finally solved it.",
    "How do you make sure your code is easy for a teammate to review and maintain?",
    "Describe a time you disagreed with a technical decision. What did you do?",
    "How do you keep an application fast as more features are added?",
    "What would you like to learn or improve in your first six months here?",
]


def _interview_slots():
    today = date.fromisoformat(config.TODAY)
    monday = today + timedelta(days=7 - today.weekday())
    slots = [(monday + timedelta(days=1), "11:00 AM"), (monday + timedelta(days=3), "3:00 PM")]
    return [f"{d.strftime('%A %d %B')} at {t}" for d, t in slots]


def _template_email(kind, first_name, title):
    if kind == "invite":
        slots = _interview_slots()
        return {
            "subject": f"Interview invitation: {title}",
            "body": (
                f"Hi {first_name},\n\n"
                f"Thank you for applying for the {title} role. We enjoyed reading your profile and "
                f"would love to meet you for a 45-minute technical interview.\n\n"
                f"Could one of these slots next week work for you?\n"
                f"- {slots[0]}\n- {slots[1]}\n\n"
                f"Just reply with the one that suits you, or suggest another time.\n\n"
                f"Best regards,\nTalent Team"
            ),
        }
    return {
        "subject": f"Your application for {title}",
        "body": (
            f"Hi {first_name},\n\n"
            f"Thank you for your interest in the {title} role and for the time you put into your "
            f"application. After careful review, we have decided to move forward with other "
            f"candidates whose experience is a closer match for this position.\n\n"
            f"We wish you the very best in your search.\n\n"
            f"Best wishes,\nTalent Team"
        ),
    }


def draft_email(candidate_id, kind="invite"):
    try:
        kind = str(kind or "").strip().lower()
        if kind not in ("invite", "reject"):
            return {"error": "kind must be 'invite' or 'reject'."}
        cand = _find_candidate(_sync(), candidate_id)
        if cand is None:
            return {"error": f"No candidate {candidate_id}."}
        first_name = cand["name"].split()[0]
        title = _find_opening(cand["opening_id"])["title"]
        if kind == "invite":
            instructions = ("Invite them to a 45-minute technical interview and offer these two slots "
                            "next week: " + " or ".join(_interview_slots()) + ".")
        else:
            instructions = "Politely tell them we will not move forward this time, and thank them."
        try:
            reply = llm.complete_json(EMAIL_PROMPT.format(
                first_name=first_name, title=title, instructions=instructions
            ))
            email = {"subject": str(reply["subject"]), "body": str(reply["body"])}
        except Exception:
            email = _template_email(kind, first_name, title)
        return {**email, "status": EMAIL_STATUS}
    except Exception as e:
        return {"error": f"Could not draft the email: {e}"}


def _template_questions(matched, missing):
    questions = [f"Walk us through a recent project where you used {s}." for s in matched[:3]]
    questions += [f"How would you get up to speed with {s} in your first month?" for s in missing[:2]]
    questions += [q for q in GENERAL_QUESTIONS if q not in questions]
    return questions[:5]


def interview_questions(candidate_id):
    try:
        cand = _find_candidate(_sync(), candidate_id)
        if cand is None:
            return {"error": f"No candidate {candidate_id}."}
        opening = _find_opening(cand["opening_id"])
        if "matched" not in cand:  # never screened: use the rule score for matched/missing
            cand = {**cand, **_rule_score(opening, anonymise(_read_resume(cand)))}
        matched, missing = cand["matched"], cand["missing"]
        try:
            reply = llm.complete_json(QUESTIONS_PROMPT.format(
                title=opening["title"], matched=", ".join(matched) or "none",
                missing=", ".join(missing) or "none",
            ))
            questions = list(dict.fromkeys(str(q).strip() for q in reply["questions"] if str(q).strip()))
        except Exception:
            questions = []
        if len(questions) < 5:  # offline, or Claude gave too few: top up from the template
            questions += [q for q in _template_questions(matched, missing) if q not in questions]
        return {"candidate_id": cand["id"], "questions": questions[:5]}
    except Exception as e:
        return {"error": f"Could not write interview questions: {e}"}

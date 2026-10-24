"""Creates the hiring demo data: one opening, an empty candidate list and five resumes.

Run it again at any time to reset hiring back to the demo state.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import store  # noqa: E402

OPENING = {
    "id": "OP1",
    "title": "Angular Developer",
    "team": "Web",
    "must_have": ["Angular", "TypeScript", "RxJS"],
    "nice_to_have": ["NgRx", "Jest", "Node.js"],
    "min_years": 3,
    "jd_text": (
        "We are hiring an Angular Developer for our Surat office to build enterprise dashboards "
        "in Angular with TypeScript and RxJS. You will write unit tests, integrate REST APIs and "
        "work closely with designers and backend developers. 3+ years of experience required."
    ),
}

RESUMES = {
    "01_rohan_verma.txt": """Rohan Verma
rohan.verma@mail.test | +91 98250 11111 | Male, Age 29

Senior Frontend Developer with 5 years of experience building enterprise web apps.

Skills: Angular 12-18, TypeScript, RxJS, NgRx, Jest, Node.js, HTML, SCSS, REST APIs

Experience
- Finlytics Pvt Ltd, Senior Frontend Developer: built a trading dashboard in Angular 17 with NgRx
  state management and RxJS streams for live prices. Raised Jest unit test coverage to 85%.
- WebCraft Solutions, Frontend Developer: migrated an Angular 12 admin portal to standalone
  components; wrote small Node.js services for the REST APIs the portal used.

Education: B.E. Computer Engineering, SVNIT Surat
""",
    "02_meera_joshi.txt": """Meera Joshi
meera.joshi@mail.test | +91 98250 22222 | Gender: Female

Frontend Developer with 3.5 years of experience in Angular.

Skills: Angular, TypeScript, RxJS, Karma, Jasmine, Bootstrap, REST APIs

Experience
- Brightline Tech, Frontend Developer: built reporting screens for an HR product in Angular and
  TypeScript, using RxJS for search and filters. Wrote unit tests with Karma and Jasmine.
- Codewave Studio, Junior Developer: built forms and tables for a logistics client portal.

Education: B.Tech Information Technology, DDU Nadiad
""",
    "03_sameer_khan.txt": """Sameer Khan
sameer.khan@mail.test | +91 98250 33333

React Developer with 4 years of experience.

Skills: React, Redux, TypeScript, Jest, Node.js, Express, GraphQL

Experience
- Pixelforge, React Developer: built a customer portal in React and TypeScript with Jest tests and a
  Node.js backend for the APIs.
- Legacy project: maintained an old AngularJS (v1) internal tool for six months before it was retired.

Education: B.Sc. Computer Science, Gujarat University
""",
    "04_isha_gupta.txt": """Isha Gupta
isha.gupta@mail.test | +91 98250 44444 | DOB: 14/03/2003

Fresher looking for a first full-time role as a frontend developer.

Skills: Angular (online course), TypeScript basics, HTML, CSS, JavaScript

Experience
- 6-month internship at Nova Infotech: fixed UI bugs and built two simple Angular pages.
- Course project: a to-do app in Angular.

Education: BCA, Veer Narmad South Gujarat University
""",
    "05_deepak_shah.txt": """Deepak Shah
deepak.shah@mail.test | +91 98250 55555

Accountant with 6 years of experience in small and mid-size firms.

Skills: Tally, GST filing, Excel, bank reconciliation, payroll

Experience
- Shah & Mehta Associates, Senior Accountant: GST returns, monthly closing and audits.
- Patel Traders, Accountant: billing, ledgers and vendor payments in Tally.

Education: B.Com, Veer Narmad South Gujarat University
""",
}


def main():
    resumes_dir = config.DATA_DIR / "resumes"
    if resumes_dir.exists():
        shutil.rmtree(resumes_dir)
    folder = resumes_dir / OPENING["id"]
    folder.mkdir(parents=True)
    for filename, text in RESUMES.items():
        (folder / filename).write_text(text, encoding="utf-8")

    store.write_json("openings.json", [OPENING])
    store.write_json("candidates.json", [])
    print(f"Hiring data written to {config.DATA_DIR}: 1 opening, {len(RESUMES)} resumes in {folder}")


if __name__ == "__main__":
    main()

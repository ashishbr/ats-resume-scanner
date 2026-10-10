"""Extraction accuracy checks.

Without a ground truth we estimate accuracy from four signals:
  1. Engine agreement   - do independent parsers read the same words in the same order?
  2. Text cleanliness   - broken glyphs, (cid:x) codes, letter-spaced or run-together words
  3. Structure          - can contact details and standard section headings be found?
  4. Keyword integrity  - are skills found by any parser also present in the primary text?

With a reference text (the resume content you know is correct, e.g. pasted from
your source document) we also compute a verified word-level accuracy.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from .extract import ENGINE_PRIORITY, ExtractionResult
from .keywords import find_skills
from .text_utils import normalize, word_tokens

SECTION_HEADINGS = {
    "Summary": ["summary", "professional summary", "profile", "professional profile", "objective",
                "career objective", "about me", "career summary", "summary of qualifications"],
    "Experience": ["experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "relevant experience", "internship experience",
                   "internships", "career history", "experience & internships"],
    "Education": ["education", "academic background", "academics", "education and training",
                  "academic qualifications", "education & certifications"],
    "Skills": ["skills", "technical skills", "core competencies", "competencies", "technologies",
               "tech stack", "skills & tools", "skills and tools", "key skills", "areas of expertise",
               "technical proficiencies", "skills & interests", "skills summary"],
    "Projects": ["projects", "academic projects", "personal projects", "key projects", "selected projects",
                 "technical projects", "project experience"],
    "Certifications": ["certifications", "certificates", "licenses & certifications",
                       "licenses and certifications", "certification", "courses & certifications"],
    "Awards": ["awards", "honors", "achievements", "honors & awards", "awards & honors",
               "accomplishments", "scholarships", "awards & scholarships"],
    "Leadership": ["leadership", "activities", "extracurricular activities", "volunteer", "volunteering",
                   "leadership & activities", "involvement", "leadership experience"],
    "Publications": ["publications", "research", "research experience"],
}
ESSENTIAL_SECTIONS = ["Experience", "Education", "Skills"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w/])\+?\(?\d[\d\s().-]{8,17}\d(?![\w/])")
LINKEDIN_RE = re.compile(r"linkedin\.com/in/[A-Za-z0-9_%-]+", re.I)
GITHUB_RE = re.compile(r"github\.com/[A-Za-z0-9_-]+", re.I)
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_RANGE_RE = re.compile(
    rf"(?:{MONTH}\s*'?\d{{2,4}}|\d{{1,2}}/\d{{2,4}}|\b(?:19|20)\d{{2}})\s*(?:-|to|until)\s*"
    rf"(?:{MONTH}\s*'?\d{{2,4}}|\d{{1,2}}/\d{{2,4}}|(?:19|20)\d{{2}}|present|current|now|ongoing|expected)",
    re.I,
)
BULLET_RE = re.compile(r"^[ \t]*(?:[\u2022\u25cf\u25aa\u25a0\u25e6\u2023\u2043\u27a2\u25ba\u2713\u2714\u2756\u27a4\-\*\u2013\u00b7]|[\ue000-\uf8ff])(?:[ \t]+|$)", re.M)
# Symbol/Wingdings bullet glyphs land in the Unicode private-use area when extracted.
GLYPH_BULLET_RE = re.compile(r"^[ \t]*[\ue000-\uf8ff](?=[ \t]|$)", re.M)
# Points taken off "Text & layout" for layouts that ATS parsers commonly mangle.
LAYOUT_PENALTIES = {"scanned": 40, "multi_column": 10, "textbox": 10, "tables": 5, "header_footer": 5}
LIGATURES = "\ufb00\ufb01\ufb02\ufb03\ufb04\ufb05\ufb06"


# ------------------------------------------------------------------- agreement

def agreement(a: str, b: str) -> dict:
    ta, tb = word_tokens(a), word_tokens(b)
    if not ta and not tb:
        return {"content": 1.0, "order": 1.0}
    if not ta or not tb:
        return {"content": 0.0, "order": 0.0}
    ca, cb = Counter(ta), Counter(tb)
    inter = sum((ca & cb).values())
    content = 2 * inter / (len(ta) + len(tb))
    order = SequenceMatcher(None, ta, tb, autojunk=False).ratio()
    return {"content": round(content, 4), "order": round(order, 4)}


# ------------------------------------------------------------------ cleanliness

def cleanliness(raw: str, pages: int = 1) -> dict:
    """Score 0-100 for how clean the extracted text is, plus the issues found."""
    issues: list[str] = []
    text = raw or ""
    stripped = text.strip()
    if len(stripped) < 100:
        return {
            "score": 0.0,
            "issues": ["Almost no text could be extracted. The file is probably a scanned image or "
                       "flattened graphic, so an ATS would see a blank resume."],
            "details": {"characters": len(stripped)},
        }
    tokens = re.findall(r"\S+", text)
    n_tokens = max(1, len(tokens))
    n_chars = max(1, len(text))
    penalty = 0.0
    details: dict = {"characters": len(stripped), "words": len(tokens)}

    cid = len(re.findall(r"\(cid:\d+\)", text))
    if cid:
        p = min(40.0, max(5.0, 400 * cid / n_tokens))
        penalty += p
        issues.append(f"{cid} unreadable glyph code(s) like '(cid:123)'. The PDF font has no text mapping, "
                      "so those characters are lost for an ATS. Re-export with standard fonts.")
    details["cid_codes"] = cid

    glyph_bullets = len(GLYPH_BULLET_RE.findall(text))
    if glyph_bullets:
        penalty += 2.0
        issues.append(f"{glyph_bullets} bullet(s) use a Symbol/Wingdings font glyph that extracts as an unknown "
                      "character. Most ATS just skip it, but a plain '\u2022' or '-' bullet is safer.")
    details["glyph_bullets"] = glyph_bullets
    body = GLYPH_BULLET_RE.sub("", text)
    bad = [ch for ch in body if ch == "\ufffd" or unicodedata.category(ch) in ("Co", "Cs")
           or (unicodedata.category(ch) == "Cc" and ch not in "\n\r\t\f")]
    if bad:
        ratio = len(bad) / n_chars
        p = min(30.0, max(2.0, 1000 * ratio))
        penalty += p
        issues.append(f"{len(bad)} broken or private-use character(s) (often icon fonts for phone/email "
                      "symbols). Replace icons with plain text labels.")
    details["broken_chars"] = len(bad)

    lig = sum(text.count(ch) for ch in LIGATURES)
    if lig:
        penalty += min(8.0, 1 + lig * 0.5)
        issues.append(f"{lig} ligature glyph(s) (e.g. a single 'fi' character). Some ATS fail to match "
                      "words like 'certified' or 'workflow' because of this.")
    details["ligatures"] = lig

    spaced = re.findall(r"(?<![A-Za-z])(?:[A-Za-z] ){3,}[A-Za-z](?![A-Za-z])", text)
    if spaced:
        penalty += min(20.0, 4 * len(spaced))
        examples = ", ".join(f"'{s}'" for s in spaced[:3])
        issues.append(f"Letter-spaced text detected ({examples}). ATS reads these as separate letters, "
                      "so headings like 'S K I L L S' are not recognized.")
    details["letter_spaced_runs"] = len(spaced)

    long_words = [t for t in re.findall(r"[A-Za-z]{22,}", text)
                  if not re.search(r"https?|www|\.com|@", t, re.I)]
    if long_words:
        penalty += min(15.0, 3 * len(long_words))
        issues.append(f"{len(long_words)} run-together word(s) such as '{long_words[0][:40]}'. Missing "
                      "spaces break keyword matching.")
    details["run_together_words"] = len(long_words)

    hyph = len(re.findall(r"[a-z]-\n[a-z]", text))
    if hyph:
        penalty += min(5.0, hyph)
        issues.append(f"{hyph} word(s) hyphenated across line breaks. Turn off auto-hyphenation.")
    details["hyphen_breaks"] = hyph

    if pages and len(stripped) / pages < 400:
        penalty += 10
        issues.append("Very little text per page. Part of the resume may be images or graphics.")

    return {"score": round(max(0.0, 100 - penalty), 1), "issues": issues, "details": details}


# -------------------------------------------------------------------- structure

def _match_heading(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 50:
        return None
    candidates = [s]
    if ":" in s:
        candidates.append(s.split(":", 1)[0])
    for cand in candidates:
        key = re.sub(r"[^a-z& ]", "", normalize(cand).lower()).strip()
        key = re.sub(r"\s+", " ", key)
        for section, variants in SECTION_HEADINGS.items():
            if key in variants:
                return section
    return None


def structure(text: str) -> dict:
    text = normalize(text)
    sections: dict[str, str] = {}
    for line in text.splitlines():
        sec = _match_heading(line)
        if sec and sec not in sections:
            sections[sec] = line.strip()
    phones = [p for p in PHONE_RE.findall(text) if 10 <= len(re.sub(r"\D", "", p)) <= 13]
    emails = EMAIL_RE.findall(text)
    linkedin = LINKEDIN_RE.findall(text)
    github = GITHUB_RE.findall(text)
    words = re.findall(r"\S+", text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return {
        "email": emails[0] if emails else "",
        "phone": phones[0].strip() if phones else "",
        "linkedin": linkedin[0] if linkedin else "",
        "github": github[0] if github else "",
        "sections_found": list(sections.keys()),
        "section_headings": sections,
        "missing_essential": [s for s in ESSENTIAL_SECTIONS if s not in sections],
        "date_ranges": len(DATE_RANGE_RE.findall(text)),
        "bullets": len(BULLET_RE.findall(text)),
        "word_count": len(words),
        "line_count": len(lines),
    }


def structure_score(st: dict) -> float:
    score = 0.0
    score += 15 if st["email"] else 0
    score += 15 if st["phone"] else 0
    score += 5 if st["linkedin"] else 0
    found = set(st["sections_found"])
    score += 15 * sum(1 for s in ESSENTIAL_SECTIONS if s in found)  # 45
    score += 10 if (found - set(ESSENTIAL_SECTIONS)) else 0
    score += 10 if st["date_ranges"] >= 1 else 0
    return min(100.0, score)


# --------------------------------------------------------------- reference check

def _levenshtein(a: list[str], b: list[str]) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def verify_against_reference(extracted: str, reference: str) -> dict:
    """Exact accuracy numbers versus a trusted copy of the resume text."""
    te, tr = word_tokens(extracted)[:6000], word_tokens(reference)[:6000]
    if not tr:
        return {}
    ce, cr = Counter(te), Counter(tr)
    inter = sum((ce & cr).values())
    recall = inter / len(tr)
    precision = inter / len(te) if te else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    wer = _levenshtein(tr, te) / len(tr)
    missing = list((cr - ce).elements())
    extra = list((ce - cr).elements())
    ref_skills, ext_skills = set(find_skills(reference)), set(find_skills(extracted))
    kw_recall = len(ref_skills & ext_skills) / len(ref_skills) if ref_skills else 1.0
    word_accuracy = max(0.0, 1 - wer)
    verified = 100 * (0.45 * f1 + 0.20 * word_accuracy + 0.35 * kw_recall)
    return {
        "verified_accuracy": round(verified, 1),
        "word_recall": round(100 * recall, 1),
        "word_precision": round(100 * precision, 1),
        "word_f1": round(100 * f1, 1),
        "word_order_accuracy": round(100 * word_accuracy, 1),
        "keyword_recall": round(100 * kw_recall, 1),
        "keywords_missing_vs_reference": sorted(ref_skills - ext_skills),
        "missing_words_sample": sorted(set(missing))[:40],
        "extra_words_sample": sorted(set(extra))[:40],
        "reference_words": len(tr),
        "extracted_words": len(te),
    }


# ------------------------------------------------------------------ main entry

def choose_primary(ex: ExtractionResult) -> None:
    """Pick the engine whose output is cleanest and most consistent with the others."""
    usable = ex.usable_engines
    if not usable:
        ex.primary_engine = ex.engines[0].name if ex.engines else ""
        return
    if len(usable) == 1:
        ex.primary_engine = usable[0].name
        return
    best, best_score = None, -1.0
    for e in usable:
        others = [o for o in usable if o is not e]
        agree = sum((lambda a: 0.7 * a["content"] + 0.3 * a["order"])(agreement(e.text, o.text))
                    for o in others) / len(others)
        score = 0.5 * cleanliness(e.text, ex.pages)["score"] / 100 + 0.5 * agree
        score -= ENGINE_PRIORITY.index(e.name) * 1e-4 if e.name in ENGINE_PRIORITY else 0
        if score > best_score:
            best, best_score = e, score
    ex.primary_engine = best.name


def extraction_accuracy(ex: ExtractionResult, reference_text: str | None = None) -> dict:
    choose_primary(ex)
    primary = ex.primary_text
    usable = ex.usable_engines
    clean = cleanliness(primary, ex.pages)
    layout_penalty = sum(LAYOUT_PENALTIES.get(f, 0) for f in ex.flags)
    clean_layout = max(0.0, clean["score"] - layout_penalty)
    st = structure(primary)
    st_score = structure_score(st)

    # 1. engine agreement
    comparisons = []
    for e in usable:
        if e.name == ex.primary_engine:
            continue
        a = agreement(primary, e.text)
        comparisons.append({"engine": e.name, "content_agreement": round(100 * a["content"], 1),
                            "order_agreement": round(100 * a["order"], 1),
                            "words": len(word_tokens(e.text))})
    if comparisons:
        agree_score = sum(0.7 * c["content_agreement"] + 0.3 * c["order_agreement"] for c in comparisons) / len(comparisons)
    else:
        agree_score = None

    # 4. keyword integrity across engines
    primary_skills = set(find_skills(primary))
    union_skills = set(primary_skills)
    lost_by_engine: dict[str, list[str]] = {}
    for e in usable:
        if e.name == ex.primary_engine:
            continue
        s = set(find_skills(e.text))
        union_skills |= s
        if s - primary_skills:
            lost_by_engine[e.name] = sorted(s - primary_skills)
    missing_from_primary = sorted(union_skills - primary_skills)
    kw_integrity = 100.0 * len(primary_skills) / len(union_skills) if union_skills else (100.0 if primary.strip() else 0.0)

    # Losing words or keywords hurts an ATS more than linearly: each point of
    # disagreement or keyword loss costs two points of accuracy.
    def amplify(x):
        return None if x is None else max(0.0, 100 - 2 * (100 - x))

    components = [
        ("Engine agreement", 35, amplify(agree_score)),
        ("Text & layout", 25, clean_layout),
        ("Structure detected", 20, st_score),
        ("Keyword integrity", 20, amplify(kw_integrity)),
    ]
    present = [(n, w, s) for n, w, s in components if s is not None]
    total_w = sum(w for _, w, _ in present)
    estimated = sum(w * s for _, w, s in present) / total_w if total_w else 0.0
    if not primary.strip():
        estimated = 0.0

    out = {
        "primary_engine": ex.primary_engine,
        "engines_used": [e.name for e in usable],
        "engines_failed": {e.name: e.error for e in ex.engines if not e.ok},
        "estimated_accuracy": round(estimated, 1),
        "grade": grade(estimated),
        "components": [{"name": n, "weight": w, "score": None if s is None else round(s, 1)} for n, w, s in components],
        "engine_comparisons": comparisons,
        "cleanliness": clean,
        "structure": st,
        "keyword_integrity": {
            "score": round(kw_integrity, 1),
            "found_in_primary": len(primary_skills),
            "found_by_any_engine": len(union_skills),
            "missing_from_primary": missing_from_primary,
            "only_seen_by": lost_by_engine,
        },
        "layout_notes": ex.layout_notes,
        "layout_flags": sorted(ex.flags),
        "layout_penalty": layout_penalty,
        "hidden_links": [l for l in ex.links if not _link_visible(l, primary)],
    }
    if reference_text and reference_text.strip():
        ref = verify_against_reference(primary, reference_text)
        if ref:
            out["reference_check"] = ref
    return out


def _link_visible(link: str, text: str) -> bool:
    core = re.sub(r"^(mailto:|tel:|https?://)(www\.)?", "", link, flags=re.I).rstrip("/").lower()
    return not core or core in normalize(text).lower().replace(" ", "")


def grade(score: float) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Fair"
    return "Poor"

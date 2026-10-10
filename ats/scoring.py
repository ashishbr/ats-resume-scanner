"""ATS scoring: job-match score when a JD is given, readiness score when it isn't."""
from __future__ import annotations

import math
import re
from collections import Counter

from .keywords import (JDAnalysis, analyze_jd, degree_levels, find_skills, phrase_in_text,
                       title_match)
from .skills_db import ACTION_VERBS, SOFT_SKILL_CATEGORY, WEAK_PHRASES
from .text_utils import content_terms, normalize, stemmed_string


def _weighted(components: list[tuple[str, float, float | None]]) -> float:
    present = [(w, s) for _, w, s in components if s is not None]
    tw = sum(w for w, _ in present)
    return sum(w * s for w, s in present) / tw if tw else 0.0


def cosine_similarity(a: str, b: str) -> float:
    ca, cb = Counter(content_terms(a)), Counter(content_terms(b))
    if not ca or not cb:
        return 0.0
    dot = sum(ca[t] * cb[t] for t in ca.keys() & cb.keys())
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb)


_BULLET_START = re.compile(r"^[ \t]*(?:[\u2022\u25cf\u25aa\u25a0\u25e6\u2023\u2043\u27a2\u25ba\u2713\u2714\u2756\u27a4\-\*\u2013\u00b7]|[\ue000-\uf8ff])[ \t]*")


def content_lines(text: str) -> list[str]:
    """Bullet points if the resume has them, otherwise sentence-like lines."""
    raw = [ln.strip() for ln in text.splitlines()]
    bullets, take_next = [], False
    for ln in raw:
        if not ln:
            continue
        m = _BULLET_START.match(ln)
        rest = ln[m.end():].strip() if m else ln
        if m and not rest:
            take_next = True  # bullet glyph alone on its line, text follows
            continue
        if m or take_next:
            if len(rest.split()) >= 4:
                bullets.append(rest)
            take_next = False
    if len(bullets) >= 3:
        return bullets
    return [ln for ln in raw if len(ln.split()) >= 6 and "@" not in ln and "|" not in ln
            and ":" not in ln[:25] and not ln.endswith(":")]


def bullet_quality(text: str) -> dict:
    text = normalize(text)
    lines = content_lines(text)
    if not lines:
        return {"lines": 0, "quantified": 0, "action_led": 0, "weak_phrases": [], "quantified_ratio": 0.0, "action_ratio": 0.0}
    quant = [ln for ln in lines if re.search(r"\d+\s*%|\$\s?\d|\d+\s*(?:x|k|m|ms|s|hrs?|hours|users|customers|clients|requests|tps|qps)\b|\b\d{2,}\b", ln, re.I)]
    action = []
    for ln in lines:
        first = re.sub(r"^[^A-Za-z]+", "", ln).split(" ", 1)[0].lower().strip(",.:;")
        if first in ACTION_VERBS:
            action.append(ln)
    weak = sorted({p for p in WEAK_PHRASES if re.search(rf"\b{re.escape(p)}\b", text, re.I)})
    return {
        "lines": len(lines),
        "quantified": len(quant),
        "action_led": len(action),
        "weak_phrases": weak,
        "quantified_ratio": round(len(quant) / len(lines), 3),
        "action_ratio": round(len(action) / len(lines), 3),
    }


# ----------------------------------------------------------------- with JD

def match_score(resume_text: str, jd_text: str, extraction: dict) -> dict:
    jd: JDAnalysis = analyze_jd(jd_text)
    resume_hits = find_skills(resume_text)
    resume_stemmed = stemmed_string(resume_text)
    resume_terms = set(resume_stemmed.split())

    def coverage(terms, matcher):
        total = sum(t.weight for t in terms)
        got, rows = 0.0, []
        for t in terms:
            m = matcher(t)
            got += t.weight * m
            rows.append({"term": t.name, "weight": t.weight, "section": t.section, "jd_count": t.count,
                         "match": m, "resume_count": resume_hits[t.name].count if t.name in resume_hits else 0})
        return (100 * got / total if total else None), rows

    hard_score, hard_rows = coverage(jd.hard_skills, lambda t: 1.0 if t.name in resume_hits else 0.0)
    soft_score, soft_rows = coverage(jd.soft_skills, lambda t: 1.0 if t.name in resume_hits else 0.0)
    phrase_score, phrase_rows = coverage(jd.phrases, lambda t: phrase_in_text(t.name, resume_stemmed, resume_terms))

    cos = cosine_similarity(resume_text, jd_text)
    # Raw cosine between a resume and a JD rarely exceeds ~0.5 even for strong fits; rescale 0.05-0.45 to 0-100.
    sim_score = max(0.0, min(100.0, (cos - 0.05) / 0.40 * 100))

    t_match = title_match(jd.title, resume_text)
    title_score = None if t_match is None else 100 * t_match

    edu_score = None
    resume_levels = degree_levels(resume_text)
    if jd.degree_level:
        top = max(resume_levels) if resume_levels else 0
        edu_score = 100.0 if top >= jd.degree_level else (50.0 if top else 0.0)

    parse_score = extraction["estimated_accuracy"]
    if extraction.get("reference_check"):
        parse_score = extraction["reference_check"]["verified_accuracy"]

    components = [
        ("Hard skills match", 45, hard_score),
        ("Key phrases from JD", 10, phrase_score),
        ("Soft skills match", 5, soft_score),
        ("Content similarity", 15, sim_score),
        ("Job title match", 10, title_score),
        ("Education requirement", 5, edu_score),
        ("Parseability", 10, parse_score),
    ]
    score = _weighted(components)

    matched = [r["term"] for r in hard_rows if r["match"]]
    missing = [r for r in hard_rows if not r["match"]]
    missing_required = [r["term"] for r in missing if r["section"] == "required"]
    missing_other = [r["term"] for r in missing if r["section"] != "required"]

    return {
        "mode": "job_match",
        "score": round(score, 1),
        "grade": _grade(score),
        "components": [{"name": n, "weight": w, "score": None if s is None else round(s, 1)} for n, w, s in components],
        "jd_title": jd.title,
        "jd_years_required": sorted(set(jd.years_required)),
        "jd_degree": jd.degree_text,
        "resume_degree_levels": sorted(resume_levels),
        "cosine_similarity": round(cos, 3),
        "hard_skills": hard_rows,
        "soft_skills": soft_rows,
        "phrases": phrase_rows,
        "matched_hard_skills": matched,
        "missing_required_skills": missing_required,
        "missing_other_skills": missing_other,
        "missing_soft_skills": [r["term"] for r in soft_rows if not r["match"]],
        "missing_phrases": [r["term"] for r in phrase_rows if r["match"] == 0],
        "keyword_match_ratio": f"{len(matched)}/{len(hard_rows)}",
        "resume_skills": _skills_summary(resume_hits),
        "bullets": bullet_quality(resume_text),
    }


# ------------------------------------------------------------- without JD

def readiness_score(resume_text: str, extraction: dict) -> dict:
    st = extraction["structure"]
    hits = find_skills(resume_text)
    hard = [h for h in hits.values() if h.category != SOFT_SKILL_CATEGORY]
    bq = bullet_quality(resume_text)

    parse_score = extraction["estimated_accuracy"]
    if extraction.get("reference_check"):
        parse_score = extraction["reference_check"]["verified_accuracy"]

    found = set(st["sections_found"])
    section_score = 100 * (sum(1 for s in ("Experience", "Education", "Skills") if s in found) / 3) * 0.8
    section_score += 20 if found & {"Projects", "Summary", "Certifications"} else 0

    contact = (40 if st["email"] else 0) + (40 if st["phone"] else 0) + (20 if st["linkedin"] else 0)
    skills_depth = min(100.0, 100 * len(hard) / 15)
    impact = min(100.0, 100 * (0.6 * min(1.0, bq["quantified_ratio"] / 0.4) + 0.4 * min(1.0, bq["action_ratio"] / 0.5)))
    impact = max(0.0, impact - 5 * len(bq["weak_phrases"]))

    wc = st["word_count"]
    if 350 <= wc <= 900:
        length = 100.0
    elif wc < 350:
        length = max(0.0, 100 * wc / 350)
    else:
        length = max(40.0, 100 - (wc - 900) / 10)
    length = 0.7 * length + 0.3 * min(100.0, 100 * st["bullets"] / 8)

    components = [
        ("Parseability", 30, parse_score),
        ("Standard sections", 20, section_score),
        ("Contact details", 10, contact),
        ("Skills depth", 15, skills_depth),
        ("Impact (metrics, action verbs)", 15, impact),
        ("Length & bullet structure", 10, length),
    ]
    score = _weighted(components)
    return {
        "mode": "readiness",
        "score": round(score, 1),
        "grade": _grade(score),
        "components": [{"name": n, "weight": w, "score": round(s, 1)} for n, w, s in components],
        "resume_skills": _skills_summary(hits),
        "hard_skill_count": len(hard),
        "bullets": bq,
    }


def _skills_summary(hits) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for h in sorted(hits.values(), key=lambda h: (h.category, h.name.lower())):
        out.setdefault(h.category, []).append(h.name)
    return out


def _grade(score: float) -> str:
    if score >= 80:
        return "Strong"
    if score >= 65:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Weak"


# --------------------------------------------------------- recommendations

def recommendations(extraction: dict, ats: dict) -> list[str]:
    recs: list[str] = []
    st = extraction["structure"]
    if extraction["estimated_accuracy"] < 75:
        recs.append("Extraction accuracy is low. Use a single-column layout, standard fonts, no text boxes, "
                    "and export to PDF from Word/Google Docs (not 'Print to PDF' from a design tool).")
    recs.extend(extraction["cleanliness"]["issues"])
    recs.extend(extraction["layout_notes"])
    ki = extraction["keyword_integrity"]
    if ki["missing_from_primary"]:
        recs.append("Some skills were read by one parser but lost by another: "
                    + ", ".join(ki["missing_from_primary"]) + ". Move them into plain body text.")
    if extraction.get("hidden_links"):
        recs.append("Links only stored as hyperlinks (not visible text): "
                    + ", ".join(extraction["hidden_links"][:4]) + ". Write the URL out so an ATS can read it.")
    if not st["email"]:
        recs.append("No email address detected in the extracted text.")
    if not st["phone"]:
        recs.append("No phone number detected in the extracted text.")
    if not st["linkedin"]:
        recs.append("Add your LinkedIn URL as plain text (linkedin.com/in/yourname).")
    for sec in st["missing_essential"]:
        recs.append(f"No '{sec}' heading recognized. Use a standard heading such as '{sec.upper()}'.")
    if ats["mode"] == "job_match":
        if ats["missing_required_skills"]:
            recs.append("Missing skills from the JD's requirements (add only if you genuinely have them): "
                        + ", ".join(ats["missing_required_skills"][:15]) + ".")
        if ats["missing_other_skills"]:
            recs.append("Other JD skills not found: " + ", ".join(ats["missing_other_skills"][:15]) + ".")
        if ats["missing_phrases"]:
            recs.append("JD themes not reflected in your resume: " + ", ".join(ats["missing_phrases"][:8]) + ".")
        title_comp = next(c for c in ats["components"] if c["name"] == "Job title match")
        if ats["jd_title"] and title_comp["score"] is not None and title_comp["score"] < 60:
            recs.append(f"Mirror the target title '{ats['jd_title']}' in your summary or headline.")
        edu = next(c for c in ats["components"] if c["name"] == "Education requirement")
        if edu["score"] is not None and edu["score"] < 100:
            recs.append(f"JD asks for a {ats['jd_degree']} degree; make sure your degree is spelled out "
                        "(e.g. 'Master of Science' or 'M.S.').")
    else:
        if ats["hard_skill_count"] < 10:
            recs.append("Few recognizable hard skills. Add a dedicated 'Skills' section listing tools and technologies.")
    bq = ats["bullets"]
    if bq["lines"] and bq["quantified_ratio"] < 0.3:
        recs.append(f"Only {bq['quantified']} of {bq['lines']} content lines include a number. Quantify impact "
                    "(%, $, time saved, users, scale).")
    if bq["lines"] and bq["action_ratio"] < 0.4:
        recs.append("Start more bullets with strong action verbs (Built, Designed, Reduced, Automated...).")
    if bq["weak_phrases"]:
        recs.append("Replace weak phrasing: " + ", ".join(f"'{p}'" for p in bq["weak_phrases"]) + ".")
    wc = st["word_count"]
    if wc < 300:
        recs.append(f"Resume is short ({wc} words). Most strong one-page resumes land between 400 and 750 words.")
    elif wc > 1100:
        recs.append(f"Resume is long ({wc} words). Trim to the most relevant content.")
    seen, out = set(), []
    for r in recs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out

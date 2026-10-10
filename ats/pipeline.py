"""One call that runs the whole scan and writes the output files."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .extract import extract
from .keywords import find_skills, skill_context
from .quality import extraction_accuracy
from .scoring import match_score, readiness_score, recommendations

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "output"


def analyze(data: bytes, filename: str, jd_text: str | None = None, reference_text: str | None = None,
            out_dir: str | Path | None = DEFAULT_OUT, save: bool = True) -> dict:
    ex = extract(data, filename)
    acc = extraction_accuracy(ex, reference_text)
    text = ex.primary_text
    has_jd = bool(jd_text and jd_text.strip())
    ats = match_score(text, jd_text, acc) if has_jd else readiness_score(text, acc)
    recs = recommendations(acc, ats)

    hits = find_skills(text)
    keyword_audit = [
        {"skill": h.name, "category": h.category, "count": h.count,
         "as_written": sorted({" ".join(f.split()) for f in h.forms}),
         "context": skill_context(text, h)}
        for h in sorted(hits.values(), key=lambda h: (h.category, h.name.lower()))
    ]

    result = {
        "file": filename,
        "file_type": ex.file_type,
        "pages": ex.pages,
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        "has_job_description": has_jd,
        "extraction": acc,
        "ats": ats,
        "keyword_audit": keyword_audit,
        "recommendations": recs,
        "extracted_text": text,
        "engine_texts": {e.name: e.text for e in ex.usable_engines},
    }
    if save and out_dir:
        result["output_files"] = save_outputs(result, Path(out_dir), jd_text)
    return result


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("_") or "resume"


def save_outputs(result: dict, out_dir: Path, jd_text: str | None = None) -> dict:
    stem = _slug(result["file"])
    folder = out_dir / f"{stem}_{datetime.now():%Y%m%d_%H%M%S}"
    (folder / "engines").mkdir(parents=True, exist_ok=True)
    files = {}

    p = folder / f"{stem}_extracted.txt"
    p.write_text(result["extracted_text"], encoding="utf-8")
    files["extracted_text"] = str(p)

    for engine, text in result["engine_texts"].items():
        ep = folder / "engines" / f"{stem}_{engine}.txt"
        ep.write_text(text, encoding="utf-8")
    files["engine_texts"] = str(folder / "engines")

    if jd_text and jd_text.strip():
        jp = folder / "job_description.txt"
        jp.write_text(jd_text, encoding="utf-8")
        files["job_description"] = str(jp)

    rp = folder / f"{stem}_ats_report.txt"
    rp.write_text(text_report(result), encoding="utf-8")
    files["report_txt"] = str(rp)

    jp = folder / f"{stem}_ats_report.json"
    slim = {k: v for k, v in result.items() if k not in ("engine_texts", "extracted_text", "output_files")}
    jp.write_text(json.dumps(slim, indent=2, default=list), encoding="utf-8")
    files["report_json"] = str(jp)
    files["folder"] = str(folder)
    return files


def _bar(score: float | None, width: int = 20) -> str:
    if score is None:
        return "n/a".ljust(width + 2)
    filled = int(round(score / 100 * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def text_report(r: dict) -> str:
    acc, ats = r["extraction"], r["ats"]
    st = acc["structure"]
    L: list[str] = []
    rule = "=" * 72
    L += [rule, "ATS RESUME SCAN REPORT", rule,
          f"File:            {r['file']} ({r['file_type'].upper()}, {r['pages']} page(s))",
          f"Analyzed:        {r['analyzed_at']}",
          f"Job description: {'provided' if r['has_job_description'] else 'not provided (general readiness check)'}",
          ""]

    L += ["-" * 72, "1. EXTRACTION ACCURACY", "-" * 72,
          f"Estimated accuracy: {acc['estimated_accuracy']}/100  ({acc['grade']})",
          f"Primary engine:     {acc['primary_engine']}  (text saved to *_extracted.txt)",
          f"Engines compared:   {', '.join(acc['engines_used'])}"]
    if acc["engines_failed"]:
        L.append("Engines failed:     " + "; ".join(f"{k} ({v})" for k, v in acc["engines_failed"].items()))
    L.append("")
    for c in acc["components"]:
        s = c["score"]
        L.append(f"  {c['name']:<22} {_bar(s)} {('%5.1f' % s) if s is not None else '  n/a'}  (weight {c['weight']})")
    if acc["engine_comparisons"]:
        L += ["", "  Engine agreement vs primary:"]
        for c in acc["engine_comparisons"]:
            L.append(f"    {c['engine']:<12} content {c['content_agreement']:5.1f}%   reading order {c['order_agreement']:5.1f}%   ({c['words']} words)")
    ref = acc.get("reference_check")
    if ref:
        L += ["", f"  VERIFIED against your reference text: {ref['verified_accuracy']}/100",
              f"    word recall {ref['word_recall']}%  precision {ref['word_precision']}%  "
              f"order accuracy {ref['word_order_accuracy']}%  keyword recall {ref['keyword_recall']}%"]
        if ref["keywords_missing_vs_reference"]:
            L.append("    keywords lost vs reference: " + ", ".join(ref["keywords_missing_vs_reference"]))
        if ref["missing_words_sample"]:
            L.append("    words missing (sample): " + ", ".join(ref["missing_words_sample"][:20]))
    L += ["", "  Structure detected:",
          f"    Email:    {st['email'] or 'NOT FOUND'}",
          f"    Phone:    {st['phone'] or 'NOT FOUND'}",
          f"    LinkedIn: {st['linkedin'] or 'NOT FOUND'}",
          f"    GitHub:   {st['github'] or '-'}",
          f"    Sections: {', '.join(st['sections_found']) or 'none recognized'}",
          f"    Date ranges: {st['date_ranges']}   Bullets: {st['bullets']}   Words: {st['word_count']}"]
    ki = acc["keyword_integrity"]
    L += ["", f"  Keyword integrity: {ki['found_in_primary']} of {ki['found_by_any_engine']} skills found by any engine are in the primary text"]
    if ki["missing_from_primary"]:
        L.append("    lost in primary: " + ", ".join(ki["missing_from_primary"]))
    issues = acc["cleanliness"]["issues"] + acc["layout_notes"]
    if issues:
        L += ["", "  Issues:"] + [f"    - {i}" for i in issues]

    L += ["", "-" * 72]
    if ats["mode"] == "job_match":
        L += ["2. ATS MATCH SCORE (resume vs job description)", "-" * 72,
              f"Match score: {ats['score']}/100  ({ats['grade']})",
              f"JD title: {ats['jd_title'] or '(not detected)'}   Hard-skill keywords matched: {ats['keyword_match_ratio']}",
              f"Content similarity (cosine): {ats['cosine_similarity']}"]
        if ats["jd_years_required"]:
            L.append("JD experience asks: " + ", ".join(ats["jd_years_required"]))
        if ats["jd_degree"]:
            L.append(f"JD degree: {ats['jd_degree']}")
        L.append("")
        for c in ats["components"]:
            s = c["score"]
            L.append(f"  {c['name']:<22} {_bar(s)} {('%5.1f' % s) if s is not None else '  n/a'}  (weight {c['weight']})")
        L += ["", "  JD hard skills:", f"    {'Skill':<30}{'JD section':<12}{'Weight':>7}{'In resume':>12}"]
        for row in ats["hard_skills"]:
            mark = f"yes ({row['resume_count']}x)" if row["match"] else "MISSING"
            L.append(f"    {row['term'][:29]:<30}{row['section']:<12}{row['weight']:>7.2f}{mark:>12}")
        if ats["soft_skills"]:
            L += ["", "  JD soft skills: " + ", ".join(
                f"{r['term']} ({'yes' if r['match'] else 'missing'})" for r in ats["soft_skills"])]
        if ats["phrases"]:
            L += ["", "  JD key phrases: " + ", ".join(
                f"{r['term']} ({'yes' if r['match'] == 1 else 'partial' if r['match'] else 'missing'})" for r in ats["phrases"])]
    else:
        L += ["2. ATS READINESS SCORE (no job description)", "-" * 72,
              f"Readiness score: {ats['score']}/100  ({ats['grade']})", ""]
        for c in ats["components"]:
            L.append(f"  {c['name']:<32} {_bar(c['score'])} {c['score']:5.1f}  (weight {c['weight']})")
    bq = ats["bullets"]
    L += ["", f"  Content lines: {bq['lines']}   with numbers: {bq['quantified']}   starting with action verb: {bq['action_led']}"]

    L += ["", "-" * 72, "3. KEYWORDS EXTRACTED FROM YOUR RESUME (verify each match)", "-" * 72]
    cat = None
    for k in r["keyword_audit"]:
        if k["category"] != cat:
            cat = k["category"]
            L += ["", f"  [{cat}]"]
        L.append(f"    {k['skill']:<24} x{k['count']:<3} as '{', '.join(k['as_written'])}'")
        L.append(f"        found in: {k['context']}")

    L += ["", "-" * 72, "4. RECOMMENDATIONS", "-" * 72]
    L += [f"  {i}. {rec}" for i, rec in enumerate(r["recommendations"], 1)] or ["  None. Looks good."]
    L += ["", rule]
    return "\n".join(L) + "\n"

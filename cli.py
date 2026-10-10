#!/usr/bin/env python3
"""Command-line ATS scanner.

Examples
  python cli.py my_resume.pdf
  python cli.py my_resume.pdf --jd job.txt
  python cli.py my_resume.pdf --jd-text "We need Python, AWS and Kafka..."
  python cli.py my_resume.docx --jd job.txt --reference my_resume_source.txt
  python cli.py resumes/*.pdf --jd job.txt          (ranks several resumes against one JD)
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from ats.pipeline import DEFAULT_OUT, analyze, text_report


def main() -> int:
    ap = argparse.ArgumentParser(description="Local ATS resume scanner")
    ap.add_argument("resumes", nargs="+", help="Resume file(s): PDF, DOCX or TXT (wildcards OK)")
    ap.add_argument("--jd", help="Job description file (.txt, .pdf or .docx)")
    ap.add_argument("--jd-text", help="Job description pasted as a string")
    ap.add_argument("--reference", help="Plain-text copy of the resume you trust, for a verified accuracy check")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output folder (default: ./output)")
    ap.add_argument("--quiet", action="store_true", help="Print only the summary line per resume")
    args = ap.parse_args()

    files: list[Path] = []
    for pattern in args.resumes:
        matches = glob.glob(pattern)
        files.extend(Path(m) for m in (matches or [pattern]))

    jd_text = args.jd_text or ""
    if args.jd:
        jd_path = Path(args.jd)
        if jd_path.suffix.lower() in (".pdf", ".docx"):
            from ats.extract import extract_path
            from ats.quality import choose_primary
            jd_ex = extract_path(jd_path)
            choose_primary(jd_ex)
            jd_text = jd_ex.primary_text
        else:
            jd_text = jd_path.read_text(encoding="utf-8", errors="replace")
    reference = Path(args.reference).read_text(encoding="utf-8", errors="replace") if args.reference else None

    results = []
    for f in files:
        if not f.exists():
            print(f"[skip] {f} not found", file=sys.stderr)
            continue
        try:
            r = analyze(f.read_bytes(), f.name, jd_text=jd_text, reference_text=reference, out_dir=args.out)
        except ValueError as exc:
            print(f"[skip] {f.name}: {exc}", file=sys.stderr)
            continue
        results.append(r)
        if not args.quiet:
            print(text_report(r))
            print(f"Saved to: {r['output_files']['folder']}\n")

    if not results:
        return 1
    label = "Match" if jd_text.strip() else "Readiness"
    print(f"{'Resume':<40}{'Extraction acc.':>16}{label + ' score':>16}{('Keywords' if jd_text.strip() else 'Skills'):>10}")
    for r in sorted(results, key=lambda r: -r["ats"]["score"]):
        acc = r["extraction"]
        shown = acc.get("reference_check", {}).get("verified_accuracy", acc["estimated_accuracy"])
        kw = r["ats"].get("keyword_match_ratio", str(r["ats"].get("hard_skill_count", "")))
        print(f"{r['file'][:39]:<40}{shown:>15.1f}%{r['ats']['score']:>15.1f}%{kw:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

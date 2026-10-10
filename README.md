# ATS Resume Scanner (local version)

Reads your resume the way an Applicant Tracking System does, saves the extracted text to a `.txt` file, checks whether the text and keywords came through intact, and scores the resume with or without a job description.

Runs 100% on your machine. No API keys, no uploads, no LLM.

## What you get

- **Extracted text** saved as `<resume>_extracted.txt`, plus the raw output of every parser in `engines/`
- **Extraction accuracy score (0-100)**: did the text, sections, contact details and keywords survive parsing?
- **Verified accuracy** (optional): paste a trusted copy of your resume text and get exact word recall, precision, reading-order accuracy and keyword recall
- **ATS match score** when you add a job description: weighted hard-skill match (required vs preferred), JD key phrases, soft skills, content similarity, job title, degree requirement, parseability
- **ATS readiness score** without a job description: parseability, standard sections, contact details, skills depth, quantified impact, length
- **Keyword audit**: every keyword found, how it was written, and the exact line it came from, so you can confirm each one was extracted correctly
- **Recommendations** and a full text + JSON report

## Quick start

You need **Python 3.9+** ([python.org/downloads](https://www.python.org/downloads/); on Windows tick "Add python.exe to PATH").

**Windows:** double-click `run_windows.bat`

**macOS / Linux:**
```bash
chmod +x run_mac_linux.sh
./run_mac_linux.sh
```

The first run creates a `.venv` folder and installs dependencies (about a minute). Your browser then opens at `http://localhost:8501`. Press `Ctrl+C` in the terminal to stop.

**Manual setup** (any OS):
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python launch.py            # or: streamlit run app.py
```

## Using it

### Web app
1. Upload your resume (PDF, DOCX or TXT).
2. Optional: paste or upload a job description.
3. Optional: open *Verify accuracy* and paste your resume's real text (copy it from the Word/Google Doc you exported from).
4. Click **Scan resume**. Results are saved to `output/` and every file is downloadable from the *Downloads* tab.

### Command line
```bash
python cli.py my_resume.pdf                                   # readiness check, no JD
python cli.py my_resume.pdf --jd job.txt                      # match against a JD (txt, pdf or docx)
python cli.py my_resume.pdf --jd-text "Python, AWS, Kafka..."  # JD as a string
python cli.py my_resume.pdf --jd job.txt --reference my_resume_source.txt   # verified accuracy
python cli.py resumes/*.pdf --jd job.txt --quiet              # rank several resumes for one JD
```

Try it right away with the bundled samples (a fictional candidate):
```bash
python cli.py samples/sample_resume.pdf samples/sample_resume_two_column.pdf samples/sample_resume_header_textbox.docx --jd samples/sample_jd.txt --quiet
```

## Output files

```
output/<resume>_<timestamp>/
  <resume>_extracted.txt      text from the primary parser (what an ATS most likely sees)
  <resume>_ats_report.txt     human-readable report
  <resume>_ats_report.json    every number, for scripting
  job_description.txt         copy of the JD used (if any)
  engines/                    raw text from each parser, for side-by-side comparison
```

## How extraction accuracy is measured

There is no ground truth inside a PDF, so the scanner runs **several independent parsers** and checks how much they agree. If PyMuPDF, pdfplumber and pypdf all read the same words in the same order, an ATS will too. Where they disagree, the layout is the risk.

| Component | Weight | What it checks |
|---|---|---|
| Engine agreement | 35 | Same words (content) and same sequence (reading order) across parsers. Each point of disagreement costs 2 points. |
| Text & layout | 25 | `(cid:x)` glyph codes, broken or icon-font characters, ligatures, letter-spaced headings like `S K I L L S`, run-together words, hyphenation, near-empty pages. Deductions for multi-column layouts, tables, text boxes, header/footer content and scanned pages. |
| Structure detected | 20 | Email, phone, LinkedIn, standard section headings (Experience, Education, Skills...), date ranges |
| Keyword integrity | 20 | Every skill found by *any* parser must also be in the primary text. Each point lost costs 2 points. |

Grades: 90+ Excellent, 75-89 Good, 60-74 Fair, under 60 Poor.

Parsers used: **PDF** PyMuPDF, pdfplumber, pypdf. **DOCX** python-docx (what a typical ATS reads) vs raw XML (everything, including text boxes). The cleanest, most consistent parser becomes the primary text.

**Verified accuracy** (when you supply reference text): 45% word F1 + 20% reading-order accuracy (1 - word error rate) + 35% keyword recall.

## How the ATS score works

**With a job description**

| Component | Weight |
|---|---|
| Hard skills match (required skills weighted 1.0, general 0.8, preferred 0.5, boosted when repeated) | 45 |
| Content similarity (cosine of stemmed terms) | 15 |
| Key phrases mined from the JD | 10 |
| Job title match | 10 |
| Parseability (extraction accuracy) | 10 |
| Soft skills match | 5 |
| Education requirement | 5 |

**Without a job description:** parseability 30, standard sections 20, skills depth 15, impact (numbers + action verbs) 15, contact details 10, length and bullets 10.

Components that don't apply (for example no degree mentioned in the JD) are dropped and the rest are re-weighted.

## Customize

- **Add keywords** without touching code: edit `custom_skills.txt` (`Name | alias one, alias two`).
- **Full skills taxonomy** (270+ skills, 560+ aliases, e.g. `k8s` = Kubernetes): `ats/skills_db.py`.
- **Weights**: `ats/quality.py` (accuracy) and `ats/scoring.py` (ATS score).

## Project layout

```
app.py            web UI (Streamlit)
cli.py            command line + multi-resume ranking
launch.py         starts the UI on a free port and opens your browser
ats/extract.py    multi-engine PDF/DOCX/TXT extraction, layout detection
ats/quality.py    extraction accuracy, structure, reference verification
ats/keywords.py   skill matching, JD analysis (required/preferred, phrases, title, degree)
ats/scoring.py    match score, readiness score, recommendations
ats/pipeline.py   runs everything, writes .txt/.json reports
ats/skills_db.py  skills taxonomy, action verbs
samples/          fictional resumes and a JD to test with
```

## Limitations

- Scanned (image-only) PDFs are detected and flagged but not OCR'd. Most ATS can't read them either.
- Keyword matching is taxonomy based: precise and explainable, but it only knows skills in the list. Add yours to `custom_skills.txt`.
- Real ATS products (Workday, Greenhouse, Lever, iCIMS, Taleo) each parse differently. This tool tells you how robust your file is across parsers, which is the best proxy available offline.
- Legacy `.doc` files are not supported. Save as `.docx` or PDF.

## Built with reference to

- [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher): mimicking ATS parsing, JD keyword extraction and cosine similarity scoring
- [CodeWithCharan/ATS-Resume-Checker](https://github.com/CodeWithCharan/ATS-Resume-Checker): the upload + JD -> match % + missing keywords flow in Streamlit

Differences: no cloud API or LLM (deterministic and offline), a multi-parser extraction accuracy check, a per-keyword audit trail, and a reference-text verification mode.

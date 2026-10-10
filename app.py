"""Local web UI for the ATS resume scanner.  Run:  streamlit run app.py"""
from __future__ import annotations

import html
import io
import json
import re
import zipfile
from pathlib import Path

import streamlit as st

from ats.extract import extract
from ats.pipeline import DEFAULT_OUT, analyze, text_report
from ats.quality import choose_primary

st.set_page_config(page_title="ATS Resume Scanner", page_icon=":page_facing_up:", layout="wide")

st.markdown(
    """
<style>
.chip{display:inline-block;padding:2px 10px;margin:3px 4px 3px 0;border-radius:999px;font-size:0.85rem;
      border:1px solid rgba(128,128,128,.35)}
.chip.ok{background:rgba(34,160,90,.15);border-color:rgba(34,160,90,.55)}
.chip.miss{background:rgba(220,60,60,.12);border-color:rgba(220,60,60,.55)}
.chip.part{background:rgba(230,160,30,.15);border-color:rgba(230,160,30,.55)}
.chip.neutral{background:rgba(100,120,220,.12);border-color:rgba(100,120,220,.45)}
.hl{white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.82rem;line-height:1.45;
    padding:14px;border-radius:8px;border:1px solid rgba(128,128,128,.25);max-height:560px;overflow:auto}
mark.jd{background:rgba(34,160,90,.35);padding:0 2px;border-radius:3px}
mark.sk{background:rgba(100,120,220,.25);padding:0 2px;border-radius:3px}
.small{opacity:.75;font-size:.85rem}
</style>
""",
    unsafe_allow_html=True,
)


def read_upload_as_text(upload) -> str:
    """JD or reference uploaded as a file: reuse the resume extractor for PDF/DOCX."""
    data = upload.getvalue()
    if upload.name.lower().endswith((".pdf", ".docx")):
        ex = extract(data, upload.name)
        choose_primary(ex)
        return ex.primary_text
    return data.decode("utf-8", errors="replace")


def wide(fn, *args, **kwargs):
    """Full-width element on both new (width='stretch') and older (use_container_width) Streamlit."""
    try:
        return fn(*args, width="stretch", **kwargs)
    except Exception:  # noqa: BLE001  older Streamlit
        return fn(*args, use_container_width=True, **kwargs)


_LINK_RE = re.compile(r"((?:mailto:|tel:|https?://)[^\s,]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)")


def md_safe(text: str) -> str:
    """Show URLs/emails as code so markdown doesn't half-linkify them."""
    return _LINK_RE.sub(lambda m: f"`{m.group(1).rstrip('.')}`" + ("." if m.group(1).endswith(".") else ""), text)


def chips(items, cls) -> str:
    return "".join(f'<span class="chip {cls}">{html.escape(str(i))}</span>' for i in items) or '<span class="small">none</span>'


def highlighted(text: str, audit: list[dict], jd_skills: set[str]) -> str:
    forms: dict[str, str] = {}
    for k in audit:
        cls = "jd" if k["skill"] in jd_skills else "sk"
        for f in k["as_written"]:
            forms.setdefault(f, cls)
    if not forms:
        return f'<div class="hl">{html.escape(text)}</div>'
    pat = re.compile("|".join(rf"(?<![A-Za-z0-9]){re.escape(f).replace(chr(92) + ' ', chr(92) + 's+')}(?![A-Za-z0-9])"
                              for f in sorted(forms, key=len, reverse=True)))
    out, last = [], 0
    for m in pat.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        out.append(f'<mark class="{forms.get(" ".join(m.group(0).split()), "sk")}">{html.escape(m.group(0))}</mark>')
        last = m.end()
    out.append(html.escape(text[last:]))
    return f'<div class="hl">{"".join(out)}</div>'


def bar_rows(components):
    for c in components:
        c1, c2, c3 = st.columns([3, 6, 1.4])
        c1.write(f"{c['name']}  \n<span class='small'>weight {c['weight']}</span>", unsafe_allow_html=True)
        if c["score"] is None:
            c2.caption("not applicable")
            c3.write("n/a")
        else:
            c2.progress(min(1.0, max(0.0, c["score"] / 100)))
            c3.write(f"**{c['score']:.0f}**")


def zip_outputs(r: dict) -> bytes:
    buf = io.BytesIO()
    stem = Path(r["file"]).stem
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{stem}_extracted.txt", r["extracted_text"])
        z.writestr(f"{stem}_ats_report.txt", text_report(r))
        slim = {k: v for k, v in r.items() if k not in ("engine_texts", "extracted_text", "output_files")}
        z.writestr(f"{stem}_ats_report.json", json.dumps(slim, indent=2, default=list))
        for name, text in r["engine_texts"].items():
            z.writestr(f"engines/{stem}_{name}.txt", text)
    return buf.getvalue()


# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    save = st.checkbox("Save results to disk", value=True)
    out_dir = st.text_input("Output folder", value=str(DEFAULT_OUT), disabled=not save)
    st.divider()
    st.markdown(
        "**Runs fully on your machine.** No API keys, nothing leaves your computer.\n\n"
        "**Extraction accuracy** compares 2 to 3 independent parsers, checks for broken characters, "
        "finds contact details and section headings, and confirms every keyword survived extraction.\n\n"
        "Paste a trusted copy of your resume text under *Verify accuracy* for an exact score."
    )

# -------------------------------------------------------------------- input
st.title("ATS Resume Scanner")
st.caption("Extract your resume the way an ATS would, check that the text and keywords came through intact, "
           "and score it with or without a job description.")

left, right = st.columns([1, 1.25])
with left:
    resume = st.file_uploader("Resume", type=["pdf", "docx", "txt"], help="PDF, DOCX or TXT")
    with st.expander("Verify accuracy against a reference (optional)"):
        st.caption("Paste the correct text of your resume (copy it from your Word/Google Doc source). "
                   "The scanner then measures exactly how much of it was extracted.")
        ref_text = st.text_area("Reference text", height=140, label_visibility="collapsed")
        ref_file = st.file_uploader("...or upload it as .txt", type=["txt"], key="ref")
with right:
    jd_text = st.text_area("Job description (optional)", height=230,
                           placeholder="Paste the job description here, or leave empty for a general ATS readiness check.")
    jd_file = st.file_uploader("...or upload the JD", type=["txt", "pdf", "docx"], key="jd")

run = wide(st.button, "Scan resume", type="primary", disabled=resume is None)

if run and resume is not None:
    jd = jd_text.strip() or (read_upload_as_text(jd_file) if jd_file else "")
    ref = ref_text.strip() or (read_upload_as_text(ref_file) if ref_file else "")
    with st.spinner("Extracting with multiple engines and scoring..."):
        try:
            st.session_state["result"] = analyze(resume.getvalue(), resume.name, jd_text=jd or None,
                                                 reference_text=ref or None,
                                                 out_dir=out_dir if save else None, save=save)
        except ValueError as exc:
            st.error(str(exc))
            st.session_state.pop("result", None)

r = st.session_state.get("result")
if not r:
    st.info("Upload a resume to begin. Add a job description for a match score.")
    st.stop()

acc, ats = r["extraction"], r["ats"]
ref = acc.get("reference_check")
st.divider()
st.subheader(f"Results for {r['file']}")

m1, m2, m3, m4 = st.columns(4)
if ref:
    m1.metric("Extraction accuracy (verified)", f"{ref['verified_accuracy']:.0f}%",
              help="Measured against the reference text you supplied.")
else:
    m1.metric("Extraction accuracy (estimated)", f"{acc['estimated_accuracy']:.0f}%",
              help="Estimated from parser agreement, text cleanliness, structure and keyword integrity.")
    m1.caption(f"Grade: **{acc['grade']}**")
if ats["mode"] == "job_match":
    m2.metric("ATS match score", f"{ats['score']:.0f}%")
    m2.caption(f"Grade: **{ats['grade']}**")
    m3.metric("JD skills matched", ats["keyword_match_ratio"])
else:
    m2.metric("ATS readiness score", f"{ats['score']:.0f}%")
    m2.caption(f"Grade: **{ats['grade']}**")
    m3.metric("Hard skills found", ats["hard_skill_count"])
m4.metric("Words extracted", acc["structure"]["word_count"])
if r.get("output_files"):
    st.caption(f"Saved to `{r['output_files']['folder']}`")

tabs = st.tabs(["Extracted text", "Accuracy check", "Keywords", "Score breakdown", "Recommendations", "Downloads"])

# ---------------------------------------------------------- extracted text
with tabs[0]:
    stem = Path(r["file"]).stem
    engines = list(r["engine_texts"].keys())
    pick = st.radio("Engine", engines, index=engines.index(acc["primary_engine"]) if acc["primary_engine"] in engines else 0,
                    horizontal=True, help="The primary engine is the one saved as your extracted .txt")
    text = r["engine_texts"].get(pick, r["extracted_text"])
    st.text_area("Extracted text", text, height=420, label_visibility="collapsed")
    st.download_button("Download extracted text (.txt)", r["extracted_text"].encode("utf-8"),
                       file_name=f"{stem}_extracted.txt", mime="text/plain")

# ---------------------------------------------------------- accuracy check
with tabs[1]:
    st.markdown(f"**Estimated accuracy: {acc['estimated_accuracy']:.1f}/100 ({acc['grade']})**  "
                f"<span class='small'>primary engine: {acc['primary_engine']}</span>", unsafe_allow_html=True)
    bar_rows(acc["components"])
    if ref:
        st.markdown("#### Verified against your reference")
        c = st.columns(5)
        c[0].metric("Verified accuracy", f"{ref['verified_accuracy']:.1f}%")
        c[1].metric("Word recall", f"{ref['word_recall']:.1f}%")
        c[2].metric("Word precision", f"{ref['word_precision']:.1f}%")
        c[3].metric("Reading order", f"{ref['word_order_accuracy']:.1f}%")
        c[4].metric("Keyword recall", f"{ref['keyword_recall']:.1f}%")
        if ref["keywords_missing_vs_reference"]:
            st.markdown("Keywords in your reference but lost in extraction: "
                        + chips(ref["keywords_missing_vs_reference"], "miss"), unsafe_allow_html=True)
        if ref["missing_words_sample"]:
            st.caption("Words missing (sample): " + ", ".join(ref["missing_words_sample"][:30]))
    if acc["engine_comparisons"]:
        st.markdown("#### Parser agreement")
        wide(st.dataframe,
            [{"Engine": c["engine"], "Content agreement %": c["content_agreement"],
              "Reading-order agreement %": c["order_agreement"], "Words": c["words"]} for c in acc["engine_comparisons"]],
            hide_index=True)
        st.caption("Content = same words regardless of order. Reading order = same words in the same sequence. "
                   "Low reading-order agreement usually means columns or tables are being read differently.")
    st.markdown("#### What an ATS can find")
    s = acc["structure"]
    c = st.columns(4)
    c[0].markdown(f"**Email**  \n{s['email'] or ':red[not found]'}")
    c[1].markdown(f"**Phone**  \n{s['phone'] or ':red[not found]'}")
    c[2].markdown(f"**LinkedIn**  \n{s['linkedin'] or ':red[not found]'}")
    c[3].markdown(f"**GitHub**  \n{s['github'] or 'n/a'}")
    st.markdown("Sections recognized: " + chips(s["sections_found"], "ok")
                + (" &nbsp; Missing: " + chips(s["missing_essential"], "miss") if s["missing_essential"] else ""),
                unsafe_allow_html=True)
    ki = acc["keyword_integrity"]
    st.markdown(f"Keyword integrity: **{ki['found_in_primary']} of {ki['found_by_any_engine']}** skills seen by any "
                "parser are present in the extracted text.")
    if ki["missing_from_primary"]:
        st.markdown("Lost in extraction: " + chips(ki["missing_from_primary"], "miss"), unsafe_allow_html=True)
    issues = acc["cleanliness"]["issues"] + acc["layout_notes"]
    if acc.get("hidden_links"):
        issues.append("Hyperlinks with no visible URL text: " + ", ".join(acc["hidden_links"]))
    if issues:
        st.markdown("#### Issues found")
        for i in issues:
            st.warning(md_safe(i))
    else:
        st.success("No extraction issues found.")
    if acc["engines_failed"]:
        st.caption("Engines unavailable: " + "; ".join(f"{k} ({v})" for k, v in acc["engines_failed"].items()))

# ---------------------------------------------------------------- keywords
with tabs[2]:
    jd_skill_names: set[str] = set()
    if ats["mode"] == "job_match":
        jd_skill_names = set(ats["matched_hard_skills"])
        st.markdown(f"**JD title:** {ats['jd_title'] or 'not detected'}"
                    + (f" &nbsp;|&nbsp; **Experience asked:** {', '.join(ats['jd_years_required'])}" if ats["jd_years_required"] else "")
                    + (f" &nbsp;|&nbsp; **Degree:** {ats['jd_degree']}" if ats["jd_degree"] else ""), unsafe_allow_html=True)
        st.markdown("**Matched hard skills**<br>" + chips(ats["matched_hard_skills"], "ok"), unsafe_allow_html=True)
        st.markdown("**Missing (required)**<br>" + chips(ats["missing_required_skills"], "miss"), unsafe_allow_html=True)
        st.markdown("**Missing (preferred / other)**<br>" + chips(ats["missing_other_skills"], "part"), unsafe_allow_html=True)
        if ats["soft_skills"]:
            st.markdown("**Soft skills**<br>"
                        + chips([r_["term"] for r_ in ats["soft_skills"] if r_["match"]], "ok")
                        + chips([r_["term"] for r_ in ats["soft_skills"] if not r_["match"]], "miss"), unsafe_allow_html=True)
        if ats["phrases"]:
            st.markdown("**Key phrases from the JD**<br>"
                        + chips([p["term"] for p in ats["phrases"] if p["match"] == 1], "ok")
                        + chips([p["term"] for p in ats["phrases"] if p["match"] == 0.5], "part")
                        + chips([p["term"] for p in ats["phrases"] if p["match"] == 0], "miss"), unsafe_allow_html=True)
        with st.expander("JD keyword table"):
            wide(st.dataframe, [{"Skill": x["term"], "JD section": x["section"], "Weight": x["weight"],
                           "Times in JD": x["jd_count"], "In resume": "yes" if x["match"] else "missing",
                           "Times in resume": x["resume_count"]} for x in ats["hard_skills"]],
                         hide_index=True)
    st.markdown("#### Every keyword extracted from your resume")
    st.caption("Check the 'Found in' column to confirm each keyword was read correctly from your resume.")
    wide(st.dataframe, [{"Skill": k["skill"], "Category": k["category"], "Count": k["count"],
                   "As written": ", ".join(k["as_written"]), "Found in": k["context"]} for k in r["keyword_audit"]],
                 hide_index=True, height=360)
    with st.expander("Extracted text with keywords highlighted", expanded=False):
        if jd_skill_names:
            st.markdown("<span class='small'><mark class='jd'>green</mark> = matches the JD, "
                        "<mark class='sk'>blue</mark> = other skills</span>", unsafe_allow_html=True)
        st.markdown(highlighted(r["extracted_text"], r["keyword_audit"], jd_skill_names), unsafe_allow_html=True)

# --------------------------------------------------------- score breakdown
with tabs[3]:
    title = "ATS match score" if ats["mode"] == "job_match" else "ATS readiness score"
    st.markdown(f"**{title}: {ats['score']:.1f}/100 ({ats['grade']})**")
    bar_rows(ats["components"])
    bq = ats["bullets"]
    st.caption(f"Content lines analysed: {bq['lines']}. With numbers: {bq['quantified']}. "
               f"Starting with an action verb: {bq['action_led']}.")
    if ats["mode"] == "job_match":
        st.caption(f"Raw cosine similarity resume vs JD: {ats['cosine_similarity']}")
    st.markdown("**Skills detected by category**")
    for cat, names in ats["resume_skills"].items():
        st.markdown(f"<span class='small'>{html.escape(cat)}</span><br>" + chips(names, "neutral"), unsafe_allow_html=True)

# --------------------------------------------------------- recommendations
with tabs[4]:
    if r["recommendations"]:
        for i, rec in enumerate(r["recommendations"], 1):
            st.markdown(f"{i}. {md_safe(rec)}")
    else:
        st.success("Nothing to fix. Looks ATS-ready.")

# --------------------------------------------------------------- downloads
with tabs[5]:
    stem = Path(r["file"]).stem
    c = st.columns(4)
    c[0].download_button("Extracted text (.txt)", r["extracted_text"].encode("utf-8"), f"{stem}_extracted.txt", "text/plain")
    c[1].download_button("Report (.txt)", text_report(r).encode("utf-8"), f"{stem}_ats_report.txt", "text/plain")
    slim = {k: v for k, v in r.items() if k not in ("engine_texts", "extracted_text", "output_files")}
    c[2].download_button("Report (.json)", json.dumps(slim, indent=2, default=list).encode("utf-8"),
                         f"{stem}_ats_report.json", "application/json")
    c[3].download_button("Everything (.zip)", zip_outputs(r), f"{stem}_ats_scan.zip", "application/zip")
    with st.expander("Preview text report"):
        st.code(text_report(r), language=None)

"""Keyword extraction: taxonomy skill matching for resumes and job descriptions,
plus RAKE-style key phrase mining for JD terms the taxonomy doesn't know."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .skills_db import SKILLS, SOFT_SKILL_CATEGORY
from .text_utils import GENERIC_JD_WORDS, STOPWORDS, normalize, stem, stemmed_string, word_tokens

CUSTOM_SKILLS_FILE = Path(__file__).resolve().parent.parent / "custom_skills.txt"


@dataclass
class Skill:
    name: str
    category: str
    patterns: list[re.Pattern]


@dataclass
class SkillHit:
    name: str
    category: str
    count: int = 0
    forms: set[str] = field(default_factory=set)
    first_pos: int = -1

    @property
    def is_soft(self) -> bool:
        return self.category == SOFT_SKILL_CATEGORY


def _alias_regex(alias: str) -> re.Pattern:
    flags = re.IGNORECASE
    list_context = False
    if alias.startswith("CSL:"):
        alias, flags, list_context = alias[4:], 0, True
    elif alias.startswith("CS:"):
        alias, flags = alias[3:], 0
    parts = [re.escape(p).replace(r"\-", r"[\s\-]?") for p in alias.split()]
    body = r"[\s\-]+".join(parts)
    if list_context:
        pat = rf"(?:^|(?<=[\s,/|(;:]))({body})(?=\s*(?:[,/|);&]|$|and\b|or\b))"
        return re.compile(pat, flags | re.MULTILINE)
    return re.compile(rf"(?<![A-Za-z0-9])({body})(?![A-Za-z0-9+#])", flags)


def _load_custom() -> dict[str, list[str]]:
    """custom_skills.txt lines: Canonical Name | alias one, alias two"""
    out: dict[str, list[str]] = {}
    if not CUSTOM_SKILLS_FILE.exists():
        return out
    for line in CUSTOM_SKILLS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, aliases = line.partition("|")
        name = name.strip()
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()] or [name.lower()]
        if name:
            out[name] = alias_list
    return out


@lru_cache(maxsize=1)
def taxonomy() -> tuple[Skill, ...]:
    skills: dict[str, Skill] = {}
    for category, entries in SKILLS.items():
        for name, aliases in entries.items():
            if name not in skills:
                skills[name] = Skill(name, category, [_alias_regex(a) for a in aliases])
    for name, aliases in _load_custom().items():
        skills[name] = Skill(name, "Custom", [_alias_regex(a) for a in aliases])
    return tuple(skills.values())


def find_skills(text: str) -> dict[str, SkillHit]:
    text = normalize(text)
    hits: dict[str, SkillHit] = {}
    for skill in taxonomy():
        spans, forms = [], set()
        for pat in skill.patterns:
            for m in pat.finditer(text):
                spans.append(m.span(1))
                forms.add(m.group(1))
        # overlapping aliases (".net" inside ".net core") count once
        count, last_end = 0, -1
        for start, end in sorted(spans):
            if start >= last_end:
                count += 1
            last_end = max(last_end, end)
        if count:
            hits[skill.name] = SkillHit(skill.name, skill.category, count, forms, min(spans)[0])
    return hits


def skill_context(text: str, hit: SkillHit, width: int = 90) -> str:
    """The line where a skill was first found, for eyeballing that the match is real."""
    text = normalize(text)
    if hit.first_pos < 0:
        return ""
    start = text.rfind("\n", 0, hit.first_pos) + 1
    end = text.find("\n", hit.first_pos)
    end = len(text) if end == -1 else end
    line = text[start:end].strip()
    if len(line) > width:
        rel = hit.first_pos - start
        lo = max(0, rel - width // 2)
        line = ("..." if lo else "") + line[lo:lo + width].strip() + "..."
    return line


# ----------------------------------------------------------------- JD analysis

_BOILERPLATE = re.compile(
    r"equal opportunity|eeo\b|without regard to|race, color|veteran status|disabilit|"
    r"reasonable accommodation|pay range|salary range|compensation|401\(?k|medical, dental|"
    r"paid time off|\bpto\b|e-verify|privacy notice",
    re.I,
)
_REQ_HEAD = re.compile(
    r"requirement|qualification|must[\s-]have|what you('ll| will) (need|bring)|what we('re| are) looking for|"
    r"you have|who you are|minimum|required|your skills|about you|skills",
    re.I,
)
_PREF_HEAD = re.compile(r"preferred|nice[\s-]to[\s-]have|bonus|desired|good to have|pluses|ideal|additional", re.I)
_RESP_HEAD = re.compile(r"responsibilit|what you('ll| will) do|the role|about the|duties|day to day|benefits|perks", re.I)
_PREF_INLINE = re.compile(r"preferred|nice[\s-]to[\s-]have|is a plus|\ba plus\b|bonus|desirable|good to have|ideally|familiarity", re.I)

SECTION_WEIGHT = {"required": 1.0, "general": 0.8, "preferred": 0.5}

_ROLE_NOUNS = re.compile(
    r"\b(engineer|developer|analyst|scientist|architect|intern|specialist|consultant|administrator|"
    r"designer|programmer|devops|sre|technician|manager|lead|associate|researcher)\b",
    re.I,
)
_TITLE_NOISE = set("""senior junior sr jr lead staff principal associate intern internship i ii iii iv v level entry
new grad graduate summer fall spring winter co op remote hybrid onsite on site full part time contract
temporary the of and for in at to a an 2024 2025 2026 2027 2028""".split())


@dataclass
class JDTerm:
    name: str
    weight: float
    section: str
    count: int
    kind: str  # hard | soft | phrase


@dataclass
class JDAnalysis:
    title: str = ""
    hard_skills: list[JDTerm] = field(default_factory=list)
    soft_skills: list[JDTerm] = field(default_factory=list)
    phrases: list[JDTerm] = field(default_factory=list)
    years_required: list[str] = field(default_factory=list)
    degree_level: int = 0  # 0 none, 1 bachelor, 2 master, 3 phd
    degree_text: str = ""


def _is_heading(line: str) -> bool:
    s = line.strip()
    return 0 < len(s) <= 70 and (s.endswith(":") or (len(s.split()) <= 7 and not s.endswith(".")))


def _classify_lines(lines: list[str]) -> list[str]:
    section = "general"
    out = []
    for line in lines:
        if _is_heading(line):
            if _PREF_HEAD.search(line):
                section = "preferred"
            elif _REQ_HEAD.search(line):
                section = "required"
            elif _RESP_HEAD.search(line):
                section = "general"
        line_section = section
        if section != "preferred" and _PREF_INLINE.search(line):
            line_section = "preferred"
        out.append(line_section)
    return out


def _extract_title(lines: list[str]) -> str:
    for line in lines[:10]:
        m = re.match(r"^\s*(?:job\s*title|title|position|role)\s*[:\-]\s*(.+)$", line, re.I)
        if m:
            return m.group(1).strip()
    for line in lines[:3]:
        s = line.strip().strip("#*").strip()
        if s and len(s.split()) <= 10 and not s.endswith(".") and _ROLE_NOUNS.search(s):
            return re.split(r"\s+[|@]\s+|\s+-\s+(?=[A-Z][a-z]+(?:,|\s*$))", s)[0].strip()
    return ""


_DEGREE_PATTERNS = [
    (3, re.compile(r"\bph\.?\s?d\b|\bdoctorate\b|\bdoctoral\b", re.I)),
    (2, re.compile(r"\bmaster'?s?\b|\bm\.s\.|\bm\.sc\b|\bm\.tech\b|\bm\.e\.|\bmba\b", re.I)),
    (2, re.compile(r"\b(?:MS|MSc|MBA|MTech)\b(?!\s*(?:Excel|Office|Word|PowerPoint|Access|Teams|SQL|Project|Visio|Outlook|Dynamics))")),
    (1, re.compile(r"\bbachelor'?s?\b|\bundergraduate degree\b|\bb\.s\.|\bb\.sc\b|\bb\.tech\b|\bb\.e\.|\bb\.a\.", re.I)),
    (1, re.compile(r"\b(?:BS|BSc|BA|BTech|BE|B\.E)\b")),
]


def degree_levels(text: str) -> set[int]:
    return {lvl for lvl, pat in _DEGREE_PATTERNS if pat.search(text)}


_JD_VERBS = {stem(v) for v in """build design develop deploy improve partner own write create maintain operate work
collaborate drive lead manage ensure support move implement deliver scale run ship define help join grow use
make learn mentor participate contribute perform analyze automate monitor test review troubleshoot""".split()}


def _rake_phrases(text: str, max_words: int = 3) -> Counter:
    """Split on stopwords/generic words/punctuation; keep 1-3 word candidate phrases."""
    text = normalize(text).lower()
    phrases: Counter = Counter()
    for chunk in re.split(r"[.,;:!?()\[\]{}\n\r\t\"\u2022\u25cf\u25aa\u25e6|/]+|\s-\s", text):
        words = re.findall(r"[a-z][a-z0-9+#]*(?:[.\-][a-z0-9+#]+)*", chunk)
        run: list[str] = []
        for w in words + ["__end__"]:
            if not run and w != "__end__" and stem(w) in _JD_VERBS:
                continue  # drop a leading verb: "build backend services" -> "backend services"
            if w == "__end__" or w in STOPWORDS or w in GENERIC_JD_WORDS or len(w) < 3 or stem(w) in _JD_VERBS:
                if run:
                    if len(run) <= max_words:
                        phrases[" ".join(run)] += 1
                    else:  # long run: keep its bigrams
                        for i in range(len(run) - 1):
                            phrases[" ".join(run[i:i + 2])] += 1
                run = []
            else:
                run.append(w)
    return phrases


def _key_phrases(text: str, known_skill_text: set[str], limit: int = 12) -> list[tuple[str, int]]:
    phrases = _rake_phrases(text)
    word_freq: Counter = Counter()
    word_deg: Counter = Counter()
    for ph, n in phrases.items():
        ws = ph.split()
        for w in ws:
            word_freq[w] += n
            word_deg[w] += n * len(ws)
    scored = []
    for ph, n in phrases.items():
        ws = ph.split()
        if len(ws) == 1 and n < 2:
            continue  # single words must repeat to count as a theme
        if any(f" {f} " in f" {ph} " for f in known_skill_text):
            continue
        if find_skills(ph):
            continue  # already covered by the taxonomy
        score = sum(word_deg[w] / word_freq[w] for w in ws) * (1 + 0.5 * (n - 1))
        scored.append((score, ph, n))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out, seen_stems = [], set()
    for _, ph, n in scored:
        key = " ".join(stem(w) for w in ph.split())
        if key in seen_stems:
            continue
        seen_stems.add(key)
        out.append((ph, n))
        if len(out) >= limit:
            break
    return out


def analyze_jd(jd_text: str) -> JDAnalysis:
    jd = normalize(jd_text)
    lines = [ln for ln in jd.splitlines()]
    kept = [ln for ln in lines if not _BOILERPLATE.search(ln)]
    sections = _classify_lines(kept)
    result = JDAnalysis(title=_extract_title([ln for ln in kept if ln.strip()]))

    agg: dict[str, dict] = {}
    for line, sec in zip(kept, sections):
        for name, hit in find_skills(line).items():
            entry = agg.setdefault(name, {"hit": hit, "count": 0, "weight": 0.0, "section": sec, "forms": set()})
            entry["count"] += hit.count
            entry["forms"] |= hit.forms
            if SECTION_WEIGHT[sec] > entry["weight"]:
                entry["weight"], entry["section"] = SECTION_WEIGHT[sec], sec
    for name, e in agg.items():
        w = e["weight"] * (1 + 0.25 * min(e["count"] - 1, 2))
        term = JDTerm(name, round(w, 3), e["section"], e["count"], "soft" if e["hit"].is_soft else "hard")
        (result.soft_skills if term.kind == "soft" else result.hard_skills).append(term)
    result.hard_skills.sort(key=lambda t: (-t.weight, t.name))
    result.soft_skills.sort(key=lambda t: (-t.weight, t.name))

    known_forms = {f.lower() for e in agg.values() for f in e["forms"]}
    body = "\n".join(kept)
    # mask known skills so mined phrases never straddle them
    for form in sorted(known_forms, key=len, reverse=True):
        body = re.sub(rf"(?<![A-Za-z0-9]){re.escape(form)}(?![A-Za-z0-9])", " | ", body, flags=re.I)
    for ph, n in _key_phrases(body, known_forms):
        result.phrases.append(JDTerm(ph, 1.0 + 0.25 * min(n - 1, 2), "general", n, "phrase"))

    for line in kept:
        if re.search(r"experience|exp\b", line, re.I):
            for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:(?:-|to)\s*(\d{1,2})\s*)?\+?\s*years?", line, re.I):
                result.years_required.append(m.group(0).strip())
    levels = degree_levels("\n".join(ln for ln in kept if re.search(r"degree|bachelor|master|ph\.?d|b\.?s|m\.?s", ln, re.I)))
    if levels:
        result.degree_level = min(levels)
        result.degree_text = {1: "Bachelor's", 2: "Master's", 3: "PhD"}[result.degree_level]
    return result


def phrase_in_text(phrase: str, resume_stemmed: str, resume_terms: set[str]) -> float:
    """1.0 exact (stem-insensitive) phrase match, 0.5 if all words appear somewhere, else 0."""
    ph = " ".join(stem(t) for t in word_tokens(phrase))
    if f" {ph} " in resume_stemmed:
        return 1.0
    words = [w for w in ph.split() if w not in STOPWORDS]
    if words and all(w in resume_terms for w in words):
        return 0.5
    return 0.0


def title_match(title: str, resume_text: str) -> float | None:
    """Role words found anywhere (60%) plus the exact role phrase found (40%).
    'Software Engineer, Payments Platform' -> role 'Software Engineer'."""
    if not title:
        return None
    role = re.split(r",|\(|\||\s[-/]\s", title)[0]
    core = [stem(w) for w in word_tokens(role) if w not in _TITLE_NOISE and not w.isdigit()]
    if not core:
        return None
    resume_stemmed = stemmed_string(resume_text)
    resume_terms = set(resume_stemmed.split())
    frac = sum(1 for w in core if w in resume_terms) / len(core)
    exact = f" {' '.join(core)} " in resume_stemmed
    return 0.6 * frac + (0.4 if exact else 0.0)

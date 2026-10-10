"""Shared text helpers: normalization, tokenizing, light stemming, stopwords."""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each either etc few for from further had has
have having he her here hers herself him himself his how i if in into is it its itself just let may me
might more most must my myself no nor not now of off on once only or other our ours ourselves out over
own per same shall she should so some such than that the their theirs them themselves then there these
they this those through to too under until up upon us very via was we were what when where which while
who whom why will with within without would you your yours yourself yourselves
""".split())

# Words that show up in almost every job description and carry no signal.
GENERIC_JD_WORDS = set("""
ability able experience experienced experiences year years strong excellent good great solid proven
demonstrated knowledge understanding familiarity familiar proficiency proficient skill skills work working
works team teams role roles candidate candidates including include includes required requirement
requirements preferred preference plus bonus nice responsibilities responsibility qualification
qualifications job position positions company opportunity opportunities benefit benefits salary
equal employer applicant applicants apply looking seeking join ideal successful environment related
relevant etc using use used new across within well highly key help helping make making ensure ensuring
support supporting provide providing need needs want day days time based level levels multiple various
other others like who what will can must may might should would also least minimum preferred degree
field fields equivalent similar wide range high quality best practices practice business businesses
world global people person individual individuals part full ownership own owning hands hand
""".split())

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_QUOTES = {ord("\u2018"): "'", ord("\u2019"): "'", ord("\u201c"): '"', ord("\u201d"): '"'}


def normalize(text: str) -> str:
    """Unicode-normalize (fixes ligatures like the 'fi' glyph), unify dashes/quotes."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_DASHES).translate(_QUOTES)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    return text


def word_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, used for comparing extractions."""
    return re.findall(r"[a-z0-9]+", normalize(text).lower())


def stem(word: str) -> str:
    """Tiny suffix stemmer, applied identically to resume and JD so it stays consistent."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 4 and (w.endswith("ches") or w.endswith("shes") or w.endswith("xes") or w.endswith("ses")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def content_terms(text: str) -> list[str]:
    """Stemmed tokens without stopwords or pure numbers."""
    return [stem(t) for t in word_tokens(text) if t not in STOPWORDS and not t.isdigit() and len(t) > 1]


def stemmed_string(text: str) -> str:
    """Space-joined stemmed token stream with padding, for phrase containment checks."""
    return " " + " ".join(stem(t) for t in word_tokens(text)) + " "

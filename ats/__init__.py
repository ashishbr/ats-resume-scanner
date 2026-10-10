"""Local ATS resume scanner: multi-engine extraction, accuracy scoring, JD keyword matching."""
from .pipeline import analyze, text_report

__all__ = ["analyze", "text_report"]
__version__ = "1.0.0"

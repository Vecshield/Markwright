"""Calibration orchestration: bundled-corpus loading + synchronous runs.

I/O lives here (corpus files, tokenizer hashing); the statistics live in
``markwright.stats``. The POC runs calibration synchronously in the API
process; v1 moves it to the jobs worker (same functions, different caller).
"""
from .corpus import available_corpora, load_corpus
from .run import run_calibration

__all__ = ["available_corpora", "load_corpus", "run_calibration"]

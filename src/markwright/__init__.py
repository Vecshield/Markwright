"""Markwright: a management layer around published LLM text-watermarking schemes.

Nothing in this package implements a watermarking algorithm. Adapters wrap
existing implementations (Hugging Face transformers, MarkLLM); the core adds
registry, key custody, calibration and honest probabilistic reporting.
"""

__version__ = "0.1.0"

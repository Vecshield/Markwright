"""Evidence bands. Derived from the deployment's false-positive target, never typed by hand.

There is deliberately no boolean anywhere in this module.
"""
from __future__ import annotations

import math
from enum import StrEnum


class Band(StrEnum):
    STRONG = "strong_evidence"
    MODERATE = "moderate_evidence"
    WEAK = "weak_evidence"
    NONE = "no_evidence"
    INSUFFICIENT = "insufficient_evidence"


WEAK_CEILING = 0.05


def band_for(
    p_adjusted: float | None,
    fpr_target: float,
    gate_ok: bool,
    resolution_floor: float | None = None,
) -> tuple[Band, str]:
    """Map an adjusted p-value to an evidence band.

    ``resolution_floor`` is the calibration's own limit (``NullModel.
    resolution_floor()``). When the deployment asks for an FPR target finer
    than the calibration can resolve, STRONG/MODERATE are refused — the system
    must not claim precision its null sample cannot support (POC plan 1.3).
    """
    if not gate_ok or p_adjusted is None:
        return Band.INSUFFICIENT, "text too short or too low-entropy to test"
    capped = resolution_floor is not None and fpr_target < resolution_floor
    if capped and p_adjusted <= WEAK_CEILING:
        return Band.WEAK, (
            f"calibration resolves p down to {resolution_floor:.1e}, coarser than the "
            f"fpr_target ({fpr_target:.1e}); evidence capped at weak until recalibrated "
            "on a larger null corpus"
        )
    if p_adjusted <= fpr_target / 100:
        return Band.STRONG, f"p <= fpr_target/100 ({fpr_target / 100:.1e})"
    if p_adjusted <= fpr_target:
        return Band.MODERATE, f"p <= fpr_target ({fpr_target:.1e})"
    if p_adjusted <= WEAK_CEILING:
        return Band.WEAK, f"fpr_target < p <= {WEAK_CEILING}"
    return Band.NONE, f"p > {WEAK_CEILING}"


def one_in_n(p: float) -> str:
    """'about 1 in 50,000' rounded DOWN to one significant figure, so the UI never
    overstates rarity."""
    if p <= 0:
        return "fewer than 1 in 1,000,000,000"
    n = 1 / p
    if n < 2:
        return "about 1 in 2 or more often"
    mag = 10 ** math.floor(math.log10(n))
    rounded = math.floor(n / mag) * mag
    return f"about 1 in {int(rounded):,}"


PLAIN_TEXT = {
    Band.STRONG: "Strong statistical evidence of this watermark. A human-written text of this length "
                 "would score this high {rarity}. This is not proof, and not a statement about authorship.",
    Band.MODERATE: "Evidence consistent with this watermark at the configured false-positive rate.",
    Band.WEAK: "Weak evidence, below this deployment's bar. Do not act on this alone.",
    Band.NONE: "No evidence of this watermark. This does not mean the text is human-written.",
    Band.INSUFFICIENT: "Too short or too low-entropy to test (this model needs about {min_tokens} tokens).",
}

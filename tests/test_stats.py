import numpy as np

from markwright.stats.bands import Band, band_for, one_in_n
from markwright.stats.null_model import (
    TAIL_EXTRAPOLATION,
    NullModel,
    fit_bucket,
    quality_for,
)


def test_null_model_pvalues_are_calibrated_on_null_draws():
    rng = np.random.default_rng(1)
    null = rng.normal(0.5, 0.05, size=20000)            # e.g. mean-g under H0 at T~200
    nm = NullModel("mean_g", [fit_bucket(null.tolist(), 200)])
    fresh = rng.normal(0.5, 0.05, size=5000)
    ps = np.array([nm.p_value(s, 200) for s in fresh])
    # under the null, p-values are ~Uniform: P(p <= a) ~ a
    for a in (0.5, 0.1, 0.01):
        assert abs((ps <= a).mean() - a) < 2.5 * np.sqrt(a * (1 - a) / 5000) + 0.003


def test_gpd_tail_extrapolates_below_empirical_resolution():
    rng = np.random.default_rng(2)
    nm = NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.05, size=20000).tolist(), 200)])
    b = nm.buckets[0]
    assert b.tail_threshold is not None and b.gpd_scale is not None
    p_far = nm.p_value(0.5 + 6 * 0.05, 200)              # 6 sigma
    assert 0 < p_far < 1e-5


def test_length_interpolation_is_monotone():
    rng = np.random.default_rng(3)
    nm = NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.07, 5000).tolist(), 100),
                              fit_bucket(rng.normal(0.5, 0.035, 5000).tolist(), 400)])
    p100, p250, p400 = (nm.p_value(0.62, n) for n in (100, 250, 400))
    assert p100 > p250 > p400


def test_bands_follow_fpr_target_and_gate():
    assert band_for(1e-7, 1e-4, True)[0] is Band.STRONG
    assert band_for(5e-5, 1e-4, True)[0] is Band.MODERATE
    assert band_for(0.01, 1e-4, True)[0] is Band.WEAK
    assert band_for(0.3, 1e-4, True)[0] is Band.NONE
    assert band_for(1e-9, 1e-4, False)[0] is Band.INSUFFICIENT   # gate wins over any p


def test_one_in_n_rounds_down():
    assert one_in_n(2.1e-5) == "about 1 in 40,000"
    assert one_in_n(0.3) == "about 1 in 3"


# ---- resolution floor + quality (POC plan 1.3) ---------------------------------------------
def test_resolution_floor_without_tail_is_empirical():
    rng = np.random.default_rng(4)
    nm = NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.05, 300).tolist(), 200)])
    b = nm.buckets[0]
    assert b.tail_threshold is None                       # 300 docs: no GPD tail
    assert nm.resolution_floor() == 1 / 301
    # the p-value clamp respects the floor
    assert nm.p_value(0.99, 200) >= nm.resolution_floor()


def test_resolution_floor_with_tail_extends_three_decades():
    rng = np.random.default_rng(5)
    nm = NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.05, 20000).tolist(), 200)])
    b = nm.buckets[0]
    assert b.tail_threshold is not None
    assert nm.resolution_floor() == (1 / 20001) * TAIL_EXTRAPOLATION


def test_floor_is_worst_bucket():
    rng = np.random.default_rng(6)
    nm = NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.07, 300).tolist(), 100),
                              fit_bucket(rng.normal(0.5, 0.035, 20000).tolist(), 400)])
    assert nm.resolution_floor() == 1 / 301               # the small bucket dominates


def test_band_cap_when_fpr_finer_than_floor():
    floor = 3.3e-3
    band, reason = band_for(1e-3, fpr_target=1e-4, gate_ok=True, resolution_floor=floor)
    assert band is Band.WEAK and "calibration resolves p down to" in reason
    # no cap when the target is resolvable
    assert band_for(1e-3, 1e-2, True, resolution_floor=floor)[0] is Band.MODERATE
    # capping never rescues a non-result
    assert band_for(0.3, 1e-4, True, resolution_floor=floor)[0] is Band.NONE
    assert band_for(1e-9, 1e-4, False, resolution_floor=floor)[0] is Band.INSUFFICIENT


def test_quality_tag():
    assert quality_for({100: 300, 200: 300}) == "provisional"
    assert quality_for({100: 5000, 200: 2500}) == "standard"
    assert quality_for({100: 5000, 200: 100}) == "provisional"   # worst bucket decides
    assert quality_for({}) == "provisional"


# ---- precomputed bootstrap (POC plan 1.4) ---------------------------------------------------
def test_precomputed_boot_interval_brackets_the_point_estimate():
    rng = np.random.default_rng(7)
    vals = rng.normal(0.5, 0.05, 2000).tolist()
    nm = NullModel("mean_g", [fit_bucket(vals, 200, n_boot=30)])
    assert len(nm.buckets[0].boot) == 30
    s = 0.58
    p = nm.p_value(s, 200)
    lo, hi = nm.p_value_interval(s, 200)
    assert lo <= p * 1.5 and p * 0.5 <= hi and lo < hi


def test_boot_survives_serialisation_roundtrip():
    rng = np.random.default_rng(8)
    nm = NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.05, 1000).tolist(), 200, n_boot=5)],
                   corpus_ids=["x"], quality="provisional")
    rt = NullModel.from_dict(nm.to_dict())
    assert len(rt.buckets[0].boot) == 5 and rt.quality == "provisional"
    assert rt.p_value_interval(0.6, 200) == nm.p_value_interval(0.6, 200)


# ---- regression: latent baseline bug ---------------------------------------------------------
def test_three_bucket_interpolation_regression():
    """max()/min() over NullBucket objects lacked a key and raised TypeError the
    first time THREE buckets existed and a count fell between the upper two."""
    rng = np.random.default_rng(9)
    nm = NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.08, 500).tolist(), 100),
                              fit_bucket(rng.normal(0.5, 0.05, 500).tolist(), 200),
                              fit_bucket(rng.normal(0.5, 0.03, 500).tolist(), 400)])
    p = nm.p_value(0.6, 306)        # between buckets 2 and 3 → two candidates for lo
    assert 0 < p < 1

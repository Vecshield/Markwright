import numpy as np

from markwright.stats.bands import Band, band_for, one_in_n
from markwright.stats.null_model import NullModel, fit_bucket


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

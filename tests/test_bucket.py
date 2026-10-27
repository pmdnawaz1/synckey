import time

from synckey.bucket import Bucket, BucketRegistry


def test_unlimited_by_default():
    b = Bucket()
    assert b.can_send()
    assert b.can_send(1_000_000)


def test_rpm_cap_enforced():
    b = Bucket(rpm_cap=60)
    # Drain the bucket completely.
    for _ in range(60):
        b.consume()
    assert not b.can_send()


def test_tpm_cap_enforced():
    b = Bucket(tpm_cap=1000)
    b.consume(estimated_tokens=1000)
    assert not b.can_send(1)


def test_refills_over_time():
    b = Bucket(rpm_cap=60)
    for _ in range(60):
        b.consume()
    # Artificially advance last_refill to simulate time passing.
    b._last_refill -= 30  # 30s = 30 tokens refill at 1/s
    assert b.can_send()


def test_on_rate_limit_tightens_cap():
    b = Bucket(rpm_cap=60)
    b.on_rate_limit(retry_after=20, rpm_observed=40)
    # Cap should be tightened to 80% of 40 = 32
    assert b.rpm_cap == pytest.approx(32.0, rel=0.1)
    # Bucket is drained
    assert not b.can_send()


def test_on_rate_limit_no_prior_cap():
    b = Bucket()
    b.on_rate_limit(retry_after=20)
    # Should infer ~2.1 rpm from retry_after=20s window
    assert b.rpm_cap is not None and b.rpm_cap < 5.0


def test_on_success_relaxes_cap_after_streak():
    b = Bucket(rpm_cap=30)
    for _ in range(20):
        b.on_success()
    # Cap should have been relaxed by 10%
    assert b.rpm_cap > 30


def test_registry_caches():
    r = BucketRegistry()
    b1 = r.get(1)
    b2 = r.get(1)
    assert b1 is b2


def test_registry_applies_known_cap():
    r = BucketRegistry()
    r.get(1)  # create with no cap
    b = r.get(1, rpm_cap=60)
    assert b.rpm_cap == 60


import pytest


def test_emission_single_sample_is_not_inflated():
    b = Bucket()
    b.consume()
    assert b.emission_rpm() == 1.0


def test_emission_rate_over_span():
    b = Bucket()
    now = time.time()
    b._window = [now - 30, now - 10]
    assert b.emission_rpm() == pytest.approx(60.0 / 30.0)


def test_relaxation_capped_at_observed_max():
    b = Bucket(rpm_cap=60)
    b.on_rate_limit(retry_after=20, rpm_observed=40)
    for _ in range(200):
        b.on_success()
    assert b.rpm_cap == pytest.approx(40.0)


def test_for_key_adopts_changed_declared_caps():
    r = BucketRegistry()
    b1 = r.for_key(1, 60, None)
    assert b1.rpm_cap == 60
    assert r.for_key(1, 60, None) is b1
    b3 = r.for_key(1, 30, None)
    assert b3 is not b1 and b3.rpm_cap == 30
    assert r.for_key(1, None, None).rpm_cap is None

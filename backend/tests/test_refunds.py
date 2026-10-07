"""Sharing what is left in a frozen wallet among the victims who claimed it."""

from __future__ import annotations

import pytest

from fraudlens.platform.refunds import allocate


def test_every_victim_is_paid_in_full_when_the_money_is_still_there():
    assert allocate([1_000.0, 500.0], 3_000.0) == [1_000.0, 500.0]
    assert allocate([1_000.0, 500.0], 1_500.0) == [1_000.0, 500.0]


def test_a_shortfall_is_shared_in_proportion_to_each_loss():
    assert allocate([1_000.0, 500.0], 600.0) == [400.0, 200.0]


@pytest.mark.parametrize(
    "claimed, available",
    [([333.33, 0.01, 99.99], 100.0), ([0.07, 0.07, 0.07], 0.2), ([12.5] * 7, 33.33)],
)
def test_never_more_than_is_in_the_wallet_and_never_more_than_was_lost(claimed, available):
    paid = allocate(claimed, available)
    assert round(sum(paid), 2) <= available
    assert all(0 <= p <= c for p, c in zip(paid, claimed, strict=True))


def test_an_empty_wallet_pays_nothing():
    assert allocate([10.0, 20.0], 0.0) == [0.0, 0.0]
    assert allocate([10.0], -5.0) == [0.0]

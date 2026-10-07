import math

import pytest

from fraudlens.models import adversary as experiment
from fraudlens.simulator.adversary import (
    DELAY_HOURS,
    SEASONED_MIN_AGE_DAYS,
    TACTICS,
    Adversary,
    AdversaryConfig,
    deploy,
)
from fraudlens.simulator.config import SimConfig
from fraudlens.simulator.engine import Simulation
from fraudlens.simulator.generate import build_tables, transaction_table

DAY = 86_400


def test_stepping_the_world_by_days_matches_one_run(small_tables, small_cfg):
    sim = Simulation(small_cfg)
    sim.run_days(0, 20)
    sim.run_days(20, small_cfg.days)
    txns = build_tables(sim)["transactions"]
    expected = small_tables["transactions"]
    assert len(txns) == len(expected)
    assert txns["amount"].sum() == pytest.approx(expected["amount"].sum())


def test_mix_moves_toward_what_got_through():
    adv = Adversary(AdversaryConfig())
    start = dict(adv.mix)
    outcomes = {t: (20, 0) for t in TACTICS}
    outcomes["fan_out"] = (20, 15)
    mix = adv.update(outcomes)
    assert mix["fan_out"] > start["fan_out"]
    assert mix["none"] < start["none"]
    assert sum(mix.values()) == pytest.approx(1.0)
    assert min(mix.values()) >= AdversaryConfig().floor


def test_too_few_cases_say_nothing_and_control_never_adapts():
    adv = Adversary(AdversaryConfig())
    before = dict(adv.mix)
    after = adv.update({t: (2, 2) for t in TACTICS})  # below min_cases everywhere
    # Only the floor moves it: no tactic gains on another.
    assert max(after, key=after.get) == max(before, key=before.get)
    assert len({round(after[t], 9) for t in TACTICS if t != "none"}) == 1
    control = Adversary(AdversaryConfig(adapt=False))
    assert control.update({"fan_out": (50, 50)}) == control.mix


@pytest.fixture(scope="module")
def adaptive_world():
    """The small world's last 20 days with adaptive cells deployed."""
    cfg = SimConfig.small()
    sim = Simulation(cfg)
    deploy_day = cfg.days - 20
    sim.run_days(0, deploy_day)
    start = len(sim.cols["amount"])
    planner = deploy(sim, Adversary(AdversaryConfig(n_cells=4, warmup_days=4)), deploy_day)
    sim.run_days(deploy_day, cfg.days)
    return sim, planner, transaction_table(sim, start)


def _rows(txns, planner, tactic, role):
    cases = [c for c, t in planner.tactic.items() if t == tactic]
    return txns[txns["case_id"].isin(cases) & (txns["fraud_role"] == role)]


def test_adaptive_cells_try_every_tactic(adaptive_world):
    _, planner, txns = adaptive_world
    assert set(planner.tactic.values()) == set(TACTICS)
    assert txns["case_id"].isin(list(planner.tactic)).any()


def test_split_payments_are_smaller_and_more_numerous(adaptive_world):
    _, planner, txns = adaptive_world
    split = _rows(txns, planner, "split_amounts", "victim_transfer")
    plain = _rows(txns, planner, "none", "victim_transfer")
    assert len(split) and len(plain)
    assert split["amount"].median() < plain["amount"].median()
    assert split.groupby("case_id").size().mean() > plain.groupby("case_id").size().mean()


def test_delayed_cash_out_waits_hours(adaptive_world):
    _, planner, txns = adaptive_world
    first_in = _rows(txns, planner, "delayed_cashout", "victim_transfer")
    first_out = _rows(txns, planner, "delayed_cashout", "mule_cashout")
    first_in = first_in.groupby("case_id")["ts"].min()
    first_out = first_out.groupby("case_id")["ts"].min()
    both = first_in.index.intersection(first_out.index)
    assert len(both)
    waited = (first_out[both] - first_in[both]).dt.total_seconds()
    assert (waited >= DELAY_HOURS[0] * 3600).all()


def test_seasoned_mules_are_old_wallets(adaptive_world):
    sim, planner, _ = adaptive_world
    seasoned = [m for pool in planner.seasoned.values() for m in pool]
    assert seasoned
    for m in seasoned:
        assert m.kind == "seasoned"
        assert m.usable_ts - float(sim.w.created_ts[m.wallet]) >= SEASONED_MIN_AGE_DAYS * DAY


def test_experiment_runs_end_to_end(trained):
    data_dir, models_root, _ = trained
    acfg = AdversaryConfig(rounds=2, round_days=3, n_cells=3, warmup_days=5)
    report = experiment.run(data_dir, models_root, None, acfg, log=lambda *_: None)
    assert set(report["arms"]) == set(experiment.ARMS)
    for arm in experiment.ARMS:
        rounds = report["arms"][arm]["rounds"]
        assert len(rounds) == acfg.rounds
        for r in rounds:
            for key in ("case_recall", "loss_txn_recall"):
                assert r[key] is None or 0 <= r[key] <= 1
    control = report["arms"]["control"]["rounds"]
    assert control[0]["mix"] == control[-1]["mix_after"]
    assert report["arms"]["frozen"]["last_model"] == report["served_model"]
    assert report["arms"]["retrain"]["last_model"] == "retrain-r1"
    assert not math.isnan(report["seconds"])

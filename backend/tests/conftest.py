import pytest

from fraudlens.features.build import build_features, load_tables
from fraudlens.models.train import train
from fraudlens.simulator.config import SimConfig
from fraudlens.simulator.engine import Simulation
from fraudlens.simulator.generate import build_tables, generate


@pytest.fixture(scope="session")
def small_cfg() -> SimConfig:
    return SimConfig.small()


@pytest.fixture(scope="session")
def small_tables(small_cfg):
    sim = Simulation(small_cfg)
    sim.run()
    return build_tables(sim)


@pytest.fixture(scope="session")
def trained(tmp_path_factory):
    """The whole pipeline on the small world: (data dir, models root, training report)."""
    data_dir = tmp_path_factory.mktemp("data")
    models_root = tmp_path_factory.mktemp("models")
    generate(SimConfig.small(), data_dir)
    features = build_features(load_tables(data_dir), snapshot_dir=data_dir)
    features.to_parquet(data_dir / "features.parquet", index=False)
    report = train(data_dir, models_root=models_root)
    return data_dir, models_root, report

"""Take a clean checkout to a populated, running platform, then serve it.

    uv run python -m fraudlens.platform.demo

This is what `make demo` runs in the API container. Each step is skipped when its
result is already there, so a second start goes straight to serving:

1. dataset, features, models, policy report and dashboard tables (`make pipeline`)
2. schema, demo accounts and the historical period in the database (`make platform`)
3. the test period replayed through the event stream, its last day one request at
   a time, the older cases closed by the review simulator, a challenger retrained
   on those verdicts and scored in shadow mode
4. the API, with the challenger in shadow mode

Step 3 stops part-way if it is interrupted; `make demo-reset` starts it again.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from ..config import BACKEND_DIR, Settings

# Present only while step 4 is serving: what the container's health check looks for.
READY_MARKER = Path(tempfile.gettempdir()) / "fraudlens-demo-ready"
START_TIMEOUT = 600  # seconds for the API to restore its feature state and answer /ready


def step(title: str, module: str, *args: str, required: bool = True) -> bool:
    print(f"\n== {title} ==", flush=True)
    t0 = time.perf_counter()
    done = subprocess.run([sys.executable, "-m", module, *args], cwd=BACKEND_DIR, check=False)
    if done.returncode and required:
        raise SystemExit(f"{module} failed (exit {done.returncode}); the demo cannot continue")
    print(f"== {'done' if not done.returncode else 'FAILED'} in {time.perf_counter() - t0:.0f}s ==")
    return done.returncode == 0


def pipeline(settings: Settings) -> None:
    from ..models import registry

    data, models = settings.dataset_dir, settings.models_dir
    if not (data / "meta.json").is_file():
        step("Generating the synthetic dataset", "fraudlens.simulator.generate")
    if not (data / "engine_after_test.pkl").is_file():
        step("Building features", "fraudlens.features.build")
    if registry.current_version(models) is None:
        step("Training and evaluating the models", "fraudlens.models.train")
    version = models / str(registry.current_version(models))
    if not (version / "policy_report.json").is_file():
        step("Evaluating the decision policy", "fraudlens.decision.evaluate")
    if not (version / "insights.json").is_file():
        step("Building the dashboard tables", "fraudlens.decision.insights")
    if not (settings.artifacts_dir / "intel" / "report.json").is_file():
        step("Training the scam-message classifier", "fraudlens.intel.train")


def database(settings: Settings) -> bool:
    """Schema, accounts and history. Returns whether the test period was already replayed."""
    from sqlalchemy import select

    from .db import make_engine, make_sessions, migrate
    from .models import Decision, Transaction

    migrate(settings.database_url)
    step("Creating the demo accounts", "fraudlens.platform.seed")
    engine = make_engine(settings.database_url)
    try:
        with make_sessions(engine)() as s:
            loaded = s.scalar(select(Transaction.txn_id).limit(1)) is not None
            replayed = s.scalar(select(Decision.txn_id).limit(1)) is not None
    finally:
        engine.dispose()
    if not loaded:
        step("Loading the historical period", "fraudlens.platform.load", "--reset")
    return replayed


def serve_command(host: str, port: int) -> list[str]:
    app = "fraudlens.api:create_app"
    return [sys.executable, "-m", "uvicorn", app, "--factory", "--host", host, "--port", str(port)]


def wait_ready(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"the API stopped while starting (exit {process.returncode})")
        try:
            if httpx.get(f"{url}/ready", timeout=5.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    raise SystemExit(f"the API was not ready after {START_TIMEOUT}s")


def populate(settings: Settings, port: int) -> None:
    """Step 3, against an API that runs only for as long as this takes."""
    from ..models import registry

    url = f"http://127.0.0.1:{port}"
    print("\n== Starting the API for the replay ==", flush=True)
    process = subprocess.Popen(serve_command("127.0.0.1", port), cwd=BACKEND_DIR)
    try:
        wait_ready(url, process)
        replay = "fraudlens.platform.replay"
        step("Replaying the test period through the stream", replay, "--to-day", "118")
        step(
            "Sending the last day one request at a time",
            replay,
            "--via",
            "http",
            "--from-day",
            "119",
            "--api",
            url,
        )
        # A mismatch is worth seeing, and no reason to leave the console unreachable.
        step(
            "Checking served decisions against the offline evaluation",
            "fraudlens.platform.verify",
            required=False,
        )
        step("Closing the older cases (review simulator)", "fraudlens.mlops.review", "--api", url)
        step("Retraining on the verdicts", "fraudlens.mlops.retrain")
        version = registry.versions(settings.models_dir)[-1]
        step(
            f"Scoring past decisions with {version} in shadow mode",
            "fraudlens.mlops.shadow",
            "--version",
            version,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()


def challenger(settings: Settings) -> str | None:
    """The version to run in shadow mode: the configured one, else the newest unpromoted."""
    from ..models import registry

    if settings.shadow_model_version:
        return settings.shadow_model_version
    versions = registry.versions(settings.models_dir)
    newest = versions[-1] if versions else None
    return newest if newest != registry.current_version(settings.models_dir) else None


def healthy(port: int) -> bool:
    if not READY_MARKER.is_file():
        return False
    try:
        return httpx.get(f"http://127.0.0.1:{port}/ready", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Build what is missing, then serve the API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--check", action="store_true", help="exit 0 once the demo is serving")
    args = parser.parse_args()
    if args.check:
        sys.exit(0 if healthy(args.port) else 1)

    READY_MARKER.unlink(missing_ok=True)
    settings = Settings()
    if settings.seed_password is None:
        # The replay and the review simulator sign in with it, and so will you.
        raise SystemExit("set FRAUDLENS_SEED_PASSWORD in backend/.env (`make demo` writes one)")
    t0 = time.perf_counter()
    pipeline(settings)
    if not database(settings):
        populate(settings, args.port)
    shadow = challenger(settings)
    if shadow:
        os.environ["FRAUDLENS_SHADOW_MODEL_VERSION"] = shadow
    minutes = (time.perf_counter() - t0) / 60
    print(f"\n== Ready in {minutes:.1f} min: serving on port {args.port} ==", flush=True)
    READY_MARKER.touch()
    os.chdir(BACKEND_DIR)
    os.execv(sys.executable, serve_command(args.host, args.port))


if __name__ == "__main__":
    main()

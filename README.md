# FraudLens

Scam-to-cash-out interception for a mobile wallet (bKash/Nagad/upay style), built
as a working prototype on synthetic data.

In an authorised-push-payment scam the victim sends the money themselves, so the
sender often looks normal. FraudLens scores the **recipient and its network** as
well as the sender, warns the customer in Bangla before the money moves, pauses
the riskiest payments for a person to review, and gets a second chance when the
money tries to leave the mule wallet through an agent.

What is in the box:

| Layer | What it does |
| --- | --- |
| Simulator | A 120-day world of wallets, agents and merchants with five fraud typologies, one of them held out of training |
| Features | 57 point-in-time features from one engine used for both training and serving |
| Models | LightGBM transaction risk, mule-wallet score, Isolation Forest anomaly, agent peer comparison, ring detection |
| Decisions | allow / warn / step-up / hold from versioned rules and budgeted thresholds, with a rules-only fallback |
| Explanations | SHAP reasons in Bangla and English, rule trace, similar past cases, case summary |
| Platform | FastAPI scoring API, Redis Streams ingestion, Postgres, roles, append-only audit log |
| Console | Alert queue, case page, network explorer, ring freeze with second approval, agent risk, customer phone demo |
| MLOps | Verdicts become labels, retraining, model registry, shadow mode, drift, fairness report |

## Start it

You need Docker with Compose v2 (about 6 GB of memory for Docker) and `make`.

```
make demo
```

The first start builds the two images, generates the dataset, trains the models,
replays 25 days of traffic through the running service and retrains a challenger
on the analysts' verdicts. That takes **FIRST_START_MINUTES** on a recent laptop;
progress is printed step by step. When the log shows the console as started, open

**http://localhost:3100**

and sign in as `analyst1`, `supervisor1` or `admin`. The password is generated on
the first `make demo` and written to `backend/.env` (`FRAUDLENS_SEED_PASSWORD`).

| Account | Sees |
| --- | --- |
| `analyst1`, `analyst2` | alerts, cases, network, rings, agents, dashboards, the phone demo |
| `supervisor1`, `supervisor2` | the same, plus freeze approvals and the audit log |
| `admin` | dashboards and the audit log, no customer data |

Later starts skip everything that already exists and are ready in under a minute.

```
make demo-down     # stop, keep the data            (Ctrl-C in the first terminal also stops it)
make demo-reset    # stop and delete the database, dataset and models
```

The demo uses ports 3100 (console), 8010 (API), 5433 (Postgres) and 6380 (Redis),
all bound to 127.0.0.1.

A ten-minute walk through every feature is in
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Documents

| | |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the parts fit and why they are split that way |
| [DATA_ASSUMPTIONS.md](docs/DATA_ASSUMPTIONS.md) | What the synthetic world contains and what it leaves out |
| [MODEL_CARD.md](docs/MODEL_CARD.md) | Models, results, ablations, fairness, limits |
| [DECISION_POLICY.md](docs/DECISION_POLICY.md) | Tiers, rules, thresholds, explanations, the language model's role |
| [PLATFORM.md](docs/PLATFORM.md) | API, workflow, security, stream, measured latency |
| [DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | The walk-through |

## Develop on the host

Needs [uv](https://docs.astral.sh/uv/), Node 22 with pnpm (`corepack enable`),
and Docker for Postgres and Redis.

```
cp backend/.env.example backend/.env   # then set FRAUDLENS_SEED_PASSWORD (12+ characters)
make setup         # backend and console dependencies
make up            # Postgres and Redis
make pipeline      # data, features, models, policy, dashboard tables
make platform      # migrate, demo accounts, load the historical period
make api           # API and stream worker on http://127.0.0.1:8010

# in a second terminal
make replay        # test days 95-118 through the event stream
make replay-live   # day 119 one request at a time; writes the latency report
make verify        # served decisions against the offline evaluation
make review        # close the older cases with the simulation's ground truth
make retrain       # train a challenger on those verdicts (promotes nothing)
make console       # console on http://localhost:3100
```

To run a challenger in shadow mode, set `FRAUDLENS_SHADOW_MODEL_VERSION` to its
version (`make models` lists them) in `backend/.env` and restart the API.
`make help` lists every target.

## Check it

```
make test     # 175 backend tests; the platform tests need `make up`
make lint     # ruff, tsc, eslint
make smoke    # opens every console page as each role in a headless browser
```

`make smoke` needs the demo (or `make api` and `make console`) running and
`make setup` done. CI (`.github/workflows/ci.yml`) runs the tests and the lint
against real Postgres and Redis, builds the console and builds both images.

## What this is not

Everything runs on synthetic data, so absolute numbers will not transfer to real
traffic. The demo accounts, the phone demo endpoints and the review simulator
exist only outside production mode. No model output moves or blocks money on its
own: a hold waits for an analyst and a freeze needs two people. The limits of
each part are listed in its document under "Limits, stated plainly".

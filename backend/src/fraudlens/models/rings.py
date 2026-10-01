"""Ring detection: suspicious wallets that are connected to each other.

One mule wallet is a symptom; the group behind it is the problem. Starting from
wallets that are confirmed bad or score high on the mule model, link any two that
share a handset or have moved money between them, and report each connected
group. A wallet that shares a handset with a suspicious wallet is pulled into the
group even if it has not been used yet: that is how unused mules are found early.
"""

from __future__ import annotations

from collections import Counter

import networkx as nx

from ..features import FeatureEngine

MAX_DEVICE_WALLETS = 25  # a "device" seen on more wallets than this is not a handset


def find_rings(
    engine: FeatureEngine, suspicious: dict[str, float], min_size: int = 3
) -> list[dict]:
    """Connected groups of suspicious wallets.

    `suspicious` maps wallet id to a mule score; confirmed (flagged) wallets are
    always included. Returns rings ordered by size, each with its members, the
    evidence linking them and where the money left.
    """
    seeds = {**dict.fromkeys(engine.flagged, 1.0), **suspicious}
    seeds = {w: s for w, s in seeds.items() if w in engine.wallets}
    graph = nx.Graph()
    graph.add_nodes_from(seeds)
    # A wallet that already had its own handset before appearing on a ring's handset
    # was most likely taken over: it is a victim, kept apart from the members.
    taken_over: dict[str, set[str]] = {}
    for wallet in seeds:
        state = engine.wallets[wallet]
        for device in state.devices:
            users = engine.device_wallets.get(device, ())
            if not 1 < len(users) <= MAX_DEVICE_WALLETS:
                continue
            for other in users:
                if other == wallet:
                    continue
                if other not in seeds and next(iter(engine.wallets[other].devices)) != device:
                    taken_over.setdefault(wallet, set()).add(other)
                else:
                    graph.add_edge(wallet, other, kind="device", device=device)
        for other in state.recipients:
            if other in seeds and not graph.has_edge(wallet, other):
                graph.add_edge(wallet, other, kind="transfer")

    rings = []
    for members in nx.connected_components(graph):
        if len(members) < min_size:
            continue
        sub = graph.subgraph(members)
        devices = sorted({d["device"] for _, _, d in sub.edges(data=True) if d["kind"] == "device"})
        agents: Counter[str] = Counter()
        received = 0.0
        for wallet in members:
            state = engine.wallets[wallet]
            agents.update(state.agents_used)
            received += state.sum_in
        rings.append(
            {
                "wallets": sorted(members),
                "size": len(members),
                "confirmed": sorted(w for w in members if w in engine.flagged),
                "linked_only": sorted(w for w in members if w not in seeds),
                "takeover_victims": sorted(
                    set().union(*(taken_over.get(w, ()) for w in members)) - members
                ),
                "shared_devices": devices,
                "transfer_links": sum(
                    1 for _, _, d in sub.edges(data=True) if d["kind"] == "transfer"
                ),
                "cash_out_agents": [
                    {"agent_id": a, "cash_outs": n} for a, n in agents.most_common(5)
                ],
                "received_total": round(received),
                "max_score": round(max(seeds.get(w, 0.0) for w in members), 4),
                "edges": [
                    {"a": a, "b": b, "kind": d["kind"], "device": d.get("device")}
                    for a, b, d in sub.edges(data=True)
                ],
            }
        )
    rings.sort(key=lambda r: (-r["size"], r["wallets"][0]))
    for i, ring in enumerate(rings):
        ring["ring_id"] = f"R{i + 1:03d}"
    return rings

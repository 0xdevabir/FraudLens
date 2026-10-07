"""Keyrings: signing keys with ids, so a key can be replaced without a flag day.

A keyring is a JSON file, kept wherever the deployment keeps secrets (a mounted
Docker or Kubernetes secret, a file only the service user can read):

    {
      "keys": {
        "jwt-20261007-3fa2":  {"secret": "...", "created": "2026-10-07T09:00:00Z"},
        "jwt-20260901-91c0":  {"secret": "...", "created": "...", "retire_at": "..."},
        "upay-20261007-77d1": {"secret": "...", "created": "...", "partner": "upay"}
      },
      "partners": {"upay": {"callback_url": "https://core.upay.example/fraudlens"}}
    }

The newest unretired key signs; every unretired key verifies. A key past its
`retire_at` verifies nothing. The service re-reads the file when it changes, so a
rotation needs no restart. The JWT keyring holds keys without a partner; the
ingest keyring holds each partner's keys and, optionally, where to call it back.

    python -m fraudlens.platform.keys rotate-jwt secrets/jwt_keys.json
    python -m fraudlens.platform.keys rotate-partner secrets/ingest_keys.json --partner upay
    python -m fraudlens.platform.keys retire secrets/ingest_keys.json upay-20260901-91c0
    python -m fraudlens.platform.keys list secrets/ingest_keys.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

KID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PARTNER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
JWT_PREFIX = "jwt"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Key:
    kid: str
    secret: str = field(repr=False)
    created: datetime
    partner: str | None = None
    retire_at: datetime | None = None

    def active(self, now: datetime | None = None) -> bool:
        return self.retire_at is None or (now or _now()) < self.retire_at


@dataclass
class Keyring:
    keys: dict[str, Key] = field(default_factory=dict)
    partners: dict[str, dict] = field(default_factory=dict)

    # ------------------------------------------------------------- reading

    def verifying(self, kid: str, now: datetime | None = None) -> Key | None:
        key = self.keys.get(kid)
        return key if key is not None and key.active(now) else None

    def signing(self, partner: str | None = None, now: datetime | None = None) -> Key | None:
        """The newest active key of this partner (None: the keys without a partner)."""
        own = [k for k in self.keys.values() if k.partner == partner and k.active(now)]
        return max(own, key=lambda k: (k.created, k.kid)) if own else None

    def callback_url(self, partner: str) -> str | None:
        return (self.partners.get(partner) or {}).get("callback_url")

    # ------------------------------------------------------------- changing

    def add(self, prefix: str, partner: str | None = None, now: datetime | None = None) -> Key:
        now = now or _now()
        while True:
            kid = f"{prefix}-{now:%Y%m%d}-{secrets.token_hex(2)}"
            if kid not in self.keys:
                break
        key = Key(kid, secrets.token_hex(32), now, partner)
        self.keys[kid] = key
        return key

    def retire(self, kid: str, at: datetime) -> None:
        key = self.keys[kid]
        if key.retire_at is None or at < key.retire_at:
            self.keys[kid] = Key(key.kid, key.secret, key.created, key.partner, at)

    def prune(self, now: datetime | None = None) -> list[str]:
        """Drop keys already retired: nothing can be verified with them any more."""
        gone = [kid for kid, key in self.keys.items() if not key.active(now)]
        for kid in gone:
            del self.keys[kid]
        return gone

    # ------------------------------------------------------------- storage

    @classmethod
    def from_json(cls, text: str) -> Keyring:
        raw = json.loads(text)
        ring = cls(partners=dict(raw.get("partners") or {}))
        for kid, entry in (raw.get("keys") or {}).items():
            if not KID.fullmatch(kid):
                raise ValueError(f"key id {kid!r} is not valid")
            partner = entry.get("partner")
            if partner is not None and not PARTNER.fullmatch(partner):
                raise ValueError(f"partner name {partner!r} is not valid")
            secret = entry["secret"]
            if not isinstance(secret, str) or not secret:
                raise ValueError(f"key {kid} has no secret")
            created = _parse(entry.get("created")) or datetime(1970, 1, 1, tzinfo=UTC)
            ring.keys[kid] = Key(kid, secret, created, partner, _parse(entry.get("retire_at")))
        return ring

    def to_json(self) -> str:
        keys = {}
        for kid, k in sorted(self.keys.items()):
            entry = {"secret": k.secret, "created": _iso(k.created)}
            if k.partner:
                entry["partner"] = k.partner
            if k.retire_at:
                entry["retire_at"] = _iso(k.retire_at)
            keys[kid] = entry
        return json.dumps({"keys": keys, "partners": self.partners}, indent=2) + "\n"

    def save(self, path: Path) -> None:
        """Write atomically, readable by the owner only."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w") as out:
                out.write(self.to_json())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


_cache: dict[Path, tuple[tuple[int, int], Keyring]] = {}
_cache_lock = threading.Lock()


def load_keyring(path: Path) -> Keyring:
    """The keyring at `path`, re-read only when the file has changed."""
    path = Path(path)
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        cached = _cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
    ring = Keyring.from_json(path.read_text())
    with _cache_lock:
        _cache[path] = (stamp, ring)
    return ring


def _open(path: Path) -> Keyring:
    return Keyring.from_json(path.read_text()) if path.exists() else Keyring()


def rotate_jwt(path: Path, ttl_minutes: int, now: datetime | None = None) -> Key:
    """A new signing key. The old ones keep verifying until the tokens they signed expire."""
    now = now or _now()
    ring = _open(path)
    ring.prune(now)
    for key in list(ring.keys.values()):
        if key.partner is None:
            ring.retire(key.kid, now + timedelta(minutes=ttl_minutes, seconds=60))
    new = ring.add(JWT_PREFIX, now=now)
    ring.save(path)
    return new


def rotate_partner(
    path: Path,
    partner: str,
    grace: timedelta,
    callback_url: str | None = None,
    now: datetime | None = None,
) -> Key:
    """A new key for a partner. Its old keys stay valid for `grace`, time to switch over."""
    if not PARTNER.fullmatch(partner):
        raise ValueError(f"partner name {partner!r} is not valid")
    now = now or _now()
    ring = _open(path)
    ring.prune(now)
    for key in list(ring.keys.values()):
        if key.partner == partner:
            ring.retire(key.kid, now + grace)
    new = ring.add(partner, partner, now)
    entry = ring.partners.setdefault(partner, {})
    if callback_url is not None:
        entry["callback_url"] = callback_url or None
    ring.save(path)
    return new


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    jwt_cmd = sub.add_parser(
        "rotate-jwt", help="new token-signing key; old ones verify until expiry"
    )
    jwt_cmd.add_argument("path", type=Path)
    jwt_cmd.add_argument("--ttl-minutes", type=int, default=None, help="default: from settings")
    partner_cmd = sub.add_parser("rotate-partner", help="new HMAC key for an ingest partner")
    partner_cmd.add_argument("path", type=Path)
    partner_cmd.add_argument("--partner", required=True)
    partner_cmd.add_argument("--grace-hours", type=float, default=24.0)
    partner_cmd.add_argument("--callback-url", default=None, help="'' removes it")
    retire_cmd = sub.add_parser("retire", help="stop accepting one key now (a leaked key)")
    retire_cmd.add_argument("path", type=Path)
    retire_cmd.add_argument("kid")
    list_cmd = sub.add_parser("list", help="key ids and their dates, never the secrets")
    list_cmd.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    if args.command == "rotate-jwt":
        if args.ttl_minutes is None:
            from ..config import Settings

            args.ttl_minutes = Settings().jwt_ttl_minutes
        key = rotate_jwt(args.path, args.ttl_minutes)
        print(f"signing with {key.kid}; earlier keys verify for {args.ttl_minutes} more minutes")
    elif args.command == "rotate-partner":
        grace = timedelta(hours=args.grace_hours)
        key = rotate_partner(args.path, args.partner, grace, args.callback_url)
        # The one time the secret is shown: hand it to the partner over a secure channel.
        print(f"key id: {key.kid}\nsecret: {key.secret}")
        print(f"the partner's earlier keys stay valid for {args.grace_hours:g} hours")
    elif args.command == "retire":
        ring = _open(args.path)
        if args.kid not in ring.keys:
            parser.error(f"no key {args.kid} in {args.path}")
        ring.retire(args.kid, _now())
        ring.save(args.path)
        print(f"{args.kid} is no longer accepted")
    else:
        ring = _open(args.path)
        now = _now()
        for kid, k in sorted(ring.keys.items(), key=lambda item: item[1].created):
            state = "active" if k.active(now) else "retired"
            until = f" until {_iso(k.retire_at)}" if k.retire_at and k.active(now) else ""
            owner = k.partner or "tokens"
            print(f"{kid:28} {owner:12} created {_iso(k.created)}  {state}{until}")


if __name__ == "__main__":
    main()

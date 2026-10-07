"""Passwords, access tokens and their keys, client addresses, rate limits, production checks."""

from __future__ import annotations

import functools
import ipaddress
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from redis import Redis

from ..config import DEV_JWT_SECRET, Settings
from .keys import Key, Keyring, load_keyring

ALGORITHM = "HS256"
DEFAULT_KID = "default"  # the key id of FRAUDLENS_JWT_SECRET when there is no keyring
MIN_KEY_LENGTH = 32
ISSUER = "fraudlens"
MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()
# Verified against when the username does not exist, so a wrong username costs
# the same time as a wrong password and the two cannot be told apart by timing.
_DUMMY_HASH = _hasher.hash("no-such-user")


class TokenError(Exception):
    """The access token is missing, malformed, expired or signed with another key."""


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (Argon2Error, InvalidHashError):
        return False


def _jwt_keyring(settings: Settings) -> Keyring:
    """The keyring file when there is one; otherwise the single secret, as key `default`."""
    if settings.jwt_keyring is not None:
        return load_keyring(settings.jwt_keyring)
    secret = settings.jwt_secret.get_secret_value()
    return Keyring({DEFAULT_KID: Key(DEFAULT_KID, secret, datetime(1970, 1, 1, tzinfo=UTC))})


def issue_token(user_id: int, role: str, settings: Settings, now: datetime | None = None) -> str:
    key = _jwt_keyring(settings).signing()
    if key is None:
        raise RuntimeError("the JWT keyring has no active key: run `keys rotate-jwt`")
    now = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role,
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
        "jti": uuid.uuid4().hex,  # names this one token, so signing out can revoke it
    }
    # `kid` names the key, so a token outlives a rotation until it expires.
    return jwt.encode(claims, key.secret, algorithm=ALGORITHM, headers={"kid": key.kid})


def read_token(token: str, settings: Settings) -> dict:
    try:
        kid = jwt.get_unverified_header(token).get("kid", DEFAULT_KID)
        key = _jwt_keyring(settings).verifying(kid) if isinstance(kid, str) else None
        if key is None:
            raise TokenError("signed with an unknown or retired key")
        return jwt.decode(
            token,
            key.secret,
            algorithms=[ALGORITHM],  # never trust the algorithm named in the token
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


def _revoked_key(claims: dict) -> str:
    return f"fraudlens:revoked:{claims['jti']}"


def revoke_token(redis: Redis, claims: dict) -> None:
    """Refuse this token from now on. The entry lasts exactly as long as the token would have."""
    redis.set(_revoked_key(claims), "1", ex=max(int(claims["exp"] - time.time()) + 1, 1))


def is_revoked(redis: Redis, claims: dict) -> bool:
    """Raises when Redis cannot be asked: a token nobody can vouch for is not accepted."""
    return bool(redis.exists(_revoked_key(claims)))


class RateLimiter:
    """Fixed-window counter in Redis: at most `limit` hits per `window` seconds per key."""

    def __init__(self, redis: Redis, name: str, limit: int, window: int) -> None:
        self.redis, self.name, self.limit, self.window = redis, name, limit, window

    def _key(self, key: str) -> str:
        return f"fraudlens:limit:{self.name}:{key}"

    def blocked(self, key: str) -> bool:
        count = self.redis.get(self._key(key))
        return count is not None and int(count) >= self.limit

    def hit(self, key: str) -> bool:
        """Count one attempt. False when the limit was already reached."""
        pipe = self.redis.pipeline()
        pipe.incr(self._key(key))
        pipe.expire(self._key(key), self.window, nx=True)
        count, _ = pipe.execute()
        return count <= self.limit

    def reset(self, key: str) -> None:
        self.redis.delete(self._key(key))


Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@functools.cache
def _proxies(entries: tuple[str, ...]) -> tuple[Network, ...]:
    nets = tuple(ipaddress.ip_network(e.strip(), strict=False) for e in entries)
    if any(net.prefixlen == 0 for net in nets):
        raise ValueError("FRAUDLENS_TRUSTED_PROXIES must not trust every address")
    return nets


def proxies(entries: list[str]) -> tuple[Network, ...]:
    return _proxies(tuple(entries))


def _parse_ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def client_address(peer: str | None, forwarded_for: list[str], trusted: list[str]) -> str | None:
    """The address a request came from. `X-Forwarded-For` is believed only as far
    back as it was written by trusted proxies: walking from the nearest hop, the
    first address that is not one of ours is the client. Anything to the left of it
    was written by the client and could say anything."""
    nets = proxies(trusted)
    if peer is None or not nets:
        return peer
    hop = _parse_ip(peer)
    if hop is None or not any(hop in net for net in nets):
        return peer  # not from our proxy: its header means nothing
    chain = [part for header in forwarded_for for part in header.split(",") if part.strip()]
    for part in reversed(chain):
        ip = _parse_ip(part)
        if ip is None:
            break  # a malformed hop: stop at the last address we could vouch for
        hop = ip
        if not any(ip in net for net in nets):
            break
    return str(hop)


def check_production(settings: Settings) -> None:
    """Refuse to run a production service on development defaults."""
    proxies(settings.trusted_proxies)  # malformed or catch-all entries fail everywhere
    if not settings.production:
        return
    if settings.jwt_keyring is None:
        secret = settings.jwt_secret.get_secret_value()
        if secret == DEV_JWT_SECRET or len(secret) < MIN_KEY_LENGTH:
            raise RuntimeError("FRAUDLENS_JWT_SECRET must be a private key of 32+ characters")
    else:
        ring = load_keyring(settings.jwt_keyring)
        if ring.signing() is None:
            raise RuntimeError("FRAUDLENS_JWT_KEYRING has no active key")
        if any(k.secret == DEV_JWT_SECRET or len(k.secret) < MIN_KEY_LENGTH
               for k in ring.keys.values()):  # fmt: skip
            raise RuntimeError("every key in FRAUDLENS_JWT_KEYRING must be 32+ characters")
    if settings.ingest_keyring is not None:
        ring = load_keyring(settings.ingest_keyring)
        if any(len(k.secret) < MIN_KEY_LENGTH for k in ring.keys.values()):
            raise RuntimeError("every key in FRAUDLENS_INGEST_KEYRING must be 32+ characters")
        for partner in ring.partners:
            url = ring.callback_url(partner)
            if url and urlsplit(url).scheme != "https":
                raise RuntimeError(f"the callback for {partner} must use https")
    if "fraudlens_dev" in settings.database_url:
        raise RuntimeError("FRAUDLENS_DATABASE_URL still uses the development password")
    if not urlsplit(settings.redis_url).password:
        raise RuntimeError("FRAUDLENS_REDIS_URL must carry a password")
    if "*" in settings.cors_origins:
        raise RuntimeError("FRAUDLENS_CORS_ORIGINS must list the allowed origins")
    if settings.demo_login:
        raise RuntimeError("FRAUDLENS_DEMO_LOGIN signs people in without a password")

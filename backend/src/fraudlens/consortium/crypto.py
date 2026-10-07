"""The consortium's cryptography, from the standard library only.

Three primitives:

- an **oblivious PRF** (OPRF). A provider turns a wallet number into a token
  without the hub seeing the number, and without being able to make tokens on its
  own. The hub holds the key `k`; the provider sends the hashed identifier blinded
  by a random factor, the hub raises it to `k`, the provider removes the blind.
  Because tokens can only be made one rate-limited, audited request at a time,
  no member can hash every 01XXXXXXXXX number offline and reverse the tokens.
- **Schnorr signatures**, so a bundle is attributable to the provider that issued
  it (the hub relays bundles but cannot forge or alter a listing).
- a **Bloom filter**, for suspected-but-unconfirmed wallets: partners can test a
  token against it but cannot list its contents, and a hit is deniable by design.

Group: the 2048-bit MODP group of RFC 3526 (group 14), a safe prime p = 2q + 1,
working in the subgroup of quadratic residues of prime order q. Secret exponents
are 256 bits (short exponents give 128-bit security in this group).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import secrets
from dataclasses import dataclass


def _pi_bits(bits: int) -> int:
    """floor(pi * 2**bits), by Machin's formula on integers."""
    guard = 64
    one = 1 << (bits + guard)

    def arctan_inv(x: int) -> int:
        total, term, n, sign = 0, one // x, 1, 1
        while term:
            total += sign * (term // n)
            term //= x * x
            n += 2
            sign = -sign
        return total

    return (4 * (4 * arctan_inv(5) - arctan_inv(239))) >> guard


# RFC 3526 section 3: p = 2^2048 - 2^1984 - 1 + 2^64 * ( [2^1918 pi] + 124476 ).
P = 2**2048 - 2**1984 - 1 + 2**64 * (_pi_bits(1918) + 124476)
Q = (P - 1) // 2
G = 4  # 2 squared: a generator of the order-q subgroup
P_BYTES = 256
EXP_BITS = 256

# A cheap check that the constant came out right (the published prime's last bytes).
assert P % 2**64 == 2**64 - 1 and hex(P).startswith("0xffffffffffffffffc90fdaa22168c234")


def _expand(domain: bytes, data: bytes, size: int) -> int:
    return int.from_bytes(hashlib.shake_256(domain + b"\x00" + data).digest(size), "big")


def hash_to_group(data: bytes) -> int:
    """Map bytes to the order-q subgroup with no known discrete log."""
    h = _expand(b"FL-CONS-H2G-v1", data, P_BYTES + 16) % P
    return pow(h, 2, P) or G  # squaring lands in the quadratic residues


def _jacobi(a: int, n: int) -> int:
    """The Jacobi symbol (a/n) for odd n > 0, by quadratic reciprocity."""
    a %= n
    result = 1
    while a:
        twos = (a & -a).bit_length() - 1
        a >>= twos
        if twos & 1 and n % 8 in (3, 5):
            result = -result
        if a % 4 == 3 and n % 4 == 3:
            result = -result
        a, n = n % a, a
    return result if n == 1 else 0


def in_group(x: int) -> bool:
    """Is x in the order-q subgroup? For a safe prime that means a quadratic residue.

    The same answer as pow(x, Q, P) == 1, far cheaper.
    """
    return 1 < x < P and _jacobi(x, P) == 1


def random_exponent() -> int:
    return secrets.randbits(EXP_BITS) | 1 << (EXP_BITS - 1)


# --------------------------------------------------------------------- OPRF


@dataclass(frozen=True)
class OprfKey:
    """The hub's secret for one key epoch. `public` lets clients unblind."""

    epoch: str
    secret: int

    @property
    def public(self) -> int:
        return pow(G, self.secret, P)

    @staticmethod
    def generate(epoch: str) -> OprfKey:
        return OprfKey(epoch, random_exponent())

    def evaluate(self, blinded: int) -> int:
        """The hub's half: raise a blinded element to the key."""
        if not in_group(blinded):
            raise ValueError("not an element of the group")
        return pow(blinded, self.secret, P)


def blind(element: int) -> tuple[int, int]:
    """Client: hide `element` as element * g^r. Returns (blinded, r)."""
    r = random_exponent()
    return element * pow(G, r, P) % P, r


def unblind(evaluated: int, r: int, public: int) -> int:
    """Client: (element * g^r)^k / (g^k)^r = element^k."""
    return evaluated * pow(pow(public, r, P), -1, P) % P


def token_from_element(element_k: int, epoch: str, kind: str) -> str:
    """The shared token: a hash of element^k, bound to the key epoch and identifier kind."""
    raw = element_k.to_bytes(P_BYTES, "big")
    digest = hashlib.sha256(b"FL-CONS-TOKEN-v1|" + f"{epoch}|{kind}|".encode() + raw)
    return digest.hexdigest()[:32]  # 128 bits


# --------------------------------------------------------------- signatures


@dataclass(frozen=True)
class SigningKey:
    secret: int

    @staticmethod
    def generate() -> SigningKey:
        return SigningKey(random_exponent())

    @property
    def public(self) -> int:
        return pow(G, self.secret, P)

    def sign(self, message: bytes) -> str:
        # Deterministic nonce (as in RFC 6979): no randomness to get wrong.
        nonce_seed = self.secret.to_bytes(P_BYTES, "big") + message
        r = _expand(b"FL-CONS-NONCE-v1", nonce_seed, 48) % Q or 1
        commitment = pow(G, r, P)
        e = _challenge(commitment, self.public, message)
        s = (r + e * self.secret) % Q
        return f"{e:064x}.{s:x}"


def _challenge(commitment: int, public: int, message: bytes) -> int:
    data = commitment.to_bytes(P_BYTES, "big") + public.to_bytes(P_BYTES, "big") + message
    return int.from_bytes(hashlib.sha256(b"FL-CONS-SIG-v1" + data).digest(), "big")


def verify(public: int, message: bytes, signature: str) -> bool:
    try:
        e_hex, s_hex = signature.split(".")
        e, s = int(e_hex, 16), int(s_hex, 16)
    except ValueError:
        return False
    if not (0 < s < Q and 1 < public < P):
        return False
    commitment = pow(G, s, P) * pow(pow(public, e, P), -1, P) % P
    return hmac.compare_digest(f"{_challenge(commitment, public, message):064x}", e_hex)


def fingerprint(public: int) -> str:
    """Short, stable name for a public key, for audit lines and screens."""
    return hashlib.sha256(public.to_bytes(P_BYTES, "big")).hexdigest()[:16]


# ------------------------------------------------------------- Bloom filter


class BloomFilter:
    """A set of tokens that can be tested but not listed."""

    def __init__(self, m: int, k: int, bits: bytearray | None = None, n: int = 0) -> None:
        self.m, self.k, self.n = m, k, n
        self.bits = bits if bits is not None else bytearray((m + 7) // 8)

    @staticmethod
    def sized(capacity: int, fp_rate: float) -> BloomFilter:
        capacity = max(capacity, 1)
        m = max(1024, math.ceil(-capacity * math.log(fp_rate) / math.log(2) ** 2))
        k = max(1, min(32, round(m / capacity * math.log(2))))
        return BloomFilter(m, k)

    def _positions(self, token: str) -> list[int]:
        # k independent positions from one extendable-output hash (double hashing
        # modulo a small, even m correlates the positions and inflates false hits).
        d = hashlib.shake_256(b"FL-CONS-BLOOM-v1" + token.encode()).digest(8 * self.k)
        return [int.from_bytes(d[8 * i : 8 * i + 8], "big") % self.m for i in range(self.k)]

    def add(self, token: str) -> None:
        for pos in self._positions(token):
            self.bits[pos >> 3] |= 1 << (pos & 7)
        self.n += 1

    def __contains__(self, token: str) -> bool:
        return all(self.bits[pos >> 3] >> (pos & 7) & 1 for pos in self._positions(token))

    def expected_fp_rate(self) -> float:
        return (1 - math.exp(-self.k * self.n / self.m)) ** self.k

    def to_dict(self) -> dict:
        return {
            "m": self.m,
            "k": self.k,
            "n": self.n,
            "bits": base64.b64encode(bytes(self.bits)).decode(),
        }

    @staticmethod
    def from_dict(d: dict) -> BloomFilter:
        bits = bytearray(base64.b64decode(d["bits"]))
        if len(bits) != (d["m"] + 7) // 8:
            raise ValueError("Bloom filter size does not match its header")
        return BloomFilter(int(d["m"]), int(d["k"]), bits, int(d["n"]))

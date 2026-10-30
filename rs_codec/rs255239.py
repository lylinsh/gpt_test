"""Systematic RS(255,239), b=0; classical BM, Chien and Forney decoder."""

from dataclasses import dataclass
from enum import IntEnum
import random

from .gf256 import EXP, LOG, gf_mul, gf_div, gf_inv, poly_eval_low, poly_mul, solve

N, K, NSYM, T = 255, 239, 16, 8
SEED = 20261030
EXPECTED_GENERATOR = bytes.fromhex("01 3B 0D 68 BD 44 D1 1E 08 A3 41 29 E5 62 32 24 3B")


def generator():
    result = [1]
    for j in range(NSYM):
        result = poly_mul(result, [1, EXP[j]])
    return bytes(result)


GENERATOR = generator()


def _block(data, length):
    data = bytes(data)
    if len(data) != length:
        raise ValueError(f"expected {length} bytes, got {len(data)}")
    return data


def encode_long(data):
    data = _block(data, K)
    work = list(data) + [0] * NSYM
    for i in range(K):
        factor = work[i]
        for j, coefficient in enumerate(GENERATOR):
            work[i + j] ^= gf_mul(factor, coefficient)
    return data + bytes(work[K:])


def encode(data):
    """Byte-at-a-time LFSR, independent of the long division work buffer."""
    data = _block(data, K)
    parity = [0] * NSYM
    for value in data:
        feedback = value ^ parity[0]
        parity = parity[1:] + [0]
        for j in range(NSYM):
            parity[j] ^= gf_mul(feedback, GENERATOR[j + 1])
    return data + bytes(parity)


def syndromes(received):
    received = _block(received, N)
    # Sparse evaluation speeds exhaustive all-zero single-error regression.
    terms = [(N - 1 - p, LOG[v]) for p, v in enumerate(received) if v]
    return tuple(
        _xor(EXP[(degree * j + logv) % 255] for degree, logv in terms)
        for j in range(NSYM)
    )


def _xor(values):
    result = 0
    for value in values:
        result ^= value
    return result


def berlekamp_massey(s):
    """Return low-degree-first locator with constant coefficient one."""
    c, b = [1] + [0] * NSYM, [1] + [0] * NSYM
    degree, shift, previous = 0, 1, 1
    for n in range(NSYM):
        discrepancy = s[n]
        for i in range(1, degree + 1):
            discrepancy ^= gf_mul(c[i], s[n - i])
        if not discrepancy:
            shift += 1
            continue
        old = c[:]
        scale = gf_div(discrepancy, previous)
        for j in range(NSYM + 1 - shift):
            c[j + shift] ^= gf_mul(scale, b[j])
        if 2 * degree <= n:
            degree, b, previous, shift = n + 1 - degree, old, discrepancy, 1
        else:
            shift += 1
    return c[: degree + 1]


class Status(IntEnum):
    CLEAN = 0
    CORRECTED = 1
    DECODE_FAILURE = 2


@dataclass(frozen=True)
class DecodeResult:
    data: bytes
    codeword: bytes
    status: Status
    error_count: int
    positions: tuple = ()
    magnitudes: tuple = ()
    reason: str = ""


def decode(received):
    received = _block(received, N)
    s = syndromes(received)
    if not any(s):
        return DecodeResult(received[:K], received, Status.CLEAN, 0)

    def failure(reason):
        return DecodeResult(
            received[:K], received, Status.DECODE_FAILURE, 0, reason=reason
        )

    locator = berlekamp_massey(s)
    degree = len(locator) - 1
    if not 1 <= degree <= T or not locator[-1]:
        return failure("locator degree outside 1..8")
    if degree == 1:
        # Equivalent exact solution of 1+X*z=0, no 255-position scan needed.
        positions = [254 - LOG[locator[1]]]
    else:
        positions = [
            p for p in range(N) if poly_eval_low(locator, gf_inv(EXP[254 - p])) == 0
        ]
    if len(positions) != degree:
        return failure("root count differs from locator degree")
    omega = poly_mul(s, locator)[:NSYM]
    derivative = [locator[i] if i & 1 else 0 for i in range(1, len(locator))]
    corrected = bytearray(received)
    magnitudes = []
    for p in positions:
        x = EXP[254 - p]
        z = gf_inv(x)
        denominator = poly_eval_low(derivative, z)
        if not denominator:
            return failure("zero Forney denominator")
        error = gf_div(gf_mul(x, poly_eval_low(omega, z)), denominator)
        if not error:
            return failure("zero error magnitude")
        corrected[p] ^= error
        magnitudes.append(error)
    if any(syndromes(corrected)):
        return failure("nonzero post-correction syndromes")
    return DecodeResult(
        bytes(corrected[:K]),
        bytes(corrected),
        Status.CORRECTED,
        degree,
        tuple(positions),
        tuple(magnitudes),
    )


def solve_error_values(received, positions):
    """Known-position independent oracle using Vandermonde equations, not Forney."""
    received = _block(received, N)
    positions = tuple(positions)
    if len(set(positions)) != len(positions) or any(not 0 <= p < N for p in positions):
        raise ValueError("invalid error positions")
    count = len(positions)
    if count > NSYM:
        raise ValueError("at most 16 known errors")
    matrix = [[EXP[((254 - p) * j) % 255] for p in positions] for j in range(count)]
    return tuple(solve(matrix, syndromes(received)[:count]))


def inject_errors(codeword, positions, values):
    result = bytearray(_block(codeword, N))
    positions, values = tuple(positions), tuple(values)
    if len(positions) != len(values) or len(set(positions)) != len(positions):
        raise ValueError("one nonzero mask per distinct position required")
    for p, value in zip(positions, values):
        if not 0 <= p < N or not 1 <= value <= 255:
            raise ValueError("invalid position or XOR mask")
        result[p] ^= value
    return bytes(result)


def random_errors(codeword, count, rng=None):
    rng = rng if rng is not None else random.Random(SEED)
    positions = rng.sample(range(N), count)
    values = [rng.randrange(1, 256) for _ in positions]
    return inject_errors(codeword, positions, values), tuple(positions), tuple(values)

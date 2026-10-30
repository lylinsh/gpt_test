"""Standard-library reference models for an 800 Gbps coherent optical RS boundary."""

from .rs255239 import (
    N,
    K,
    T,
    SEED,
    Status,
    DecodeResult,
    encode,
    encode_long,
    decode,
    syndromes,
    inject_errors,
    random_errors,
)

__all__ = [
    "N",
    "K",
    "T",
    "SEED",
    "Status",
    "DecodeResult",
    "encode",
    "encode_long",
    "decode",
    "syndromes",
    "inject_errors",
    "random_errors",
]

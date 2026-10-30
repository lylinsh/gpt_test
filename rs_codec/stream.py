"""Cycle-stepped behavioral scheduler; latency/II are assumptions, not RTL evidence.

step() returns signals immediately before the active edge, then applies handshakes.
All lanes and TX/RX have independent storage and flow control.
"""

from collections import deque
from dataclasses import dataclass
from functools import lru_cache

from .rs255239 import K, N, Status, decode, encode

MAX64 = (1 << 64) - 1


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Beat:
    data: bytes  # exactly 32 bytes, lane 0 first
    keep: int
    sop: bool
    eop: bool
    cw_id: int
    status: Status = Status.CLEAN
    error_count: int = 0

    @property
    def data_int(self):
        return int.from_bytes(self.data, "little")


@dataclass(frozen=True)
class Signals:
    s_ready: bool
    m_valid: bool
    output: Beat | None


def pack_beats(block, cw_id, status=Status.CLEAN, error_count=0):
    block = bytes(block)
    if len(block) not in (K, N) or not 0 <= cw_id <= 0xFFFFFFFF:
        raise ValueError("239/255-byte block and unsigned 32-bit ID required")
    return tuple(
        Beat(
            block[i : i + 32].ljust(32, b"\0"),
            (1 << len(block[i : i + 32])) - 1,
            i == 0,
            i == 224,
            cw_id,
            status,
            error_count,
        )
        for i in range(0, len(block), 32)
    )


@lru_cache(maxsize=256)
def _transform(mode, block):
    # Pure-function memoization only speeds the simulator for repeated payloads.
    if mode == "encode":
        return encode(block), Status.CLEAN, 0
    result = decode(block)
    return result.data, result.status, result.error_count


class StreamCore:
    def __init__(self, mode, slots=8, latency=None):
        if mode not in ("encode", "decode") or slots < 1:
            raise ValueError("invalid mode or slot depth")
        self.mode, self.slots = mode, slots
        self.latency = (16 if mode == "encode" else 48) if latency is None else latency
        if self.latency < 8:
            raise ValueError("store-and-forward latency must be at least 8 cycles")
        self.cycle = 0
        self.reset()

    def reset(self):
        self.pending = deque()
        self.partial = []
        self.occupied = 0
        self.stalled_input = None
        self.stats = dict.fromkeys(
            (
                "cw_in",
                "cw_out",
                "cw_clean",
                "cw_corrected",
                "cw_failed",
                "symbols_corrected",
            ),
            0,
        )

    def _inc(self, name, amount=1):
        self.stats[name] = min(MAX64, self.stats[name] + amount)

    def step(self, source=None, m_ready=True, rst_n=True, stats_clear=False):
        """source=None means s_valid=0. Hold source unchanged until accepted.

        Synchronous reset has priority and suppresses both handshake signals.
        stats_clear does not discard in-flight blocks and dominates count events.
        """
        if not rst_n:
            self.reset()
            self.cycle += 1
            return Signals(False, False, None)
        if self.stalled_input is not None and source != self.stalled_input:
            raise ProtocolError("input changed or valid dropped while stalled")
        ready = bool(self.partial) or self.occupied < self.slots
        output = None
        if self.pending and self.pending[0][0] <= self.cycle:
            _, beats, index = self.pending[0]
            output = beats[index]
        signals = Signals(ready, output is not None, output)
        self.stalled_input = source if source is not None and not ready else None
        if source is not None and ready:
            self._accept(source, stats_clear)
        if output is not None and m_ready:
            job = self.pending[0]
            job[2] += 1
            if output.eop:
                self.pending.popleft()
                self.occupied -= 1
                if not stats_clear:
                    self._inc("cw_out")
                    if self.mode == "decode":
                        name = {
                            Status.CLEAN: "cw_clean",
                            Status.CORRECTED: "cw_corrected",
                            Status.DECODE_FAILURE: "cw_failed",
                        }[output.status]
                        self._inc(name)
                        self._inc("symbols_corrected", output.error_count)
        if stats_clear:
            for name in self.stats:
                self.stats[name] = 0
        self.cycle += 1
        return signals

    def _accept(self, beat, stats_clear):
        index = len(self.partial)
        length = K if self.mode == "encode" else N
        count = 32 if index < 7 else length - 224
        if (
            len(beat.data) != 32
            or beat.keep != (1 << count) - 1
            or beat.sop != (index == 0)
            or beat.eop != (index == 7)
            or not 0 <= beat.cw_id <= 0xFFFFFFFF
        ):
            raise ProtocolError(f"invalid beat format at index {index}")
        if index and beat.cw_id != self.partial[0].cw_id:
            raise ProtocolError("ID changed within codeword")
        if not index:
            self.first_cycle = self.cycle
            self.occupied += 1
        self.partial.append(beat)
        if index == 7:
            block = b"".join(b.data[: b.keep.bit_count()] for b in self.partial)
            result, status, errors = _transform(self.mode, block)
            beats = pack_beats(result, beat.cw_id, status, errors)
            self.pending.append(
                [max(self.first_cycle + self.latency, self.cycle + 1), beats, 0]
            )
            self.partial = []
            if not stats_clear:
                self._inc("cw_in")


class Duplex:
    def __init__(self, lanes=8, slots=8):
        if lanes not in (1, 8):
            raise ValueError("v1 supports 1 or 8 lanes")
        self.tx = [StreamCore("encode", slots) for _ in range(lanes)]
        self.rx = [StreamCore("decode", slots) for _ in range(lanes)]

    def step(
        self,
        tx_sources=None,
        rx_sources=None,
        tx_ready=None,
        rx_ready=None,
        rst_n=True,
        stats_clear=False,
    ):
        n = len(self.tx)
        tx_sources = [None] * n if tx_sources is None else tx_sources
        rx_sources = [None] * n if rx_sources is None else rx_sources
        tx_ready = [True] * n if tx_ready is None else tx_ready
        rx_ready = [True] * n if rx_ready is None else rx_ready
        if any(len(v) != n for v in (tx_sources, rx_sources, tx_ready, rx_ready)):
            raise ValueError("one signal per lane required")
        return tuple(
            [
                core.step(source, ready, rst_n, stats_clear)
                for core, source, ready in zip(cores, sources, readiness)
            ]
            for cores, sources, readiness in (
                (self.tx, tx_sources, tx_ready),
                (self.rx, rx_sources, rx_ready),
            )
        )

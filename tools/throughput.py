"""Reproducible assumed-cycle scheduler benchmark, NOT Python/RTL line rate."""

import json
from dataclasses import replace
from rs_codec import Status, decode, encode, inject_errors
from rs_codec.stream import Duplex, pack_beats


def run_scenario(scenario, window=10008):
    if window < 10000 or window % 9:
        raise ValueError("window must be >=10000 and divisible by 9")
    data = bytes(range(239))
    cw = encode(data)
    eight = inject_errors(
        cw, [0, 31, 32, 63, 64, 238, 239, 254], [1, 2, 3, 4, 5, 6, 7, 8]
    )
    failed = inject_errors(cw, list(range(9)), [1] * 9)
    assert decode(failed).status == Status.DECODE_FAILURE
    blocks = {
        "clean": [cw],
        "eight_errors": [eight],
        "failure": [failed],
        "mixed": [cw, eight, failed],
    }[scenario]
    outputs = [decode(block) for block in blocks]
    model = Duplex()
    tx_template = pack_beats(data, 0)
    tx_expected = pack_beats(cw, 0)
    rx_templates = [pack_beats(block, 0) for block in blocks]
    rx_expected = [pack_beats(r.data, 0, r.status, r.error_count) for r in outputs]
    start, end = 108, 108 + window
    counts = {
        direction: dict(cw_in=0, cw_out=0, input_bytes=0, output_bytes=0)
        for direction in ("tx", "rx")
    }
    output_index = {"tx": [0] * 8, "rx": [0] * 8}
    first_latencies, tail_latencies = (
        {"tx": set(), "rx": set()},
        {"tx": set(), "rx": set()},
    )
    for cycle in range(end + 80):
        cw_index, phase = divmod(cycle, 9)
        active = cycle < end and phase < 8
        cw_id = cw_index & 0xFFFFFFFF
        variant = cw_index % len(blocks)
        tx_source = replace(tx_template[phase], cw_id=cw_id) if active else None
        rx_source = (
            replace(rx_templates[variant][phase], cw_id=cw_id) if active else None
        )
        tx, rx = model.step([tx_source] * 8, [rx_source] * 8)
        for direction, signals, source in (
            ("tx", tx, tx_source),
            ("rx", rx, rx_source),
        ):
            for lane, signal in enumerate(signals):
                if active:
                    assert signal.s_ready, (
                        scenario,
                        direction,
                        lane,
                        cycle,
                        "II broken",
                    )
                if start <= cycle < end and active:
                    counts[direction]["input_bytes"] += source.keep.bit_count()
                    counts[direction]["cw_in"] += int(source.eop)
                if signal.m_valid:
                    beat = signal.output
                    index = output_index[direction][lane]
                    block_index, beat_index = divmod(index, 8)
                    expected = (
                        tx_expected
                        if direction == "tx"
                        else rx_expected[block_index % len(blocks)]
                    )[beat_index]
                    expected = replace(expected, cw_id=block_index & 0xFFFFFFFF)
                    assert beat == expected, (
                        scenario,
                        direction,
                        lane,
                        cycle,
                        "scoreboard mismatch",
                    )
                    output_index[direction][lane] += 1
                    if beat.sop:
                        first_latencies[direction].add(cycle - block_index * 9)
                    if beat.eop:
                        tail_latencies[direction].add(cycle - block_index * 9)
                    if start <= cycle < end:
                        counts[direction]["output_bytes"] += beat.keep.bit_count()
                        counts[direction]["cw_out"] += int(beat.eop)
    for direction, cores in (("tx", model.tx), ("rx", model.rx)):
        assert all(
            not core.pending and not core.partial and core.occupied == 0
            for core in cores
        )
        expected_cw = end // 9
        assert all(
            core.stats["cw_in"] == core.stats["cw_out"] == expected_cw for core in cores
        )
        count = counts[direction]
        assert count["cw_out"] == 8 * window // 9
        count.update(
            steady_cycles=window,
            assumed_clock_mhz=500,
            ii_cycles=9,
            input_effective_gbps=count["input_bytes"] * 8 * 0.5 / window,
            output_effective_gbps=count["output_bytes"] * 8 * 0.5 / window,
            information_gbps=count["cw_out"] * 239 * 8 * 0.5 / window,
            first_output_latency_cycles=sorted(first_latencies[direction]),
            tail_output_latency_cycles=sorted(tail_latencies[direction]),
            status_totals={
                name: sum(core.stats[name] for core in cores)
                for name in ("cw_clean", "cw_corrected", "cw_failed")
            },
        )
        assert count["information_gbps"] >= 800
    return counts


def main():
    report = {
        "interpretation": "Behavioral scheduler with assumed latency/II. Not measured RTL or CPU throughput.",
        "latency_origin": "accepted input SOP to output SOP/EOP valid; no backpressure",
        "seed": 20261030,
        "scenarios": {
            scenario: run_scenario(scenario)
            for scenario in ("clean", "eight_errors", "failure", "mixed")
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

from dataclasses import replace
import random
import unittest

from rs_codec import SEED, decode, encode, inject_errors
from rs_codec.budget import LinkBudget
from rs_codec.stream import Duplex, MAX64, ProtocolError, StreamCore, pack_beats


class Streaming(unittest.TestCase):
    def test_layout(self):
        data = bytes(range(239))
        info, code = pack_beats(data, 0xFFFFFFFF), pack_beats(encode(data), 0)
        self.assertEqual(info[7].keep, 0x7FFF)
        self.assertEqual(code[7].keep, 0x7FFFFFFF)
        self.assertEqual(code[7].data[15:31], encode(data)[239:])
        self.assertEqual(code[7].data[31], 0)
        self.assertEqual(info[0].data_int & 0xFFFF, 0x0100)

    def test_duplex_random_flow(self):
        rng = random.Random(SEED)
        model = Duplex()
        sources, expected, offsets = {}, {}, {}
        for direction in ("tx", "rx"):
            for lane in range(8):
                source, target = [], []
                for i in range(14):
                    data = rng.randbytes(239)
                    cw_id = (0xFFFFFFFA + i) & 0xFFFFFFFF
                    if direction == "tx":
                        block = data
                        target.extend(pack_beats(encode(data), cw_id))
                    else:
                        count = i % 10
                        positions = rng.sample(range(255), count)
                        block = inject_errors(encode(data), positions, [1 + i] * count)
                        result = decode(block)
                        target.extend(
                            pack_beats(
                                result.data, cw_id, result.status, result.error_count
                            )
                        )
                    source.extend(pack_beats(block, cw_id))
                sources[direction, lane], expected[direction, lane] = source, target
                offsets[direction, lane] = 0
        held = {}
        received = {key: [] for key in sources}
        stalled_outputs = {}
        progress_others = False
        for cycle in range(20000):
            all_inputs, all_ready = [], []
            for direction in ("tx", "rx"):
                ins, ready = [], []
                for lane in range(8):
                    key = direction, lane
                    if (
                        key not in held
                        and offsets[key] < len(sources[key])
                        and rng.random() > 0.2
                    ):
                        held[key] = sources[key][offsets[key]]
                    ins.append(held.get(key))
                    ready.append(
                        False if lane == 0 and cycle < 250 else rng.random() > 0.35
                    )
                all_inputs.append(ins)
                all_ready.append(ready)
            results = model.step(*all_inputs, *all_ready)
            for direction, signals, readiness in zip(("tx", "rx"), results, all_ready):
                for lane, (signal, ready) in enumerate(zip(signals, readiness)):
                    key = direction, lane
                    if key in stalled_outputs:
                        self.assertEqual(signal.output, stalled_outputs[key])
                    if signal.m_valid and not ready:
                        stalled_outputs[key] = signal.output
                    else:
                        stalled_outputs.pop(key, None)
                    if key in held and signal.s_ready:
                        offsets[key] += 1
                        held.pop(key)
                    if signal.m_valid and ready:
                        received[key].append(signal.output)
                        if lane != 0 and cycle < 250:
                            progress_others = True
            if all(len(received[key]) == len(expected[key]) for key in expected):
                break
        else:
            self.fail("flow timeout")
        self.assertTrue(progress_others)
        for key in expected:
            self.assertEqual(received[key], expected[key], key)
        for core in model.tx + model.rx:
            self.assertEqual(core.stats["cw_in"], 14)
            self.assertEqual(core.stats["cw_out"], 14)
        for core in model.rx:
            self.assertEqual(
                sum(core.stats[k] for k in ("cw_clean", "cw_corrected", "cw_failed")),
                14,
            )
            self.assertEqual(
                core.stats["symbols_corrected"],
                sum(
                    expected["rx", model.rx.index(core)][i].error_count
                    for i in range(7, 14 * 8, 8)
                ),
            )

    def test_reset_phases(self):
        for mode in ("encode", "decode"):
            block = bytes(239 if mode == "encode" else 255)
            beats = pack_beats(block, 123)
            for reset_cycle in (3, 10, 24, 48, 52, 60):
                core = StreamCore(mode)
                for cycle in range(reset_cycle):
                    core.step(beats[cycle] if cycle < 8 else None, m_ready=False)
                for _ in range(2):
                    signal = core.step(rst_n=False)
                    self.assertEqual(signal.s_ready, False)
                    self.assertEqual(signal.m_valid, False)
                for _ in range(80):
                    self.assertFalse(core.step().m_valid)
                for beat in pack_beats(block, 456):
                    self.assertTrue(core.step(beat).s_ready)
                output = []
                for _ in range(80):
                    signal = core.step()
                    if signal.m_valid:
                        output.append(signal.output)
                self.assertEqual(len(output), 8)
                self.assertTrue(all(beat.cw_id == 456 for beat in output))

    def test_protocol_monitor(self):
        beats = pack_beats(bytes(239), 7)
        for bad in (
            replace(beats[0], keep=5),
            replace(beats[0], sop=False),
            replace(beats[0], eop=True),
            replace(beats[0], data=b"\0"),
        ):
            with self.assertRaises(ProtocolError):
                StreamCore("encode").step(bad)
        core = StreamCore("encode")
        core.step(beats[0])
        with self.assertRaises(ProtocolError):
            core.step(replace(beats[1], cw_id=8))
        core = StreamCore("encode", slots=1)
        for beat in beats:
            core.step(beat, m_ready=False)
        self.assertFalse(core.step(beats[0], m_ready=False).s_ready)
        with self.assertRaises(ProtocolError):
            core.step(None, m_ready=False)
        core = StreamCore("encode")
        for beat in beats[:-1]:
            core.step(beat)
        with self.assertRaises(ProtocolError):
            core.step(replace(beats[-1], keep=0xFFFF))

    def test_stats_clear_saturation_and_stalled_tail(self):
        core = StreamCore("decode")
        block = inject_errors(encode(bytes(239)), [254], [128])
        for i, beat in enumerate(pack_beats(block, 1)):
            core.step(beat, stats_clear=i == 7)
        self.assertEqual(core.stats["cw_in"], 0)
        for _ in range(40):
            core.step()
        for _ in range(7):
            core.step()
        # First output is cycle 48; hold EOP at cycle 55.
        for _ in range(10):
            signal = core.step(m_ready=False)
            self.assertTrue(signal.output.eop)
            self.assertEqual(core.stats["cw_out"], 0)
        core.stats["cw_out"] = MAX64
        core.stats["symbols_corrected"] = MAX64
        core.step()
        self.assertEqual(core.stats["cw_out"], MAX64)
        self.assertEqual(core.stats["symbols_corrected"], MAX64)
        self.assertEqual(core.stats["cw_corrected"], 1)
        # clear during in-flight receive must preserve the transaction.
        core = StreamCore("encode")
        for i, beat in enumerate(pack_beats(bytes(239), 1)):
            core.step(beat, stats_clear=i == 3)
        self.assertEqual(core.stats["cw_in"], 1)
        for _ in range(15):
            core.step()
        core.step(stats_clear=True)  # output EOP at cycle 23; clear wins
        self.assertFalse(any(core.stats.values()))
        self.assertEqual(core.occupied, 0)

    def test_one_lane_and_budget(self):
        self.assertEqual(len(Duplex(1).tx), 1)
        budget = LinkBudget().calculate()
        self.assertAlmostEqual(budget["coded_gbps"], 853.5564853556485)
        self.assertAlmostEqual(budget["information_capacity_gbps"], 849.7777777777778)
        self.assertAlmostEqual(budget["baud_with_overhead_gbd"], 72.55230125523013)

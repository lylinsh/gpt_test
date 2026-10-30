import hashlib
import json
import random
import unittest

from rs_codec import gf256 as gf
from rs_codec.rs255239 import (
    EXPECTED_GENERATOR,
    GENERATOR,
    SEED,
    Status,
    berlekamp_massey,
    decode,
    encode,
    encode_long,
    inject_errors,
    random_errors,
    solve_error_values,
    syndromes,
)


class Mathematics(unittest.TestCase):
    def test_field_exhaustive(self):
        def multiply_bits(a, b):
            product = 0
            while b:
                if b & 1:
                    product ^= a
                a <<= 1
                if a & 256:
                    a ^= 0x11D
                b >>= 1
            return product

        for a in range(256):
            self.assertEqual(gf.gf_mul(a, 0), 0)
            self.assertEqual(gf.gf_mul(a, 1), a)
            for b in range(256):
                self.assertEqual(gf.gf_mul(a, b), multiply_bits(a, b), (a, b))
                self.assertEqual(gf.gf_mul(a, b), gf.gf_mul(b, a))
                c = (a + b) & 255
                self.assertEqual(gf.gf_mul(a, b ^ c), gf.gf_mul(a, b) ^ gf.gf_mul(a, c))
        for a in range(1, 256):
            self.assertEqual(gf.gf_mul(a, gf.gf_inv(a)), 1)
        self.assertEqual(gf.gf_inv(0), 0)
        with self.assertRaises(ZeroDivisionError):
            gf.gf_div(1, 0)
        self.assertEqual(len(set(gf.EXP[:255])), 255)
        self.assertEqual(gf.EXP[255], 1)

    def test_generator_vectors(self):
        self.assertEqual(GENERATOR, EXPECTED_GENERATOR)
        for j in range(16):
            self.assertEqual(gf.poly_eval_high(GENERATOR, gf.EXP[j]), 0)
        vectors = [
            (
                bytes(239),
                "00" * 16,
                "80bd5cb5a9ca35dcdea1d59b5f1778f4114f6215af38004a02a99a1d37383648",
            ),
            (
                bytes(range(239)),
                "3d4a1daccc4a4caa43488e7b4f6559c4",
                "030d0337e148bdd5727b7f52c9ff4d347bb1835950210de99ca414d9c3ee1105",
            ),
            (
                bytes(238) + b"\1",
                "3b0d68bd44d11e08a34129e56232243b",
                "96ac9f865e4cd68c74b491da8306c63cd62f3162616d353ed2fecc45e7f6669a",
            ),
            (
                b"\1" + bytes(238),
                "a90116b0fa8bd4b22148bc0c8cde891a",
                "385f462a3dd3a17df4b1b72fa5fa3ebeb3f9d30d7feb844f82e4baf1739f3fca",
            ),
            (
                b"\xff" * 239,
                "eb907407d6ef1d98386c111f5aa16e84",
                "3d05ccab5f1bf8763adde771e38508bf09456fa10845a05f2345923fc809465d",
            ),
        ]
        for data, parity, digest in vectors:
            cw = encode(data)
            self.assertEqual(cw, encode_long(data))
            self.assertEqual(cw[239:].hex(), parity)
            self.assertEqual(hashlib.sha256(cw).hexdigest(), digest)
            self.assertFalse(any(syndromes(cw)))
            print("VECTOR", parity, digest)

    def test_random_encoders_1000(self):
        rng = random.Random(SEED)
        for cw_id in range(1000):
            data = rng.randbytes(239)
            cw = encode(data)
            self.assertEqual(cw, encode_long(data), (SEED, cw_id, data.hex()))
            self.assertFalse(any(syndromes(cw)), (SEED, cw_id, data.hex()))
            self.assertEqual(
                syndromes(cw),
                tuple(gf.poly_eval_high(cw, gf.EXP[j]) for j in range(16)),
            )

    def check_errors(self, data, positions, values, cw_id):
        cw = encode(data)
        received = inject_errors(cw, positions, values)
        result = decode(received)
        replay = json.dumps(
            dict(
                seed=SEED,
                cw_id=cw_id,
                original=data.hex(),
                received=received.hex(),
                positions=positions,
                values=values,
            )
        )
        self.assertEqual(result.data, data, replay)
        self.assertEqual(result.codeword, cw, replay)
        self.assertEqual(
            result.status, Status.CORRECTED if positions else Status.CLEAN, replay
        )
        self.assertEqual(result.error_count, len(positions), replay)
        self.assertEqual(solve_error_values(received, positions), tuple(values), replay)
        self.assertEqual(
            dict(zip(result.positions, result.magnitudes)),
            dict(zip(positions, values)),
            replay,
        )

    def test_random_errors_0_to_8(self):
        rng = random.Random(SEED)
        for count in range(9):
            for trial in range(256):
                data = rng.randbytes(239)
                positions = rng.sample(range(255), count)
                values = [rng.randrange(1, 256) for _ in positions]
                self.check_errors(data, positions, values, count * 256 + trial)

    def test_boundaries_and_batches(self):
        data = bytes(range(239))
        for positions in (
            [0, 238, 239, 254],
            list(range(8)),
            list(range(224, 232)),
            list(range(239, 247)),
            [0, 31, 32, 63, 64, 238, 239, 254],
        ):
            self.check_errors(data, positions, [255] * len(positions), 0xFFFFFFFF)
        # Same syndrome pattern on different legal codewords.
        p, v = [0, 239, 254], [1, 128, 255]
        a = inject_errors(encode(bytes(239)), p, v)
        b = inject_errors(encode(data), p, v)
        self.assertEqual(syndromes(a), syndromes(b))
        self.assertEqual(decode(a).data, bytes(239))
        self.assertEqual(decode(b).data, data)

    def test_single_error_all_65025(self):
        cw = bytes(255)
        for p in range(255):
            for value in range(1, 256):
                received = inject_errors(cw, [p], [value])
                result = decode(received)
                self.assertEqual(
                    (
                        result.codeword,
                        result.status,
                        result.error_count,
                        result.positions,
                        result.magnitudes,
                    ),
                    (cw, Status.CORRECTED, 1, (p,), (value,)),
                    json.dumps(
                        dict(
                            seed=SEED,
                            cw_id=p * 255 + value,
                            original=bytes(239).hex(),
                            received=received.hex(),
                            positions=[p],
                            values=[value],
                        )
                    ),
                )

    def test_beyond_radius_and_miscorrection(self):
        rng = random.Random(SEED)
        outcomes = dict(clean=0, corrected=0, failed=0)
        for count in (9, 10, 16, 32, 100, 255):
            for trial in range(32):
                cw = encode(rng.randbytes(239))
                received, positions, values = random_errors(cw, count, rng)
                result = decode(received)
                if result.status == Status.DECODE_FAILURE:
                    outcomes["failed"] += 1
                    self.assertEqual(result.data, received[:239])
                    self.assertEqual(result.codeword, received)
                    self.assertEqual(result.error_count, 0)
                else:
                    outcomes[
                        "clean" if result.status == Status.CLEAN else "corrected"
                    ] += 1
                    self.assertFalse(any(syndromes(result.codeword)))
                    self.assertLessEqual(result.error_count, 8)
        self.assertGreater(outcomes["failed"], 0)
        # Deliberate >8-error undetected word and miscorrection: both are unavoidable.
        original = bytes(255)
        other = encode(b"\1" + bytes(238))
        distance = sum(a != b for a, b in zip(original, other))
        self.assertEqual(distance, 17)
        self.assertEqual(decode(other).status, Status.CLEAN)
        near_other = inject_errors(other, [0], [1])
        self.assertEqual(sum(a != b for a, b in zip(original, near_other)), 16)
        self.assertEqual(decode(near_other).codeword, other)
        self.assertEqual(decode(near_other).status, Status.CORRECTED)
        print(
            "BEYOND_RADIUS", json.dumps(outcomes), "constructed clean=1 miscorrected=1"
        )

    def test_invalid_inputs(self):
        for func, data in ((encode, bytes(238)), (decode, bytes(256))):
            with self.assertRaises(ValueError):
                func(data)
        for p, v in (([0, 0], [1, 2]), ([255], [1]), ([0], [0])):
            with self.assertRaises(ValueError):
                inject_errors(bytes(255), p, v)
        self.assertEqual(berlekamp_massey([0] * 16), [1])

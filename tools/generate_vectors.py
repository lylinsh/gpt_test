"""Run with python -m tools.generate_vectors from repository root."""

import hashlib
import json
from rs_codec.rs255239 import GENERATOR, encode, syndromes


def main():
    vectors = [
        ("zero", bytes(239)),
        ("ramp", bytes(range(239))),
        ("last_one", bytes(238) + b"\x01"),
        ("first_one", b"\x01" + bytes(238)),
        ("ff", b"\xff" * 239),
    ]
    print(
        json.dumps(
            {
                "generator": GENERATOR.hex(),
                "vectors": [
                    dict(
                        name=name,
                        data=data.hex(),
                        codeword=(cw := encode(data)).hex(),
                        parity=cw[239:].hex(),
                        sha256=hashlib.sha256(cw).hexdigest(),
                        syndromes=syndromes(cw),
                    )
                    for name, data in vectors
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Small command line interface: python -m rs_codec --help."""

import argparse
import json
import random
from .budget import LinkBudget
from .rs255239 import SEED, decode, encode, random_errors


def main():
    parser = argparse.ArgumentParser(
        description="Pure RS(255,239) optical FEC reference model"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("budget", help="800 Gbps DP-64QAM rate budget")
    for name in ("encode", "decode"):
        sub.add_parser(name).add_argument("hex_bytes")
    demo = sub.add_parser("demo", help="seeded byte-error correction experiment")
    demo.add_argument("--errors", type=int, default=8, choices=range(256))
    demo.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    try:
        if args.command == "budget":
            print(json.dumps(LinkBudget().calculate(), indent=2))
        elif args.command == "encode":
            print(encode(bytes.fromhex(args.hex_bytes)).hex())
        elif args.command == "decode":
            result = decode(bytes.fromhex(args.hex_bytes))
            print(
                json.dumps(
                    dict(
                        data=result.data.hex(),
                        codeword=result.codeword.hex(),
                        status=result.status.name,
                        error_count=result.error_count,
                        positions=result.positions,
                        magnitudes=result.magnitudes,
                        reason=result.reason,
                    ),
                    indent=2,
                )
            )
        else:
            rng = random.Random(args.seed)
            data = rng.randbytes(239)
            received, positions, values = random_errors(encode(data), args.errors, rng)
            result = decode(received)
            print(
                json.dumps(
                    dict(
                        seed=args.seed,
                        cw_id=0,
                        original=data.hex(),
                        received=received.hex(),
                        positions=positions,
                        values=values,
                        status=result.status.name,
                        error_count=result.error_count,
                        recovered=result.data == data,
                        reason=result.reason,
                    ),
                    indent=2,
                )
            )
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

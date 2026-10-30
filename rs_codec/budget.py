"""Digital rate budget. Not an optical BER or hardware timing prediction."""

from dataclasses import dataclass, asdict
import json


@dataclass(frozen=True)
class LinkBudget:
    net_gbps: float = 800.0
    overhead_multiplier: float = 1.02
    baud_gbd: float = 75.0
    clock_mhz: float = 500.0
    lanes: int = 8
    ii: int = 9

    def calculate(self):
        if (
            min(self.net_gbps, self.baud_gbd, self.clock_mhz) <= 0
            or self.overhead_multiplier < 1
            or self.lanes < 1
            or self.ii < 8
        ):
            raise ValueError("invalid link/scheduler parameters")
        coded = self.net_gbps * 255 / 239
        return dict(
            **asdict(self),
            code_rate=239 / 255,
            coded_gbps=coded,
            minimum_baud_gbd=coded / 12,
            baud_with_overhead_gbd=coded * self.overhead_multiplier / 12,
            line_capacity_gbps=self.baud_gbd * 12,
            information_capacity_gbps=self.lanes
            * 239
            * 8
            * self.clock_mhz
            / self.ii
            / 1000,
            coded_capacity_gbps=self.lanes * 255 * 8 * self.clock_mhz / self.ii / 1000,
        )


if __name__ == "__main__":
    print(json.dumps(LinkBudget().calculate(), indent=2))

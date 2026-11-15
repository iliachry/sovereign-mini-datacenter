"""Solar-aware compute & relay marketplace for the Sovereign Mini Datacenter network.

Dynamically computes task execution prices based on real-time solar irradiance,
battery State-of-Charge (SoC), and hardware compute utilization.
"""

from __future__ import annotations

import enum
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.log import get_logger

logger = get_logger("sovereign_dc.economy.market")


class ServiceType(enum.StrEnum):
    """Catalog of sovereign compute and infrastructure services."""

    LLM_INFERENCE = "llm_inference"  # Quantified in 1,000 tokens
    VECTOR_EMBEDDING = "vector_embedding"  # Quantified in 1,000 document chunks
    SPACE_DTN_RELAY = "space_dtn_relay"  # Quantified in Megabytes (MB)
    STORAGE_LEASE = "storage_lease"  # Quantified in Gigabyte-Days (GB-day)
    ENERGY_OFFLOAD = "energy_offload"  # Quantified in Kilowatt-hours (kWh)


# Base benchmark prices in SMDC compute credits
BASE_SERVICE_PRICING: dict[ServiceType, tuple[float, str]] = {
    ServiceType.LLM_INFERENCE: (0.05, "1k tokens"),
    ServiceType.VECTOR_EMBEDDING: (0.02, "1k chunks"),
    ServiceType.SPACE_DTN_RELAY: (0.10, "MB routed"),
    ServiceType.STORAGE_LEASE: (0.01, "GB-day"),
    ServiceType.ENERGY_OFFLOAD: (0.15, "kWh solar export"),
}


# Energy-state pricing rule (``ComputeMarket.get_dynamic_multiplier``). README.md and AGENTS.md
# quote these figures; tests/test_regress_round5_astra.py compares the documents with them.
#
# Surplus discount: battery SoC at or above SOLAR_SURPLUS_MIN_SOC_PERCENT *and* a measured
# solar harvest at or above SOLAR_SURPLUS_MIN_SOLAR_WATTS. (The sentinel's
# SOLAR_SURPLUS_COMPUTE operating mode is a different rule with its own, higher solar bar.)
SOLAR_SURPLUS_MIN_SOC_PERCENT = 75.0
SOLAR_SURPLUS_MIN_SOLAR_WATTS = 500.0
SOLAR_SURPLUS_MULTIPLIER = 0.50
# Nominal pricing at or above this SoC (when the surplus discount does not apply).
NOMINAL_MIN_SOC_PERCENT = 40.0
NOMINAL_MULTIPLIER = 1.00
# Moderate surge from this SoC up to NOMINAL_MIN_SOC_PERCENT; critical surge below it.
MODERATE_SURGE_MIN_SOC_PERCENT = 25.0
MODERATE_SURGE_MULTIPLIER = 1.50
CRITICAL_SURGE_MULTIPLIER = 3.00


@dataclass
class PriceQuote:
    """Dynamic pricing quotation for an autonomous service request."""

    service_type: str
    quantity: float
    unit_name: str
    base_unit_price: float
    energy_multiplier: float
    final_unit_price: float
    total_cost_credits: float
    energy_state: str
    quoted_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceOffer:
    """An active service offering advertised by a sovereign peer node."""

    offer_id: str
    node_id: str
    wallet_address: str
    service_type: str
    unit_price: float
    unit_name: str
    max_capacity: float
    active: bool = True
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if not self.offer_id:
            self.offer_id = f"offer_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ComputeMarket:
    """Autonomous marketplace matching compute demand with solar-rich nodes."""

    def __init__(self) -> None:
        self._offers: dict[str, ServiceOffer] = {}

    @staticmethod
    def get_dynamic_multiplier(battery_soc: float, solar_power_w: float | None) -> tuple[float, str]:
        """Calculates price discount/surge multiplier based on physical energy state.

        - Battery SoC >= 75% and solar >= 500 W: 50% surplus discount (0.5x)
        - Battery SoC >= 40% (otherwise): nominal pricing (1.0x)
        - Battery SoC 25% up to 40%: moderate surge (1.5x)
        - Battery SoC < 25%: heavy preservation surge (3.0x)

        The figures are the module constants ``SOLAR_SURPLUS_*``, ``NOMINAL_*``,
        ``MODERATE_SURGE_*`` and ``CRITICAL_SURGE_MULTIPLIER``; every bound is inclusive at
        its lower end.

        ``solar_power_w`` may be None (the node has no solar measurement): an unknown harvest
        never earns the surplus discount. A non-finite ``battery_soc`` raises ValueError
        rather than being priced as a critical or healthy battery.
        """
        if not isinstance(battery_soc, (int, float)) or not math.isfinite(battery_soc):
            raise ValueError("battery_soc must be a finite number")
        if solar_power_w is not None and not math.isfinite(solar_power_w):
            solar_power_w = None
        if (
            battery_soc >= SOLAR_SURPLUS_MIN_SOC_PERCENT
            and solar_power_w is not None
            and solar_power_w >= SOLAR_SURPLUS_MIN_SOLAR_WATTS
        ):
            return SOLAR_SURPLUS_MULTIPLIER, "SOLAR_SURPLUS_DISCOUNT_50%"
        elif battery_soc >= NOMINAL_MIN_SOC_PERCENT:
            return NOMINAL_MULTIPLIER, "NOMINAL_ENERGY_STATE"
        elif battery_soc >= MODERATE_SURGE_MIN_SOC_PERCENT:
            return MODERATE_SURGE_MULTIPLIER, "MODERATE_POWER_SURGE_150%"
        else:
            return CRITICAL_SURGE_MULTIPLIER, "CRITICAL_BATTERY_SURGE_300%"

    def calculate_quote(
        self,
        service_type: ServiceType,
        quantity: float,
        battery_soc: float = 85.0,
        solar_power_w: float | None = 850.0,
    ) -> PriceQuote:
        """Generates an energy-aware price quote for a service request."""
        if quantity <= 0:
            raise ValueError("Service quantity must be positive")

        base_rate, unit_name = BASE_SERVICE_PRICING.get(service_type, (0.05, "unit"))
        multiplier, energy_state = self.get_dynamic_multiplier(battery_soc, solar_power_w)

        final_rate = round(base_rate * multiplier, 6)
        total_cost = round(final_rate * quantity, 4)

        quote = PriceQuote(
            service_type=service_type.value,
            quantity=quantity,
            unit_name=unit_name,
            base_unit_price=base_rate,
            energy_multiplier=multiplier,
            final_unit_price=final_rate,
            total_cost_credits=total_cost,
            energy_state=energy_state,
            quoted_at=datetime.now(UTC).isoformat(),
        )

        logger.debug(
            "Generated quote for %s x %.1f: %.4f credits (%s)", service_type.value, quantity, total_cost, energy_state
        )
        return quote

    def register_offer(self, offer: ServiceOffer) -> None:
        """Publishes a compute or relay service offer to the local market registry."""
        self._offers[offer.offer_id] = offer
        logger.info(
            "Registered %s offer from %s: %.4f credits / %s (ID: %s)",
            offer.service_type,
            offer.node_id,
            offer.unit_price,
            offer.unit_name,
            offer.offer_id,
        )

    def list_offers(self, service_type: ServiceType | None = None, active_only: bool = True) -> list[ServiceOffer]:
        """Lists active service offers filtered by service type."""
        offers = list(self._offers.values())
        if active_only:
            offers = [o for o in offers if o.active]
        if service_type:
            offers = [o for o in offers if o.service_type == service_type.value]
        return sorted(offers, key=lambda x: x.unit_price)

    def cancel_offer(self, offer_id: str) -> bool:
        """Cancels or deactivates a service offer."""
        if offer_id in self._offers:
            self._offers[offer_id].active = False
            return True
        return False

"""Solar-aware compute & relay marketplace for the Sovereign Mini Datacenter network.

Dynamically computes task execution prices based on real-time solar irradiance,
battery State-of-Charge (SoC), and hardware compute utilization.
"""

from __future__ import annotations

import enum
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
    def get_dynamic_multiplier(battery_soc: float, solar_power_w: float) -> tuple[float, str]:
        """Calculates price discount/surge multiplier based on physical energy state.

        - Battery SoC > 75% & Solar > 500W: 50% Green Discount (excess power monetization)
        - Battery SoC 40-75%: Nominal pricing (1.0x)
        - Battery SoC 25-40%: Moderate surge (1.5x)
        - Battery SoC < 25%: Heavy preservation surge (3.0x)
        """
        if battery_soc >= 75.0 and solar_power_w >= 500.0:
            return 0.50, "SOLAR_SURPLUS_DISCOUNT_50%"
        elif battery_soc >= 40.0:
            return 1.00, "NOMINAL_ENERGY_STATE"
        elif battery_soc >= 25.0:
            return 1.50, "MODERATE_POWER_SURGE_150%"
        else:
            return 3.00, "CRITICAL_BATTERY_SURGE_300%"

    def calculate_quote(
        self,
        service_type: ServiceType,
        quantity: float,
        battery_soc: float = 85.0,
        solar_power_w: float = 850.0,
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

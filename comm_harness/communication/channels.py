"""Pluggable wireless/network channel impairment models for multi-agent communication.

Separates *what* agents transmit (:class:`Envelope`) from *how* the physical or
simulated medium delivers, delays, throttles, or drops packets.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
import random
import threading
from typing import Callable, Optional, Sequence, Tuple

from comm_harness.communication.envelope import Envelope


PositionResolver = Callable[[str], Optional[Sequence[float]]]


@dataclass(frozen=True)
class ChannelDecision:
    """Outcome evaluated per ``(envelope, recipient)`` link."""

    deliver: bool
    delay_s: float = 0.0
    drop_reason: Optional[str] = None
    distance_m: Optional[float] = None


class ChannelModel(ABC):
    """Abstract communication medium policy."""

    name: str = "base"

    @abstractmethod
    def evaluate(
        self,
        envelope: Envelope,
        recipient: str,
        *,
        now: float,
    ) -> ChannelDecision:
        """Decide whether and when ``envelope`` reaches ``recipient``."""
        raise NotImplementedError


class IdealChannel(ChannelModel):
    """Lossless, zero-latency channel for upper-bound collaborative experiments."""

    name = "ideal"

    def evaluate(
        self,
        envelope: Envelope,
        recipient: str,
        *,
        now: float,
    ) -> ChannelDecision:
        if now - envelope.created_at > envelope.ttl_s:
            return ChannelDecision(deliver=False, drop_reason="ttl_expired")
        return ChannelDecision(deliver=True, delay_s=0.0)


class BlackoutChannel(ChannelModel):
    """Drops all traffic (`No-Comm` ablation baseline)."""

    name = "blackout"

    def evaluate(
        self,
        envelope: Envelope,
        recipient: str,
        *,
        now: float,
    ) -> ChannelDecision:
        return ChannelDecision(deliver=False, drop_reason="channel_blackout")


class ImpairedChannel(ChannelModel):
    """Configurable channel modeling MTU limits, token-bucket bandwidth, packet loss,
    latency/jitter, and optional Euclidean communication range cutoff.
    """

    name = "impaired"

    def __init__(
        self,
        *,
        drop_prob: float = 0.0,
        bandwidth_bytes_per_sec: Optional[float] = None,
        burst_bytes: Optional[float] = None,
        mtu_bytes: Optional[int] = None,
        base_latency_s: float = 0.0,
        jitter_s: float = 0.0,
        max_range_m: Optional[float] = None,
        position_resolver: Optional[PositionResolver] = None,
        seed: int = 42,
    ) -> None:
        if not (0.0 <= float(drop_prob) <= 1.0):
            raise ValueError("drop_prob must be in [0.0, 1.0]")
        self.drop_prob = float(drop_prob)
        self.bandwidth_bps = (
            float(bandwidth_bytes_per_sec) if bandwidth_bytes_per_sec is not None else None
        )
        self.mtu_bytes = int(mtu_bytes) if mtu_bytes is not None else None
        self.base_latency_s = max(0.0, float(base_latency_s))
        self.jitter_s = max(0.0, float(jitter_s))
        self.max_range_m = float(max_range_m) if max_range_m is not None else None
        self.position_resolver = position_resolver
        self._rng = random.Random(seed)
        self._lock = threading.Lock()

        if self.bandwidth_bps is not None:
            if self.bandwidth_bps <= 0:
                raise ValueError("bandwidth_bytes_per_sec must be positive")
            self._capacity = float(burst_bytes) if burst_bytes is not None else max(
                self.bandwidth_bps, 1024.0
            )
            self._tokens = self._capacity
            self._last_refill: Optional[float] = None
        else:
            self._capacity = 0.0
            self._tokens = 0.0
            self._last_refill = None

    def _consume_bandwidth(self, nbytes: int, now: float) -> bool:
        if self.bandwidth_bps is None:
            return True
        if self._last_refill is None:
            self._last_refill = now
        else:
            elapsed = max(0.0, now - self._last_refill)
            self._tokens = min(self._capacity, self._tokens + elapsed * self.bandwidth_bps)
            self._last_refill = now
        if self._tokens < nbytes:
            return False
        self._tokens -= nbytes
        return True

    def _compute_distance(self, sender: str, recipient: str) -> Optional[float]:
        if self.position_resolver is None:
            return None
        p_a = self.position_resolver(sender)
        p_b = self.position_resolver(recipient)
        if p_a is None or p_b is None:
            return None
        dims = min(len(p_a), len(p_b))
        if dims == 0:
            return None
        return math.sqrt(sum((float(p_a[i]) - float(p_b[i])) ** 2 for i in range(dims)))

    def evaluate(
        self,
        envelope: Envelope,
        recipient: str,
        *,
        now: float,
    ) -> ChannelDecision:
        if now - envelope.created_at > envelope.ttl_s:
            return ChannelDecision(deliver=False, drop_reason="ttl_expired")

        if self.mtu_bytes is not None and envelope.serialized_bytes > self.mtu_bytes:
            return ChannelDecision(deliver=False, drop_reason="mtu_exceeded")

        dist = self._compute_distance(envelope.sender, recipient)
        if self.max_range_m is not None and dist is not None and dist > self.max_range_m:
            return ChannelDecision(
                deliver=False,
                drop_reason="out_of_range",
                distance_m=round(dist, 3),
            )

        with self._lock:
            if not self._consume_bandwidth(envelope.serialized_bytes, now):
                return ChannelDecision(
                    deliver=False,
                    drop_reason="bandwidth_exceeded",
                    distance_m=round(dist, 3) if dist is not None else None,
                )
            if self.drop_prob > 0.0 and self._rng.random() < self.drop_prob:
                return ChannelDecision(
                    deliver=False,
                    drop_reason="packet_loss",
                    distance_m=round(dist, 3) if dist is not None else None,
                )
            jitter = self._rng.uniform(0.0, self.jitter_s) if self.jitter_s > 0.0 else 0.0

        return ChannelDecision(
            deliver=True,
            delay_s=round(self.base_latency_s + jitter, 4),
            distance_m=round(dist, 3) if dist is not None else None,
        )

"""Wire-level message envelope contract for communication-native multi-agent harnesses.

Every cross-agent packet is serialized to UTF-8 JSON bytes on send and decoded
fresh on receive so agents can never share mutable Python references.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class Envelope:
    """Immutable serialized packet transmitted between agent endpoints."""

    message_id: str
    episode_id: str
    sender: str
    recipients: Tuple[str, ...]
    topic: str
    payload: Dict[str, Any]
    sequence: int
    created_at: float = field(default_factory=time.time)
    ttl_s: float = 60.0
    priority: int = 0
    schema_version: int = 1
    serialized_bytes: int = 0

    def to_wire_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "message_id": str(self.message_id),
            "episode_id": str(self.episode_id),
            "sender": str(self.sender),
            "recipients": list(self.recipients),
            "topic": str(self.topic),
            "payload": self.payload,
            "sequence": int(self.sequence),
            "created_at": float(self.created_at),
            "ttl_s": float(self.ttl_s),
            "priority": int(self.priority),
        }

    def to_wire_bytes(self) -> bytes:
        return json.dumps(
            self.to_wire_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def create(
        cls,
        *,
        message_id: str,
        episode_id: str,
        sender: str,
        recipients: Sequence[str],
        topic: str,
        payload: Dict[str, Any],
        sequence: int,
        ttl_s: float = 60.0,
        priority: int = 0,
        created_at: float | None = None,
    ) -> "Envelope":
        if not sender or not isinstance(sender, str):
            raise ValueError("sender must be a non-empty string")
        clean_recipients = tuple(sorted({str(r).strip() for r in recipients if str(r).strip()}))
        if not clean_recipients:
            raise ValueError("recipients must contain at least one valid node_id")
        if not topic or not isinstance(topic, str):
            raise ValueError("topic must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON-serializable dict")

        draft = cls(
            message_id=message_id,
            episode_id=episode_id,
            sender=sender,
            recipients=clean_recipients,
            topic=topic,
            payload=payload,
            sequence=int(sequence),
            created_at=float(time.time() if created_at is None else created_at),
            ttl_s=float(ttl_s),
            priority=int(priority),
        )
        wire = draft.to_wire_bytes()
        return cls.from_wire_bytes(wire)

    @classmethod
    def from_wire_bytes(cls, wire: bytes) -> "Envelope":
        data = json.loads(wire.decode("utf-8"))
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            message_id=str(data["message_id"]),
            episode_id=str(data.get("episode_id", "default")),
            sender=str(data["sender"]),
            recipients=tuple(str(r) for r in data["recipients"]),
            topic=str(data["topic"]),
            payload=dict(data.get("payload") or {}),
            sequence=int(data.get("sequence", 0)),
            created_at=float(data.get("created_at", 0.0)),
            ttl_s=float(data.get("ttl_s", 60.0)),
            priority=int(data.get("priority", 0)),
            serialized_bytes=len(wire),
        )

    def to_inbox_item(self, recipient: str, delivered_at: float | None = None) -> Dict[str, Any]:
        """Return a detached dict suitable for an agent's tool result."""
        wire = self.to_wire_bytes()
        detached: Dict[str, Any] = json.loads(wire.decode("utf-8"))
        detached["recipient"] = str(recipient)
        detached["serialized_bytes"] = len(wire)
        detached["delivered_at"] = float(time.time() if delivered_at is None else delivered_at)
        return detached

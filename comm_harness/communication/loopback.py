"""Intra-machine multi-agent communication backends (in-memory & localhost IPC).

Provides two interchangeable implementations of the :class:`CommEndpoint` contract:
1. :class:`InMemoryCommBus` / :class:`InMemoryNodeEndpoint`:
   Zero-setup thread-safe communication inside one Python process.
2. :class:`LocalhostCommServer` / :class:`LocalhostCommClient`:
   Cross-process communication on the same computer (`http://127.0.0.1:<port>`)
   so independent robot processes (or even different Python virtualenvs) talk
   through a shared channel-aware broker without sharing memory.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from comm_harness.communication.channels import ChannelModel, IdealChannel
from comm_harness.communication.envelope import Envelope
from comm_harness.rpc import RpcClient, RpcServer


class CommEndpoint(ABC):
    """Uniform per-robot communication handle attached to a robot's ``Toolkit``."""

    node_id: str

    @abstractmethod
    def send(
        self,
        recipients: Sequence[str],
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        """Transmit a unicast/multicast packet to ``recipients``."""
        raise NotImplementedError

    @abstractmethod
    def broadcast(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        """Broadcast a packet to all registered peers except ``self.node_id``."""
        raise NotImplementedError

    @abstractmethod
    def poll(self, *, max_messages: int = 20, topic: str = "") -> List[Dict[str, Any]]:
        """Drain arrived packets from this robot's inbox."""
        raise NotImplementedError

    @abstractmethod
    def peers(self) -> List[str]:
        """Return registered teammate node IDs (excluding ``self.node_id``)."""
        raise NotImplementedError

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """Return local inbox depth, peers, and communication link statistics."""
        raise NotImplementedError


class InMemoryCommBus:
    """Channel-aware intra-machine message broker supporting delayed delivery,
    packet-loss/MTU/bandwidth/range impairments, and structured event tracing.
    """

    def __init__(
        self,
        episode_id: str = "local-session",
        *,
        channel: Optional[ChannelModel] = None,
        emit: Optional[Callable[..., None]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.episode_id = str(episode_id)
        self.channel: ChannelModel = channel or IdealChannel()
        self.emit = emit or (lambda *a, **k: None)
        self.clock = clock

        self._lock = threading.RLock()
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._positions: Dict[str, Tuple[float, ...]] = {}
        self._sequence: Dict[str, int] = defaultdict(int)
        # Per-recipient queue of (deliver_at_timestamp, Envelope)
        self._queues: Dict[str, List[Tuple[float, Envelope]]] = defaultdict(list)
        self._delivered_ids: Dict[str, set] = defaultdict(set)

        self._stats: Dict[str, Any] = {
            "channel": self.channel.name,
            "messages_sent": 0,
            "link_attempts": 0,
            "messages_delivered": 0,
            "messages_dropped": 0,
            "source_bytes": 0,
            "delivered_bytes": 0,
        }
        self._drop_reasons: Counter = Counter()
        self._per_node_tx: Counter = Counter()
        self._per_node_rx: Counter = Counter()

        # Automatically wire position resolver if the channel supports range checks
        if getattr(self.channel, "position_resolver", None) is None and hasattr(
            self.channel, "position_resolver"
        ):
            self.channel.position_resolver = self.get_position

    def register_node(
        self,
        node_id: str,
        *,
        position: Optional[Sequence[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "InMemoryNodeEndpoint":
        nid = str(node_id).strip()
        if not nid:
            raise ValueError("node_id must be non-empty")
        with self._lock:
            self._nodes[nid] = dict(metadata or {})
            if position is not None:
                self._positions[nid] = tuple(float(x) for x in position)
        self.emit("comm_node_registered", node_id=nid, position=self._positions.get(nid))
        return InMemoryNodeEndpoint(self, nid)

    def update_position(self, node_id: str, position: Sequence[float]) -> Dict[str, Any]:
        nid = str(node_id).strip()
        coords = tuple(float(x) for x in position)
        with self._lock:
            self._nodes.setdefault(nid, {})
            self._positions[nid] = coords
        return {"node_id": nid, "position": list(coords)}

    def get_position(self, node_id: str) -> Optional[Tuple[float, ...]]:
        with self._lock:
            return self._positions.get(str(node_id))

    def list_nodes(self) -> List[str]:
        with self._lock:
            return sorted(self._nodes.keys())

    def send(
        self,
        sender: str,
        recipients: Sequence[str],
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        now = float(self.clock())
        clean_sender = str(sender).strip()
        with self._lock:
            self._nodes.setdefault(clean_sender, {})
            self._sequence[clean_sender] += 1
            seq = self._sequence[clean_sender]
            msg_id = f"{self.episode_id}/{clean_sender}/{seq}"

            env = Envelope.create(
                message_id=msg_id,
                episode_id=self.episode_id,
                sender=clean_sender,
                recipients=recipients,
                topic=topic,
                payload=payload,
                sequence=seq,
                ttl_s=ttl_s,
                priority=priority,
                created_at=now,
            )

            self._stats["messages_sent"] += 1
            self._stats["source_bytes"] += env.serialized_bytes
            self._per_node_tx[clean_sender] += 1

            enqueued_for: List[str] = []
            dropped_for: Dict[str, str] = {}

            for rcpt in env.recipients:
                self._nodes.setdefault(rcpt, {})
                self._stats["link_attempts"] += 1
                decision = self.channel.evaluate(env, rcpt, now=now)
                if decision.deliver:
                    deliver_at = now + max(0.0, float(decision.delay_s))
                    self._queues[rcpt].append((deliver_at, env))
                    enqueued_for.append(rcpt)
                else:
                    reason = decision.drop_reason or "dropped"
                    self._stats["messages_dropped"] += 1
                    self._drop_reasons[reason] += 1
                    dropped_for[rcpt] = reason
                    self.emit(
                        "comm_drop",
                        message_id=env.message_id,
                        sender=clean_sender,
                        recipient=rcpt,
                        topic=env.topic,
                        bytes=env.serialized_bytes,
                        drop_reason=reason,
                        distance_m=decision.distance_m,
                    )

            self.emit(
                "comm_tx",
                message_id=env.message_id,
                sender=clean_sender,
                recipients=list(env.recipients),
                topic=env.topic,
                bytes=env.serialized_bytes,
                enqueued_count=len(enqueued_for),
                dropped_count=len(dropped_for),
            )

            return {
                "message_id": env.message_id,
                "sender": clean_sender,
                "recipients": list(env.recipients),
                "topic": env.topic,
                "serialized_bytes": env.serialized_bytes,
                "accepted_links": len(enqueued_for),
                "dropped_links": len(dropped_for),
            }

    def broadcast(
        self,
        sender: str,
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        peers = [n for n in self.list_nodes() if n != str(sender).strip()]
        if not peers:
            return {
                "message_id": None,
                "sender": str(sender),
                "recipients": [],
                "topic": str(topic),
                "serialized_bytes": 0,
                "accepted_links": 0,
                "dropped_links": 0,
                "note": "no_peers_registered",
            }
        return self.send(
            sender=sender,
            recipients=peers,
            topic=topic,
            payload=payload,
            ttl_s=ttl_s,
            priority=priority,
        )

    def poll(
        self,
        node_id: str,
        *,
        max_messages: int = 20,
        topic: str = "",
    ) -> List[Dict[str, Any]]:
        now = float(self.clock())
        nid = str(node_id).strip()
        cap = max(1, int(max_messages))
        filter_topic = str(topic).strip()

        delivered: List[Dict[str, Any]] = []
        with self._lock:
            queue = self._queues.get(nid, [])
            if not queue:
                return []

            remaining: List[Tuple[float, Envelope]] = []
            # Sort ready items by priority (desc), then arrival time (asc)
            queue.sort(key=lambda item: (-item[1].priority, item[0], item[1].sequence))

            for deliver_at, env in queue:
                if len(delivered) >= cap:
                    remaining.append((deliver_at, env))
                    continue
                if deliver_at > now:
                    remaining.append((deliver_at, env))
                    continue
                if now - env.created_at > env.ttl_s:
                    self._stats["messages_dropped"] += 1
                    self._drop_reasons["ttl_expired_in_queue"] += 1
                    continue
                if filter_topic and env.topic != filter_topic:
                    remaining.append((deliver_at, env))
                    continue
                if env.message_id in self._delivered_ids[nid]:
                    continue

                self._delivered_ids[nid].add(env.message_id)
                item = env.to_inbox_item(nid, delivered_at=now)
                delivered.append(item)
                self._stats["messages_delivered"] += 1
                self._stats["delivered_bytes"] += env.serialized_bytes
                self._per_node_rx[nid] += 1
                self.emit(
                    "comm_rx",
                    message_id=env.message_id,
                    sender=env.sender,
                    recipient=nid,
                    topic=env.topic,
                    bytes=env.serialized_bytes,
                )

            self._queues[nid] = remaining
        return delivered

    def summary(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        now = float(self.clock())
        with self._lock:
            out: Dict[str, Any] = {
                **self._stats,
                "registered_nodes": sorted(self._nodes.keys()),
                "drop_reasons": dict(self._drop_reasons),
            }
            if node_id is not None:
                nid = str(node_id).strip()
                ready_count = sum(
                    1
                    for deliver_at, env in self._queues.get(nid, ())
                    if deliver_at <= now and (now - env.created_at <= env.ttl_s)
                )
                out["node_id"] = nid
                out["peers"] = [n for n in sorted(self._nodes.keys()) if n != nid]
                out["inbox_ready"] = ready_count
                out["tx_count"] = int(self._per_node_tx[nid])
                out["rx_count"] = int(self._per_node_rx[nid])
            return out


class InMemoryNodeEndpoint(CommEndpoint):
    """Per-robot handle backed by :class:`InMemoryCommBus`."""

    def __init__(self, bus: InMemoryCommBus, node_id: str) -> None:
        self._bus = bus
        self.node_id = str(node_id)

    def send(
        self,
        recipients: Sequence[str],
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        return self._bus.send(
            self.node_id,
            recipients,
            topic,
            payload,
            ttl_s=ttl_s,
            priority=priority,
        )

    def broadcast(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        return self._bus.broadcast(
            self.node_id,
            topic,
            payload,
            ttl_s=ttl_s,
            priority=priority,
        )

    def poll(self, *, max_messages: int = 20, topic: str = "") -> List[Dict[str, Any]]:
        return self._bus.poll(self.node_id, max_messages=max_messages, topic=topic)

    def peers(self) -> List[str]:
        return [n for n in self._bus.list_nodes() if n != self.node_id]

    def status(self) -> Dict[str, Any]:
        return self._bus.summary(node_id=self.node_id)


class LocalhostCommServer:
    """Standalone localhost (`127.0.0.1`) HTTP communication broker wrapping :class:`InMemoryCommBus`.

    Allows independent OS processes on the same computer to communicate over
    a realistic channel model (`IdealChannel`, `ImpairedChannel`, `BlackoutChannel`).
    """

    def __init__(
        self,
        bus: Optional[InMemoryCommBus] = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.bus = bus or InMemoryCommBus()
        self.rpc = RpcServer(host=host, port=port, name="comm_broker")
        self.rpc.register("register_node", self._rpc_register_node)
        self.rpc.register("update_position", self.bus.update_position)
        self.rpc.register("send", self.bus.send)
        self.rpc.register("broadcast", self.bus.broadcast)
        self.rpc.register("poll", self.bus.poll)
        self.rpc.register("list_nodes", self.bus.list_nodes)
        self.rpc.register("summary", self.bus.summary)

    def _rpc_register_node(
        self,
        node_id: str,
        position: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.bus.register_node(node_id, position=position, metadata=metadata)
        return self.bus.summary(node_id=node_id)

    @property
    def endpoint(self) -> str:
        return self.rpc.endpoint

    def start_background(self) -> str:
        return self.rpc.start_background()

    def serve_forever(self) -> None:
        self.rpc.serve_forever()

    def close(self) -> None:
        self.rpc.close()


class LocalhostCommClient(CommEndpoint):
    """Per-robot client connecting to a :class:`LocalhostCommServer` over `127.0.0.1`."""

    def __init__(
        self,
        endpoint: str,
        node_id: str,
        *,
        position: Optional[Sequence[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.node_id = str(node_id).strip()
        self._rpc = RpcClient(endpoint, timeout_s=timeout_s)
        self._rpc.call(
            "register_node",
            node_id=self.node_id,
            position=list(position) if position is not None else None,
            metadata=metadata,
        )

    def update_position(self, position: Sequence[float]) -> Dict[str, Any]:
        return self._rpc.call(
            "update_position",
            node_id=self.node_id,
            position=[float(x) for x in position],
        )

    def send(
        self,
        recipients: Sequence[str],
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        return self._rpc.call(
            "send",
            sender=self.node_id,
            recipients=list(recipients),
            topic=str(topic),
            payload=dict(payload),
            ttl_s=float(ttl_s),
            priority=int(priority),
        )

    def broadcast(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> Dict[str, Any]:
        return self._rpc.call(
            "broadcast",
            sender=self.node_id,
            topic=str(topic),
            payload=dict(payload),
            ttl_s=float(ttl_s),
            priority=int(priority),
        )

    def poll(self, *, max_messages: int = 20, topic: str = "") -> List[Dict[str, Any]]:
        return self._rpc.call(
            "poll",
            node_id=self.node_id,
            max_messages=int(max_messages),
            topic=str(topic),
        )

    def peers(self) -> List[str]:
        nodes = self._rpc.call("list_nodes")
        return [n for n in nodes if n != self.node_id]

    def status(self) -> Dict[str, Any]:
        return self._rpc.call("summary", node_id=self.node_id)

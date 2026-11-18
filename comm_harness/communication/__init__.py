"""Serialized node messages and channel-aware multi-agent communication primitives.

Even ideal delivery never shares mutable node state: all envelopes are encoded
to UTF-8 JSON wire bytes on send and decoded fresh on receive.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import json
import threading

from comm_harness.communication.channels import (
    BlackoutChannel,
    ChannelDecision,
    ChannelModel,
    IdealChannel,
    ImpairedChannel,
)
from comm_harness.communication.envelope import Envelope
from comm_harness.communication.loopback import (
    CommEndpoint,
    InMemoryCommBus,
    InMemoryNodeEndpoint,
    LocalhostCommClient,
    LocalhostCommServer,
)
from comm_harness.communication.tools import CommToolProvider, build_comm_tools


class IdealTransport:
    def __init__(self, episode_id, emit=lambda *a, **k: None):
        self.episode_id = episode_id
        self.emit = emit
        self.pending = []
        self.delivered = defaultdict(set)
        self.sequence = defaultdict(int)
        self.lock = threading.Lock()
        self.stats = {
            "messages_sent": 0,
            "messages_delivered": 0,
            "source_bytes": 0,
            "link_bytes": 0,
            "expired": 0,
        }

    def send(self, sender, recipients, kind, payload, tick, ttl_ticks=1000):
        if not recipients or not all(isinstance(r, str) for r in recipients):
            raise ValueError("recipients must be nonempty node names")
        with self.lock:
            self.sequence[sender] += 1
            msg = {
                "schema_version": 1,
                "episode_id": self.episode_id,
                "message_id": "%s/%s/%d" % (self.episode_id, sender, self.sequence[sender]),
                "sender": sender,
                "recipients": sorted(set(recipients)),
                "sequence": self.sequence[sender],
                "kind": kind,
                "payload": payload,
                "created_tick": int(tick),
                "ttl_ticks": int(ttl_ticks),
            }
            # Decode after serialization to prevent references into sender memory.
            wire = json.dumps(msg, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
            detached = json.loads(wire)
            detached["serialized_bytes"] = len(wire)
            self.pending.append(detached)
            self.stats["messages_sent"] += 1
            self.stats["source_bytes"] += len(wire)
            self.stats["link_bytes"] += len(wire) * len(msg["recipients"])
            self.emit("message_sent", **detached)
            return msg["message_id"]

    def deliver(self, tick):
        with self.lock:
            pending, self.pending = self.pending, []
        inboxes = defaultdict(list)
        for msg in pending:
            if tick - msg["created_tick"] > msg["ttl_ticks"]:
                self.stats["expired"] += 1
                self.emit(
                    "message_expired",
                    message_id=msg["message_id"],
                    sender=msg["sender"],
                    recipients=msg["recipients"],
                    age_ticks=tick - msg["created_tick"],
                    expired_tick=tick,
                )
                continue
            for recipient in msg["recipients"]:
                if msg["message_id"] in self.delivered[recipient]:
                    self.emit(
                        "message_duplicate",
                        message_id=msg["message_id"],
                        recipient=recipient,
                        tick=tick,
                    )
                    continue
                self.delivered[recipient].add(msg["message_id"])
                envelope = copy.deepcopy(msg)
                envelope["delivered_tick"] = int(tick)
                envelope["recipient"] = recipient
                inboxes[recipient].append(envelope)
                self.stats["messages_delivered"] += 1
                self.emit(
                    "message_delivered",
                    message_id=msg["message_id"],
                    recipient=recipient,
                    sender=msg["sender"],
                    sequence=msg["sequence"],
                    delivered_tick=tick,
                    age_ticks=tick - msg["created_tick"],
                )
        return dict(inboxes)


class TaskBoard:
    """Deterministic coordinator. Its private dictionary is never given to agents."""

    def __init__(self, goals, nodes, transport):
        self.nodes, self.transport = list(nodes), transport
        self.goals = {
            g["goal_id"]: {
                "goal_id": g["goal_id"],
                "owner": nodes[i % len(nodes)],
                "version": 1,
                "state": "ASSIGNED",
            }
            for i, g in enumerate(goals)
        }
        self.processed = set()

    def broadcast(self, tick):
        self.transport.send(
            "coordinator",
            self.nodes,
            "task_snapshot",
            {"goals": list(self.goals.values())},
            tick,
        )

    def ingest(self, messages, tick):
        changed = False
        for message in messages:
            if message["message_id"] in self.processed:
                continue
            self.processed.add(message["message_id"])
            payload, sender = message["payload"], message["sender"]
            gid = payload.get("goal_id")
            if gid not in self.goals or sender not in self.nodes:
                continue
            state = self.goals[gid]
            if message["kind"] == "goal_claim" and state["state"] != "CLAIMED_FOUND":
                state.update(state="CLAIMED_FOUND", owner=sender, version=state["version"] + 1)
                changed = True
            elif message["kind"] == "assignment_request":
                accepted = (
                    payload.get("expected_version") == state["version"]
                    and state["state"] != "CLAIMED_FOUND"
                )
                operation = payload.get("operation")
                if operation == "release":
                    accepted = accepted and state["owner"] == sender
                elif operation == "claim":
                    accepted = accepted and state["owner"] in (None, sender)
                else:
                    accepted = False
                if accepted:
                    state.update(
                        owner=None if operation == "release" else sender,
                        state="PENDING" if operation == "release" else "ASSIGNED",
                        version=state["version"] + 1,
                    )
                    changed = True
                self.transport.send(
                    "coordinator",
                    [sender],
                    "assignment_ack",
                    {"accepted": accepted, "goal": dict(state)},
                    tick,
                )
        if changed:
            self.broadcast(tick)

    def complete(self):
        return bool(self.goals) and all(
            g["state"] == "CLAIMED_FOUND" for g in self.goals.values()
        )


__all__ = [
    "BlackoutChannel",
    "ChannelDecision",
    "ChannelModel",
    "CommEndpoint",
    "CommToolProvider",
    "Envelope",
    "IdealChannel",
    "IdealTransport",
    "ImpairedChannel",
    "InMemoryCommBus",
    "InMemoryNodeEndpoint",
    "LocalhostCommClient",
    "LocalhostCommServer",
    "TaskBoard",
    "build_comm_tools",
]

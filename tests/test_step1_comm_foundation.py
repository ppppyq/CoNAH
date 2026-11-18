"""Step-1 Foundation Tests: Wire Envelope, Pluggable Channels, Localhost IPC (`127.0.0.1`),
CommTools Receipt Auditing, and Multi-Process Cooperative Search.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from comm_harness.communication import (
    BlackoutChannel,
    Envelope,
    IdealChannel,
    ImpairedChannel,
    InMemoryCommBus,
    LocalhostCommClient,
    LocalhostCommServer,
    build_comm_tools,
)
from comm_harness.rpc import RpcClient, RpcError, RpcServer
from comm_harness.tools import HarnessRuntime, Toolkit
from tasks.coop_grid_search import run_coop_grid_demo


class Step1CommFoundationTests(unittest.TestCase):
    def test_envelope_wire_detachment_and_byte_metering(self):
        mutable = {"coords": [1, 2], "nested": {"tag": "vase"}}
        env = Envelope.create(
            message_id="ep/r0/1",
            episode_id="ep",
            sender="robot_0",
            recipients=["robot_1", "robot_1", "robot_2"],
            topic="sighting",
            payload=mutable,
            sequence=1,
            created_at=100.0,
        )
        # Mutating caller's dict after create must NOT mutate the envelope or inbox item
        mutable["coords"].append(999)
        mutable["nested"]["tag"] = "corrupted"

        self.assertEqual(env.recipients, ("robot_1", "robot_2"))
        self.assertGreater(env.serialized_bytes, 40)
        inbox_item = env.to_inbox_item("robot_1", delivered_at=100.1)
        self.assertEqual(inbox_item["payload"]["coords"], [1, 2])
        self.assertEqual(inbox_item["payload"]["nested"]["tag"], "vase")

    def test_impaired_channel_mtu_bandwidth_latency_and_range(self):
        fake_now = [1000.0]
        channel = ImpairedChannel(
            mtu_bytes=400,
            bandwidth_bytes_per_sec=250.0,
            burst_bytes=350.0,
            base_latency_s=0.5,
            max_range_m=5.0,
            seed=123,
        )
        bus = InMemoryCommBus("ep_imp", channel=channel, clock=lambda: fake_now[0])
        r0 = bus.register_node("robot_0", position=(0.0, 0.0))
        r1 = bus.register_node("robot_1", position=(3.0, 4.0))  # distance = 5.0m (in range)
        r2 = bus.register_node("robot_2", position=(10.0, 0.0))  # distance = 10.0m (out of range!)

        # 1. Oversized packet exceeds MTU -> dropped
        oversized = r0.send(["robot_1"], "blob", {"text": "x" * 420})
        self.assertEqual(oversized["accepted_links"], 0)
        self.assertEqual(bus.summary()["drop_reasons"]["mtu_exceeded"], 1)

        # 2. Out-of-range link to robot_2 -> dropped; in-range link to robot_1 -> accepted with 0.5s latency
        sent = r0.send(["robot_1", "robot_2"], "ping", {"k": "v"})
        self.assertEqual(sent["accepted_links"], 1)
        self.assertEqual(sent["dropped_links"], 1)
        self.assertEqual(bus.summary()["drop_reasons"]["out_of_range"], 1)

        # Before latency elapses (t=1000.2), inbox is still empty
        fake_now[0] = 1000.2
        self.assertEqual(r1.poll(), [])

        # After latency elapses (t=1000.6), message arrives!
        fake_now[0] = 1000.6
        arrived = r1.poll()
        self.assertEqual(len(arrived), 1)
        self.assertEqual(arrived[0]["payload"], {"k": "v"})

        # 3. Exhaust burst bandwidth at t=1000.6
        r0.send(["robot_1"], "flood1", {"data": "a" * 80})
        throttled = r0.send(["robot_1"], "flood2", {"data": "b" * 80})
        self.assertEqual(throttled["accepted_links"], 0)
        self.assertGreaterEqual(bus.summary()["drop_reasons"].get("bandwidth_exceeded", 0), 1)

        # Advance clock by 2.0s -> token bucket refills -> send succeeds again!
        fake_now[0] = 1002.6
        recovered = r0.send(["robot_1"], "after_refill", {"ok": True})
        self.assertEqual(recovered["accepted_links"], 1)

    def test_localhost_rpc_and_comm_broker_roundtrip(self):
        rpc = RpcServer(name="unit_rpc")
        rpc.register("echo", lambda x, raw: {"x2": x * 2, "raw": raw})
        ep = rpc.start_background()
        try:
            cli = RpcClient(ep)
            self.assertTrue(cli.healthz()["ok"])
            out = cli.call("echo", x=21, raw=b"\x00\x01\xfe")
            self.assertEqual(out["x2"], 42)
            self.assertEqual(out["raw"], b"\x00\x01\xfe")
            with self.assertRaises(RpcError):
                cli.call("nonexistent_method")
        finally:
            rpc.close()

        # LocalhostCommServer + LocalhostCommClient + Toolkit receipt auditing
        comm_srv = LocalhostCommServer(InMemoryCommBus("ipc_test", channel=IdealChannel()))
        comm_ep = comm_srv.start_background()
        try:
            c0 = LocalhostCommClient(comm_ep, "robot_0", position=(0, 0))
            c1 = LocalhostCommClient(comm_ep, "robot_1", position=(1, 1))

            tk0 = Toolkit()
            tk0.add_tools(build_comm_tools(c0))
            tk0.runtime = HarnessRuntime(lambda *a, **k: None, registry=tk0.registry)
            tk0.runtime.start_episode("ipc_ep")
            tk1 = Toolkit()
            tk1.add_tools(build_comm_tools(c1))

            tx = tk0.execute_tool(
                "comm_broadcast",
                {"topic": "hello", "payload": {"goal": "vase", "pos": [1, 0]}},
            )
            self.assertFalse(tx.is_error)
            self.assertEqual(tx.data["accepted_links"], 1)
            # NETWORK_TX side effect MUST produce a verifiable receipt with 0 violations
            self.assertEqual(tk0.runtime.summary()["receipts"], 1)
            self.assertEqual(tk0.runtime.summary()["violations"], 0)

            rx = tk1.execute_tool("comm_poll", {"max_messages": 10, "topic": "hello"})
            self.assertFalse(rx.is_error)
            self.assertEqual(rx.data["count"], 1)
            self.assertEqual(rx.data["messages"][0]["sender"], "robot_0")
            self.assertEqual(rx.data["messages"][0]["payload"]["pos"], [1, 0])

            st = tk1.execute_tool("comm_status", {})
            self.assertIn("robot_0", st.data["peers"])
        finally:
            comm_srv.close()

    def test_multiprocess_cooperative_search_ideal_vs_blackout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # 1. With localhost intra-machine communication (IdealChannel): both robots exchange
            # opportunistic sightings across separate OS processes and achieve Team SR = 1.0!
            ideal_res = run_coop_grid_demo(channel_mode="ideal", run_dir=base / "ideal")
            self.assertEqual(ideal_res["evaluation"]["team_sr"], 1.0)
            self.assertTrue(ideal_res["evaluation"]["all_succeeded"])
            self.assertGreaterEqual(ideal_res["communication"]["messages_delivered"], 2)
            for nid, w in ideal_res["workers"].items():
                self.assertTrue(w["completed"], nid)
                self.assertEqual(w["harness"]["violations"], 0, nid)

            # 2. Under BlackoutChannel (No-Comm ablation): packets are dropped by the channel,
            # so neither robot receives the cross-map sighting -> Team SR drops to 0.0!
            blackout_res = run_coop_grid_demo(channel_mode="blackout", run_dir=base / "blackout")
            self.assertEqual(blackout_res["evaluation"]["team_sr"], 0.0)
            self.assertEqual(blackout_res["communication"]["messages_delivered"], 0)
            self.assertGreaterEqual(blackout_res["communication"]["messages_dropped"], 2)


if __name__ == "__main__":
    unittest.main()

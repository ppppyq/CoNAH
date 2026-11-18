"""Unit tests for Mode B Multi-Robot HarnessVLN collaboration & coordinate transforms."""

import math
import unittest
import numpy as np

from platforms.hm3d_m3.habitat.pose_utils import (
    local_map_to_global_xz,
    global_xz_to_local_map,
)
from tasks.multi_harness_vln.collaboration import (
    CollaborativeMemoryHub,
    relative_distance_and_direction,
)


class TestMultiRobotPoseTransforms(unittest.TestCase):
    def test_roundtrip_local_global_xz(self):
        start_sim_location = (3.5, -2.1, math.radians(37.0))
        for lx_m, ly_m in [(12.0, 12.0), (14.5, 11.2), (8.3, 15.7)]:
            wx, wz = local_map_to_global_xz(lx_m, ly_m, start_sim_location, map_origin_m=12.0)
            rx, ry = global_xz_to_local_map(wx, wz, start_sim_location, map_origin_m=12.0)
            self.assertAlmostEqual(lx_m, rx, places=6)
            self.assertAlmostEqual(ly_m, ry, places=6)

    def test_relative_distance_and_direction(self):
        # Start at global origin facing o0 = 0 -> dx_sim = d_local_x, dy_sim = d_local_y
        start_sim_location = (0.0, 0.0, 0.0)
        # Robot at local map (12.0, 12.0) facing heading 0 deg (+x_local)
        current_pose = np.array([12.0, 12.0, 0.0], dtype=np.float32)
        # Target 3m ahead in local +x -> dx_sim = +3.0 -> wz = -3.0, wx = 0.0
        wx, wz = local_map_to_global_xz(15.0, 12.0, start_sim_location, map_origin_m=12.0)
        dist_m, direction, (lx, ly) = relative_distance_and_direction(
            current_pose, start_sim_location, wx, wz, map_origin_m=12.0
        )
        self.assertAlmostEqual(dist_m, 3.0, places=4)
        self.assertEqual(direction, "forward")
        self.assertAlmostEqual(lx, 15.0, places=4)
        self.assertAlmostEqual(ly, 12.0, places=4)


class TestMultiRobotCollaborationHub(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.goals = [
            {
                "goal_id": "g1",
                "goal_index": 1,
                "category": "vase",
                "modality": "ON",
                "instruction": "Find the vase",
            },
            {
                "goal_id": "g3",
                "goal_index": 3,
                "category": "radiator",
                "modality": "ON",
                "instruction": "Find the radiator",
            },
        ]
        self.hub = CollaborativeMemoryHub(
            episode_id="ep_test",
            node_names=["robot_0", "robot_1"],
            goals=self.goals,
            emit_event=lambda ev, **kw: self.events.append((ev, kw)),
            allow_cosearch_after_finish=True,
        )

    def test_goal_division_and_cosearch_fallback(self):
        g_r0 = self.hub.claim_initial_or_next_goal("robot_0", (-2.1, 4.4), step=0)
        g_r1 = self.hub.claim_initial_or_next_goal("robot_1", (-2.9, 3.9), step=0)
        self.assertEqual(g_r0["goal_id"], "g1")
        self.assertEqual(g_r1["goal_id"], "g3")

        # Robot 1 finds g3 at step 50
        all_done = self.hub.record_goal_found("robot_1", step=50, goal_id="g3", world_xz=(-3.1, 1.3))
        self.assertFalse(all_done)

        # Robot 1 asks for next goal -> should co-search g1 (which is still ACTIVE)
        g_r1_next = self.hub.claim_initial_or_next_goal("robot_1", (-3.1, 1.3), step=51)
        self.assertIsNotNone(g_r1_next)
        self.assertEqual(g_r1_next["goal_id"], "g1")
        self.assertIn("robot_1", self.hub.goals["g1"]["co_owners"])

        # Robot 1 finds g1 at step 90 -> all goals now FOUND
        all_done_2 = self.hub.record_goal_found("robot_1", step=90, goal_id="g1", world_xz=(-4.5, 0.6))
        self.assertTrue(all_done_2)
        self.assertTrue(self.hub.all_goals_found())

    def test_sighting_and_prompt_formatting(self):
        self.hub.claim_initial_or_next_goal("robot_0", (0.0, 0.0), step=0)
        self.hub.claim_initial_or_next_goal("robot_1", (0.0, -2.0), step=0)
        self.hub.update_robot_pose("robot_0", 10, (0.0, 0.0))
        self.hub.update_robot_pose("robot_1", 10, (0.0, -2.0))

        # Robot 1 spots robot_0's target ('vase')
        sent = self.hub.broadcast_object_sighting(
            sender="robot_1",
            step=12,
            category="vase",
            world_xz=(-4.5, 0.6),
            description="vase on wooden cabinet",
            confidence=0.85,
            source="grounded_sam",
        )
        self.assertTrue(sent)
        self.hub.broadcast_place_observed(
            sender="robot_1",
            step=12,
            world_xz=(-3.0, 1.5),
            place_summary="Dining room with wooden table and radiator",
            objects=["dining table", "radiator"],
            searched_goal="radiator",
        )
        self.hub.deliver_tick(12)

        prompt_block = self.hub.format_team_memory_for_prompt(
            node_id="robot_0",
            current_pose=np.array([12.0, 12.0, 0.0], dtype=np.float32),
            start_sim_location=(0.0, 0.0, 0.0),
            active_goal_category="vase",
            step=13,
        )
        self.assertIn("CONFIRMED TARGET SIGHTINGS FOR YOUR GOAL", prompt_block)
        self.assertIn("vase on wooden cabinet", prompt_block)
        self.assertIn("Already searched by robot_1", prompt_block)
        summary = self.hub.summary()
        self.assertGreaterEqual(summary["communication"]["messages_sent"], 4)
        self.assertEqual(summary["communication"]["messages_delivered"], summary["communication"]["messages_sent"])

    def test_n_robots_scaling_4_robots_3_goals(self):
        goals_3 = [
            {"goal_id": "g1", "goal_index": 1, "category": "vase", "modality": "ON", "instruction": "Find the vase"},
            {"goal_id": "g2", "goal_index": 2, "category": "chair", "modality": "ON", "instruction": "Find the chair"},
            {"goal_id": "g3", "goal_index": 3, "category": "radiator", "modality": "ON", "instruction": "Find the radiator"},
        ]
        hub4 = CollaborativeMemoryHub(
            episode_id="ep_4robots",
            node_names=["robot_0", "robot_1", "robot_2", "robot_3"],
            goals=goals_3,
            emit_event=lambda ev, **kw: self.events.append((ev, kw)),
            allow_cosearch_after_finish=True,
        )
        # First 3 robots each claim a distinct PENDING goal; 4th robot automatically co-searches
        g0 = hub4.claim_initial_or_next_goal("robot_0", (0.0, 0.0), step=0)
        g1 = hub4.claim_initial_or_next_goal("robot_1", (1.0, 0.0), step=0)
        g2 = hub4.claim_initial_or_next_goal("robot_2", (0.0, 1.0), step=0)
        g3 = hub4.claim_initial_or_next_goal("robot_3", (1.0, 1.0), step=0)
        self.assertEqual({g0["goal_id"], g1["goal_id"], g2["goal_id"]}, {"g1", "g2", "g3"})
        self.assertIsNotNone(g3)
        self.assertIn("robot_3", hub4.goals[g3["goal_id"]]["co_owners"])

        # Robot 2 spots a 'vase' in another room and broadcasts to all 3 teammates
        hub4.broadcast_object_sighting(
            sender="robot_2",
            step=15,
            category="vase",
            world_xz=(-3.5, 2.2),
            description="vase on dining table",
            confidence=0.88,
            source="grounded_sam",
        )
        hub4.deliver_tick(15)
        best = hub4.get_best_sighting_for_category(
            category="vase",
            current_pose=np.array([12.0, 12.0, 0.0], dtype=np.float32),
            start_sim_location=(0.0, 0.0, 0.0),
            exclude_sender="robot_0",
        )
        self.assertIsNotNone(best)
        self.assertEqual(best["sender"], "robot_2")
        self.assertAlmostEqual(best["world_xz"][0], -3.5, places=4)


if __name__ == "__main__":
    unittest.main()

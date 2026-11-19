"""RACER ROS Noetic -> CoNAH HTTP JSON-RPC bridge server (`http://127.0.0.1:9001`).

Run inside the ``conah-racer:noetic`` Docker container (with ``--network host``):
    source /opt/ros/noetic/setup.bash
    source /root/racer_ws/devel/setup.bash
    PYTHONPATH=/workspace/CoNAH python3 /workspace/CoNAH/platforms/racer_ros/racer_env_server.py --port 9001

Responsibilities:
1. Subscribes to RACER UAV odometry topics (`/state_ukf/odom_<id>` or `/drone_<id>_visual_slam/odom`).
2. Publishes exploration start trigger (`/move_base_simple/goal`) and per-UAV waypoints.
3. Publishes real-time wireless link & AP/RAN markers to RViz (`/conah/comm_links` MarkerArray):
   - Green line: strong link
   - Yellow line: weak / congested link
   - Red line: NLOS blocked / dropped link
   - Blue cube: fixed AP / AI-RAN node
"""
from __future__ import annotations

import argparse
import threading
import time
from typing import Any, Dict, List, Optional

from comm_harness.rpc import RpcServer


class RacerRosBridge:
    """Thread-safe ROS Noetic adapter exposing RACER simulator state over RPC."""

    def __init__(self, num_uavs: int = 4) -> None:
        import rospy
        from geometry_msgs.msg import Point, PoseStamped
        from nav_msgs.msg import Odometry
        from visualization_msgs.msg import Marker, MarkerArray

        self._rospy = rospy
        self._Point = Point
        self._PoseStamped = PoseStamped
        self._Marker = Marker
        self._MarkerArray = MarkerArray

        self.num_uavs = int(num_uavs)
        self._lock = threading.RLock()
        self._poses: Dict[str, List[float]] = {}
        self._last_seen: Dict[str, float] = {}
        self._triggered = False

        if not rospy.core.is_initialized():
            rospy.init_node("conah_racer_rpc_bridge", anonymous=True, disable_signals=True)

        self._trigger_pub = rospy.Publisher("/move_base_simple/goal", PoseStamped, queue_size=5)
        self._comm_marker_pub = rospy.Publisher("/conah/comm_links", MarkerArray, queue_size=10)
        self._subs = []

        # Subscribe to standard RACER odom topic patterns for drone_1 .. drone_N
        for idx in range(1, self.num_uavs + 1):
            node_id = f"uav_{idx}"
            for topic in (f"/state_ukf/odom_{idx}", f"/drone_{idx}_visual_slam/odom"):
                sub = rospy.Subscriber(
                    topic,
                    Odometry,
                    callback=self._make_odom_cb(node_id),
                    queue_size=10,
                )
                self._subs.append(sub)

    def _make_odom_cb(self, node_id: str):
        def _cb(msg: Any) -> None:
            p = msg.pose.pose.position
            with self._lock:
                self._poses[node_id] = [round(float(p.x), 3), round(float(p.y), 3), round(float(p.z), 3)]
                self._last_seen[node_id] = time.time()

        return _cb

    def trigger_exploration(self, x: float = 0.0, y: float = 0.0, z: float = 1.0) -> Dict[str, Any]:
        """Publish a 2D Nav Goal to start RACER multi-UAV exploration (equivalent to clicking in RViz)."""
        msg = self._PoseStamped()
        msg.header.stamp = self._rospy.Time.now()
        msg.header.frame_id = "world"
        msg.pose.position.x = float(x)
        msg.pose.position.y = float(y)
        msg.pose.position.z = float(z)
        msg.pose.orientation.w = 1.0
        self._trigger_pub.publish(msg)
        with self._lock:
            self._triggered = True
        return {"triggered": True, "goal": [x, y, z]}

    def get_swarm_state(self) -> Dict[str, Any]:
        """Return live 3D positions of all active UAVs observed from ROS odometry."""
        now = time.time()
        with self._lock:
            return {
                "triggered": self._triggered,
                "uav_count": len(self._poses),
                "poses": {k: list(v) for k, v in sorted(self._poses.items())},
                "ages_s": {k: round(now - t, 3) for k, t in sorted(self._last_seen.items())},
            }

    def publish_comm_links_rviz(
        self,
        links: List[Dict[str, Any]],
        ap_positions: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, Any]:
        """Render live wireless link states and AP/RAN nodes inside RViz (`/conah/comm_links`).

        Each item in ``links``:
            {"src": [x,y,z], "dst": [x,y,z], "quality": "good"|"weak"|"dropped"}
        """
        arr = self._MarkerArray()
        stamp = self._rospy.Time.now()
        mid = 0

        # Clear previous frame markers cleanly
        delete_all = self._Marker()
        delete_all.header.frame_id = "world"
        delete_all.header.stamp = stamp
        delete_all.action = self._Marker.DELETEALL
        arr.markers.append(delete_all)

        color_map = {
            "good": (0.10, 0.80, 0.35, 0.90),     # Green
            "weak": (0.95, 0.72, 0.10, 0.90),     # Yellow
            "dropped": (0.90, 0.18, 0.20, 0.85),  # Red
        }

        for link in links:
            src = link.get("src")
            dst = link.get("dst")
            if not src or not dst or len(src) < 3 or len(dst) < 3:
                continue
            quality = str(link.get("quality", "good")).lower()
            r, g, b, a = color_map.get(quality, color_map["good"])

            m = self._Marker()
            m.header.frame_id = "world"
            m.header.stamp = stamp
            m.ns = "comm_links"
            m.id = mid
            mid += 1
            m.type = self._Marker.LINE_STRIP
            m.action = self._Marker.ADD
            m.scale.x = 0.08 if quality != "dropped" else 0.05
            m.color.r, m.color.g, m.color.b, m.color.a = r, g, b, a
            m.points = [
                self._Point(x=float(src[0]), y=float(src[1]), z=float(src[2])),
                self._Point(x=float(dst[0]), y=float(dst[1]), z=float(dst[2])),
            ]
            arr.markers.append(m)

        for ap_name, pos in (ap_positions or {}).items():
            if len(pos) < 3:
                continue
            m = self._Marker()
            m.header.frame_id = "world"
            m.header.stamp = stamp
            m.ns = "ap_nodes"
            m.id = mid
            mid += 1
            m.type = self._Marker.CUBE
            m.action = self._Marker.ADD
            m.pose.position.x = float(pos[0])
            m.pose.position.y = float(pos[1])
            m.pose.position.z = float(pos[2])
            m.pose.orientation.w = 1.0
            m.scale.x = m.scale.y = m.scale.z = 0.45
            m.color.r, m.color.g, m.color.b, m.color.a = (0.06, 0.42, 0.74, 0.95)
            arr.markers.append(m)

        self._comm_marker_pub.publish(arr)
        return {"published_links": len(links), "published_aps": len(ap_positions or {})}


def main() -> None:
    parser = argparse.ArgumentParser(description="CoNAH RACER ROS Noetic RPC Bridge Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--num-uavs", type=int, default=4)
    args = parser.parse_args()

    bridge = RacerRosBridge(num_uavs=args.num_uavs)
    rpc = RpcServer(host=args.host, port=args.port, name="racer_env_server")
    rpc.register("trigger_exploration", bridge.trigger_exploration)
    rpc.register("get_swarm_state", bridge.get_swarm_state)
    rpc.register("publish_comm_links_rviz", bridge.publish_comm_links_rviz)

    print(f"[CoNAH] RACER RPC Server listening at {rpc.endpoint}", flush=True)
    try:
        rpc.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        rpc.close()


if __name__ == "__main__":
    main()

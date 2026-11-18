"""
Design other useful functions
"""

import quaternion
import numpy as np
from typing import List


def get_l2_distance(x1, x2, y1, y2):
    """
    Computes the L2 distance between two points.
    """
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


def get_rel_pose_change(pos2, pos1):
    x1, y1, o1 = pos1
    x2, y2, o2 = pos2

    theta = np.arctan2(y2 - y1, x2 - x1) - o1
    dist = get_l2_distance(x1, x2, y1, y2)
    dx = dist * np.cos(theta)
    dy = dist * np.sin(theta)
    do = o2 - o1

    return dx, dy, do


def get_new_pose(pose, rel_pose_change):
    x, y, o = pose
    dx, dy, do = rel_pose_change

    global_dx = dx * np.sin(np.deg2rad(o)) + dy * np.cos(np.deg2rad(o))
    global_dy = dx * np.cos(np.deg2rad(o)) - dy * np.sin(np.deg2rad(o))
    x += global_dy
    y += global_dx
    o += np.rad2deg(do)
    if o > 180.:
        o -= 360.

    return x, y, o


def threshold_poses(coords, shape):
    coords[0] = min(max(0, coords[0]), shape[0] - 1)
    coords[1] = min(max(0, coords[1]), shape[1] - 1)
    return coords


def get_sim_location(sim, agent_id: int = 0):
    """Returns x, y, o pose of the agent in the Habitat simulator."""
    agent_state = sim.get_agent_state(agent_id)
    x = -agent_state.position[2]
    y = -agent_state.position[0]
    axis = quaternion.as_euler_angles(agent_state.rotation)[0]
    if (axis % (2 * np.pi)) < 0.1 or (axis %
                                        (2 * np.pi)) > 2 * np.pi - 0.1:
        o = quaternion.as_euler_angles(agent_state.rotation)[1]
    else:
        o = 2 * np.pi - quaternion.as_euler_angles(agent_state.rotation)[1]
    if o > np.pi:
        o -= 2 * np.pi
    return x, y, o


def get_start_sim_location(position: List, rotation: List) -> tuple:
    x = -position[2]
    y = -position[0]
    axis = quaternion.as_euler_angles(rotation)[0]
    if (axis % (2 * np.pi)) < 0.1 or (axis %
                                        (2 * np.pi)) > 2 * np.pi - 0.1:
        o = quaternion.as_euler_angles(rotation)[1]
    else:
        o = 2 * np.pi - quaternion.as_euler_angles(rotation)[1]
    if o > np.pi:
        o -= 2 * np.pi
        
    return x, y, o


def get_pose_change(sim, last_sim_location, agent_id: int = 0):
    """Returns dx, dy, do pose change of the agent relative to the last
    timestep."""
    curr_sim_pose = get_sim_location(sim, agent_id=agent_id)
    dx, dy, do = get_rel_pose_change(curr_sim_pose, last_sim_location)
    
    return dx, dy, do, curr_sim_pose


def local_map_to_global_xz(local_x_m: float, local_y_m: float, start_sim_location: tuple, map_origin_m: float = 12.0) -> tuple:
    """Convert a robot's local Semantic_Mapping metre coordinates (x_m, y_m) to Habitat global (wx, wz)."""
    dx = float(local_x_m) - map_origin_m
    dy = float(local_y_m) - map_origin_m
    x0, y0, o0 = start_sim_location
    sx = x0 + dx * np.cos(o0) - dy * np.sin(o0)
    sy = y0 + dx * np.sin(o0) + dy * np.cos(o0)
    return float(-sy), float(-sx)


def global_xz_to_local_map(wx: float, wz: float, start_sim_location: tuple, map_origin_m: float = 12.0) -> tuple:
    """Convert Habitat global (wx, wz) metres into a robot's local Semantic_Mapping metre coordinates (x_m, y_m)."""
    rel = get_rel_pose_change((-float(wz), -float(wx), 0.0), start_sim_location)
    mx, my, _ = get_new_pose((map_origin_m, map_origin_m, 0.0), (rel[0], rel[1], 0.0))
    return float(mx), float(my)

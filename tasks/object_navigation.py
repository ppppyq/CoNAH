"""Object-navigation task catalog and completion policy, independent of Habitat.

The task consumes sensor facts through an injected depth query. Target identity
comes from visual verification in the planner; no ground-truth goal is accepted.
"""
import math
from comm_harness.tools.contracts import SideEffect
from comm_harness.tools.registry import ToolRegistry, ToolSpec


TASK_TOOLS = (
    ToolSpec('recall_place', SideEffect.READ_ONLY, 'Recall already-observed places.'),
    ToolSpec('plan_navigation', SideEffect.PLANNING, 'Choose a navigation action.'),
    ToolSpec('ground_target', SideEffect.PLANNING, 'Locate the intended object in an image.'),
    ToolSpec('verify_stop', SideEffect.PLANNING, 'Verify target identity and task completion.'),
    ToolSpec('move_to_pixel', SideEffect.WORLD_MUTATING, 'Task-level approach operation.'),
    ToolSpec('backtrack', SideEffect.WORLD_MUTATING, 'Task-level return to a visited waypoint.'),
)


class ObjectNavigationTask:
    name = 'object_navigation'
    required_capabilities = frozenset(('observe_panorama', 'query_depth', 'project_target',
                                       'move', 'turn', 'action', 'stop'))
    allowed_tools = required_capabilities | frozenset(t.name for t in TASK_TOOLS)

    def __init__(self, config):
        self.legacy_stop_enabled = getattr(config, 'LEGACY_STOP_ENABLED', False)
        self.stop_geometric_gate_enabled = getattr(config, 'STOP_GEOMETRIC_GATE', True)
        self.stop_gate_distance_m = getattr(config, 'STOP_GEOMETRIC_GATE_DISTANCE_M', 1.0)
        self.stop_gate_max_target_age_steps = getattr(config, 'STOP_GEOMETRIC_GATE_MAX_TARGET_AGE_STEPS', 80)
        self.stop_gate_distance_basis = getattr(config, 'STOP_GATE_DISTANCE_BASIS', 'fresh_depth')
        self.stop_gate_depth_distance_m = getattr(config, 'STOP_GATE_DEPTH_DISTANCE_M', 2.5)
        self.stop_gate_depth_max_std_m = getattr(config, 'STOP_GATE_DEPTH_MAX_STD_M', 0.5)

    def registry(self, platform_specs):
        specs = tuple(platform_specs)
        missing = self.required_capabilities - {s.name for s in specs}
        if missing:
            raise ValueError('Platform lacks navigation capabilities: ' + ', '.join(sorted(missing)))
        registry = ToolRegistry()
        for spec in (*TASK_TOOLS, *specs):
            registry.register(spec)
        return registry

    def check_stop(self, *, step, current_pose, agent_map_x, agent_map_y, reason,
                   last_grounded_target, query_depth, stop_evidence=None, panorama_frames=None):
        self.step = step
        self.last_grounded_target = last_grounded_target
        self.query_depth = query_depth
        return self._stop_geometric_gate(step, current_pose, agent_map_x, agent_map_y,
                                         reason, stop_evidence, panorama_frames)

    def _stop_gate_fresh_depth(self, stop_evidence, panorama_frames):
        distance, info = self.query_depth(stop_evidence, panorama_frames, self.step)
        if distance is not None and info.get('depth_window_std_m', 0) > self.stop_gate_depth_max_std_m:
            info = dict(info, depth_invalid_reason='depth_unstable')
            return None, info
        return distance, info

    def _stop_gate_map_target(self, step: int, current_pose, agent_map_x: int,
                              agent_map_y: int):
        """Distance to the last stored grounded target.

        Retained as the fallback and, more importantly, recorded on every gate
        event next to the fresh-depth reading so the two bases can be compared
        offline without spending another full run.
        """
        info = {'distance_map_target_m': None}
        target = self.last_grounded_target
        if target is None:
            info['map_target_invalid_reason'] = 'no_grounded_target'
            return None, info

        age = int(step) - int(target['step'])
        info.update({
            'target_age_steps': age,
            'target_map': target['map'],
            'target_world': target['world'],
            'target_source': target['source'],
            'target_planned_action': target.get('planned_action'),
        })
        try:
            dx = float(current_pose[0]) - float(target['world'][0])
            dy = float(current_pose[1]) - float(target['world'][1])
            distance_m = float(math.sqrt(dx * dx + dy * dy))
        except Exception as e:
            info['map_target_invalid_reason'] = f'distance_error:{e}'
            return None, info

        info['distance_map_target_m'] = distance_m
        if not math.isfinite(distance_m):
            info['map_target_invalid_reason'] = 'non_finite_distance'
            return None, info
        if age > self.stop_gate_max_target_age_steps:
            info['map_target_invalid_reason'] = 'stale_target'
            return None, info
        return distance_m, info


    def _stop_geometric_gate(self, step: int, current_pose, agent_map_x: int,
                             agent_map_y: int, reason: str,
                             stop_evidence=None, panorama_frames=None):
        """Decide whether the agent is geometrically close enough to STOP.

        Two bases are computed every time and both are written to the trace:
        `fresh_depth` (metric depth at the bbox the double-check framed) and
        `map_target` (distance to the last stored projection). `EVAL.STOP_GATE_DISTANCE_BASIS` picks which one decides;
        the other rides along so the choice can be re-evaluated offline.

        Whichever basis is used, it must never consult `episode.goals`, goal
        view points, `infos[...]['distance_to_goal']` or `sim.geodesic_distance`:
        those are privileged goal state and using them would invalidate the
        zero-shot setting. Depth and the agent's own projections are fair game.

        Fails closed. A wrong STOP ends the episode at 0; a wrong CONTINUE only
        spends steps and can still recover.
        """
        info = {'reason': reason}
        if self.legacy_stop_enabled:
            info['mode'] = 'legacy_bypass'
            return True, info
        if not self.stop_geometric_gate_enabled:
            info['mode'] = 'gate_disabled'
            return True, info

        # Both radii go in the trace unconditionally; `threshold_m` below is set
        # to whichever one the chosen basis is actually judged against, so a
        # reader never has to guess which radius a decision used.
        info['threshold_map_target_m'] = float(self.stop_gate_distance_m)
        info['threshold_fresh_depth_m'] = float(self.stop_gate_depth_distance_m)
        info['agent_map'] = [int(agent_map_x), int(agent_map_y)]
        try:
            info['agent_pose'] = [float(current_pose[0]), float(current_pose[1]),
                                  float(current_pose[2])]
        except Exception:
            info['agent_pose'] = None

        fresh_m, fresh_info = self._stop_gate_fresh_depth(stop_evidence, panorama_frames)
        info.update(fresh_info)
        map_m, map_info = self._stop_gate_map_target(
            step, current_pose, agent_map_x, agent_map_y)
        info.update(map_info)

        # Preference order: the configured basis first, then the other one. The
        # fallback is not a technicality -- both are measured geometry, and
        # refusing to decide because the preferred sensor reading was unusable
        # would send the agent back to burning steps it does not have.
        prefer_fresh = str(self.stop_gate_distance_basis) == 'fresh_depth'
        candidates = ([('fresh_depth', fresh_m), ('map_target', map_m)] if prefer_fresh
                      else [('map_target', map_m), ('fresh_depth', fresh_m)])
        basis, distance_m = next(((b, d) for b, d in candidates if d is not None),
                                (None, None))
        info['distance_basis'] = basis
        if basis is None:
            # Report why the *preferred* basis was unusable; that is the one whose
            # failure modes we are trying to characterise.
            preferred_reason = (info.get('depth_invalid_reason') if prefer_fresh
                                else info.get('map_target_invalid_reason'))
            other_reason = (info.get('map_target_invalid_reason') if prefer_fresh
                            else info.get('depth_invalid_reason'))
            info['fail_reason'] = preferred_reason or other_reason or 'no_usable_distance'
            return False, info

        # Each basis carries its own threshold: map_target measures to a point
        # already pulled back onto standing-height floor, fresh_depth measures to
        # the object surface, and goal view points sit a median 1.57 m back from
        # the object. Sharing one radius would make the depth basis reject stops
        # taken from the best possible standing position.
        threshold = (float(self.stop_gate_depth_distance_m) if basis == 'fresh_depth'
                     else float(self.stop_gate_distance_m))
        info['threshold_m'] = threshold

        info['distance_m'] = distance_m
        if distance_m > threshold:
            info['fail_reason'] = 'distance_exceeds_threshold'
            return False, info
        return True, info


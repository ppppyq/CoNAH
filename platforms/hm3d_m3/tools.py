"""HM3D sensor/action capabilities; no target-category or task-success decisions.

The current interface is an in-process capability API. Sensor frames and private
environment outputs are not exposed as model-facing JSON tools. The task loop
chooses the capabilities; the common executor owns their traces and receipts.
"""
from functools import partial

from comm_harness.tools import ToolError
from comm_harness.tools.contracts import SideEffect
from comm_harness.tools.executor import ToolOutcome
from comm_harness.tools.registry import ToolSpec


PLATFORM_TOOLS = (
    ToolSpec('observe_panorama', SideEffect.READ_ONLY,
             'One physical left turn in a panorama capture; returns updated sensors.',
             step_consuming=True, physical_motion=True),
    ToolSpec('query_depth', SideEffect.READ_ONLY,
             'Read metric depth statistics at an observed image bounding box.'),
    ToolSpec('project_target', SideEffect.PLANNING,
             'Project an image pixel with measured depth into map/world coordinates.'),
    ToolSpec('move', SideEffect.WORLD_MUTATING, 'Execute MOVE_FORWARD.', step_consuming=True),
    ToolSpec('turn', SideEffect.WORLD_MUTATING, 'Execute TURN_LEFT or TURN_RIGHT.', step_consuming=True),
    ToolSpec('action', SideEffect.WORLD_MUTATING, 'Execute another supported sensor action.', step_consuming=True),
    ToolSpec('stop', SideEffect.TERMINAL, 'Execute STOP after the task decides to finish.',
             step_consuming=True, requires_stop_evidence=True),
)

ACTION_PARAMETERS = {'type': 'object', 'properties': {
    'actions': {'type': 'array', 'minItems': 1, 'maxItems': 1,
                'items': {'type': 'integer', 'minimum': 0, 'maximum': 5}}},
    'required': ['actions'], 'additionalProperties': False}


class HabitatTools:
    specs = PLATFORM_TOOLS

    def __init__(self, environment, min_depth=0.1, max_depth=5.0):
        # Late-bound because the legacy evaluator recreates environments.
        self.environment = environment
        self.min_depth, self.max_depth = float(min_depth), float(max_depth)

    def register(self, executor):
        for spec in self.specs:
            if spec.name in ('query_depth', 'project_target'):
                executor.bind(spec, getattr(self, spec.name), owner='platform:hm3d_m3')
            else:
                executor.bind(spec, partial(self._step, spec.name), owner='platform:hm3d_m3',
                              parameters=ACTION_PARAMETERS)

    @staticmethod
    def action_name(action_id):
        if action_id == 0:
            return 'stop'
        if action_id == 1:
            return 'move'
        if action_id in (2, 3):
            return 'turn'
        return 'action'

    def _step(self, name, actions):
        valid = {'stop': (0,), 'move': (1,), 'turn': (2, 3),
                 'observe_panorama': (2,), 'action': (4, 5)}
        if any(action not in valid[name] for action in actions):
            raise ToolError('action does not match capability: ' + name)
        outputs = self.environment().step([{'action': action} for action in actions])
        # Environment metrics may contain privileged goal information. Only the
        # evaluator receives them; the tool trace records the action, not metrics.
        return ToolOutcome(outputs, {'actions': actions}, consumed_steps=1)

    @staticmethod
    def preprocess_depth(depth, min_depth, max_depth):
        # Preserve the author conversion and centimetre output exactly.
        depth = depth[:, :, 0] * 1
        for i in range(depth.shape[1]):
            depth[:, i][depth[:, i] == 0.] = depth[:, i].max()
        depth[depth > 0.99] = 0.
        depth[depth == 0] = 1.0
        return min_depth * 100.0 + depth * (max_depth - min_depth) * 100.0

    def query_depth(self, frames, evidence, window=5, anchor_top=False):
        import numpy as np
        info = {'distance_fresh_depth_m': None}
        def invalid(reason):
            info['depth_invalid_reason'] = reason
            return ToolOutcome((None, info), dict(info))
        if not evidence:
            return invalid('no_stop_evidence')
        if not frames:
            return invalid('no_panorama')
        view = evidence.get('view_idx')
        if view is None or not (0 <= view < len(frames)):
            return invalid('view_out_of_range')
        raw_depth = frames[view].get('depth')
        if raw_depth is None:
            return invalid('frame_has_no_depth')
        try:
            depth = self.preprocess_depth(np.array(raw_depth, dtype=np.float32, copy=True),
                                          self.min_depth, self.max_depth) / 100.0
        except Exception as exc:
            return invalid('depth_preprocess_error:' + str(exc))
        h, w = depth.shape[:2]
        box = evidence['bbox_norm']
        if len(box) != 4 or not np.isfinite(box).all():
            return invalid('invalid_bbox')
        x1, y1, x2, y2 = box
        px1, px2 = x1 / 1000.0 * w, x2 / 1000.0 * w
        py1, py2 = y1 / 1000.0 * h, y2 / 1000.0 * h
        x = int(np.clip((px1 + px2) / 2.0, 0, w - 1))
        y = int(np.clip(py1 if anchor_top else py2, 0, h - 1))
        half = max(1, int(window)) // 2
        patch = depth[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1]
        valid = patch[np.isfinite(patch)]
        if not valid.size:
            return invalid('empty_depth_patch')
        median, std = float(np.median(valid)), float(np.std(valid))
        info.update(distance_fresh_depth_m=median, depth_window_std_m=std,
                    depth_anchor_px=[x, y], depth_view=evidence.get('target_view'),
                    depth_bbox_norm=list(box))
        if median >= self.max_depth - 0.05:
            return invalid('depth_clipped_at_max')
        return ToolOutcome((median, info), dict(info))

    @staticmethod
    def project_target(pixel_coords, depth_image, full_pose, camera_intrinsics):
        from platforms.hm3d_m3.navigation.depth_utils import get_world_xz_from_pixel
        target = get_world_xz_from_pixel(pixel_coords=pixel_coords, depth_image=depth_image,
                                       full_pose=full_pose, camera_intrinsics=camera_intrinsics)
        return ToolOutcome(target, {'target_world': target})

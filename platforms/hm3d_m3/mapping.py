"""Depth-only local occupancy and FMM navigation; never reads Habitat's pathfinder."""
import math
import numpy as np
import cv2
import skfmm
from scipy import ndimage
from PIL import Image, ImageDraw


class ObservedMap:
    def __init__(self, position, resolution=0.1, size=501, radius=0.18):
        self.resolution, self.size, self.radius = resolution, size, radius
        self.origin = np.asarray([position[0], position[2]]) - size * resolution / 2
        self.free = np.zeros((size, size), dtype=bool)
        self.occupied = np.zeros_like(self.free)
        self.visits = np.zeros((size, size), dtype=np.int32)
        self.obs = None

    def cell(self, xz):
        x, z = np.rint((np.asarray(xz) - self.origin) / self.resolution).astype(int)
        return int(z), int(x)

    def world(self, cell):
        return (self.origin + np.asarray([cell[1], cell[0]]) * self.resolution).tolist()

    def inside(self, cell):
        return 0 <= cell[0] < self.size and 0 <= cell[1] < self.size

    def update(self, obs):
        self.obs = obs
        depth = np.asarray(obs['depth'])
        h, w = depth.shape
        fx = w / (2 * math.tan(math.radians(obs['hfov']) / 2))
        matrix = np.asarray(obs['rotation_matrix'])
        camera = np.asarray(obs['camera_position'])
        floor = obs['position'][1]
        start = self.cell([obs['position'][0], obs['position'][2]])
        if not self.inside(start):
            raise ValueError('robot moved beyond configured local map')
        free_patch = np.zeros_like(self.free, dtype=np.uint8)
        hits = []
        rows = np.arange(12, h - 10, 12)
        for col in range(4, w - 4, 8):
            d = depth[rows, col]
            valid = np.isfinite(d) & (d > 0.12) & (d <= obs['max_depth'] + .01)
            if not valid.any():
                continue
            dv, rv = d[valid], rows[valid]
            points = np.stack(((col - (w - 1) / 2) * dv / fx,
                               -(rv - (h - 1) / 2) * dv / fx, -dv), axis=1)
            points = points.dot(matrix.T) + camera
            height = points[:, 1] - floor
            distances = np.linalg.norm(points[:, [0, 2]] - np.asarray(obs['position'])[[0, 2]], axis=1)
            obstacles = (height > .18) & (height < 1.6) & (dv < obs['max_depth'] - .05)
            if obstacles.any():
                choices = np.flatnonzero(obstacles)
                chosen = choices[np.argmin(distances[choices])]
                endpoint = points[chosen, [0, 2]]
                hit = self.cell(endpoint)
                if self.inside(hit):
                    hits.append(hit)
            else:
                # A valid mid-height ray sees open space up to its measured range.
                choices = np.flatnonzero((height > .05) & (height < 1.6))
                if not len(choices):
                    choices = np.arange(len(points))
                chosen = choices[np.argmax(distances[choices])]
                endpoint = points[chosen, [0, 2]]
            end = self.cell(endpoint)
            cv2.line(free_patch, (start[1], start[0]), (end[1], end[0]), 1, 2)
        self.free |= free_patch.astype(bool)
        for cell in hits:
            self.occupied[cell] = True
        # The robot's already occupied footprint is known traversible.
        cv2.circle(free_patch, (start[1], start[0]), 2, 1, -1)
        self.free |= free_patch.astype(bool)
        self.occupied[start[0]-1:start[0]+2, start[1]-1:start[1]+2] = False
        self.free[self.occupied] = False
        self.visits[start] += 1

    def traversible(self):
        pad = int(math.ceil(self.radius / self.resolution))
        yy, xx = np.ogrid[-pad:pad+1, -pad:pad+1]
        blocked = ndimage.binary_dilation(self.occupied, structure=(xx * xx + yy * yy <= pad * pad))
        allowed = self.free & ~blocked
        current = self.cell(np.asarray(self.obs['position'])[[0, 2]])
        allowed[current] = True
        return allowed

    def field(self, goal, allowed=None):
        if allowed is None:
            allowed = self.traversible()
        if not self.inside(goal) or not allowed[goal]:
            return None
        phi = np.ma.masked_where(~allowed, np.ones_like(allowed, dtype=float))
        phi[goal] = 0
        try:
            return np.ma.filled(skfmm.distance(phi, dx=self.resolution), np.inf)
        except (ValueError, RuntimeError):
            return None

    def candidates(self, tick, graph=None):
        current = self.cell(np.asarray(self.obs['position'])[[0, 2]])
        allowed = self.traversible()
        distance = self.field(current, allowed)
        if distance is None:
            return []
        unknown = ~(self.free | self.occupied)
        frontier = allowed & ndimage.binary_dilation(unknown, iterations=2)
        possible = frontier & (distance > .8) & (distance < 5)
        if not possible.any():
            possible = allowed & (distance > 1) & (distance < 3)
        cells = np.argwhere(possible)
        if not len(cells):
            return []
        novelty = ndimage.uniform_filter(self.visits.astype(float), size=15)
        # Prefer reachable new space, but keep alternatives in distinct directions.
        ranks = sorted(cells.tolist(), key=lambda c: (novelty[tuple(c)] * 30 + abs(distance[tuple(c)] - 2.3), c))
        selected = []
        for c in ranks:
            if any(np.linalg.norm(np.asarray(c) - p) * self.resolution < .9 for p in selected):
                continue
            selected.append(c)
            if len(selected) >= 6:
                break
        records = []
        for i, cell in enumerate(selected):
            records.append({'id': 'f%d_%d' % (tick, i), 'kind': 'frontier', 'cell': cell,
                            'world_xz': self.world(cell), 'path_distance_m': round(float(distance[tuple(cell)]), 2),
                            'bearing_deg': round(self.bearing(self.world(cell)), 1)})
        if graph is not None:
            for place in list(graph.places.values())[-8:]:
                c = self.cell(place.world_xz)
                if self.inside(c) and .8 < distance[c] < 8:
                    records.append({'id': 'back_' + place.place_id, 'kind': 'backtrack', 'cell': list(c),
                                    'world_xz': list(place.world_xz), 'path_distance_m': round(float(distance[c]), 2),
                                    'bearing_deg': round(self.bearing(place.world_xz), 1)})
        return records

    def bearing(self, xz):
        delta = np.asarray(xz) - np.asarray(self.obs['position'])[[0, 2]]
        rotation = np.asarray(self.obs['rotation_matrix'])
        forward, right = -rotation[[0, 2], 2], rotation[[0, 2], 0]
        return math.degrees(math.atan2(float(delta.dot(right)), float(delta.dot(forward))))

    def approach_candidate(self, object_xz, identifier):
        """Choose a reachable observation point near a depth-grounded hypothesis."""
        current = self.cell(np.asarray(self.obs['position'])[[0, 2]])
        distance = self.field(current)
        if distance is None:
            return None
        cells = np.argwhere(np.isfinite(distance) & (distance > .4) & (distance < 5))
        if not len(cells):
            return None
        world = self.origin + cells[:, [1, 0]] * self.resolution
        range_to_object = np.linalg.norm(world - np.asarray(object_xz), axis=1)
        valid = (range_to_object >= .45) & (range_to_object <= 1.15)
        if not valid.any():
            return None
        cells, range_to_object = cells[valid], range_to_object[valid]
        cost = abs(range_to_object - .7) + .1 * distance[cells[:, 0], cells[:, 1]]
        cell = tuple(cells[int(np.argmin(cost))])
        return {'id': identifier, 'kind': 'object_candidate', 'cell': list(map(int, cell)),
                'world_xz': self.world(cell), 'path_distance_m': round(float(distance[cell]), 2),
                'bearing_deg': round(self.bearing(self.world(cell)), 1)}

    def next_action(self, destination):
        current = self.cell(np.asarray(self.obs['position'])[[0, 2]])
        goal = self.cell(destination)
        field = self.field(goal)
        if field is None or not np.isfinite(field[current]):
            return 0, 'unreachable_in_observed_map'
        if field[current] < .3:
            return 0, 'arrived'
        r, c = current
        options = []
        for dr in range(-4, 5):
            for dc in range(-4, 5):
                q = r + dr, c + dc
                if dr * dr + dc * dc <= 16 and self.inside(q) and np.isfinite(field[q]):
                    # Check the straight short segment, not just the destination cell.
                    steps = max(abs(dr), abs(dc), 1)
                    rr = np.rint(np.linspace(r, q[0], steps + 1)).astype(int)
                    cc = np.rint(np.linspace(c, q[1], steps + 1)).astype(int)
                    if np.all(np.isfinite(field[rr, cc])):
                        options.append((field[q], q))
        if not options:
            return 0, 'no_short_term_goal'
        _, target = min(options)
        if field[target] >= field[current] - .01:
            return 0, 'no_fmm_progress'
        bearing = self.bearing(self.world(target))
        if bearing > 17:
            return 3, 'running'
        if bearing < -17:
            return 2, 'running'
        return 1, 'running'

    def render(self, candidates):
        current = self.cell(np.asarray(self.obs['position'])[[0, 2]])
        half = 65
        r0, r1 = max(0, current[0]-half), min(self.size, current[0]+half)
        c0, c1 = max(0, current[1]-half), min(self.size, current[1]+half)
        pixels = np.full((r1-r0, c1-c0, 3), 110, dtype=np.uint8)
        pixels[self.free[r0:r1, c0:c1]] = [240, 240, 240]
        pixels[self.occupied[r0:r1, c0:c1]] = [25, 25, 25]
        image = Image.fromarray(pixels).resize((520, 520), Image.Resampling.NEAREST if hasattr(Image, 'Resampling') else Image.NEAREST)
        draw = ImageDraw.Draw(image)
        def point(cell):
            return (int((cell[1]-c0) * 520/(c1-c0)), int((cell[0]-r0)*520/(r1-r0)))
        x, y = point(current)
        draw.ellipse((x-5, y-5, x+5, y+5), fill='red')
        forward = -np.asarray(self.obs['rotation_matrix'])[[0, 2], 2]
        draw.line((x, y, x+forward[0]*22, y+forward[1]*22), fill='red', width=3)
        for candidate in candidates:
            x, y = point(candidate['cell'])
            draw.ellipse((x-3, y-3, x+3, y+3), fill='blue')
            draw.text((x+4, y), candidate['id'], fill='blue')
        return image

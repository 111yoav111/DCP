import numpy as np
from src.tasks.divisible_task import DivisibleTask
import pickle
import zstandard as zstd


class RenderTask(DivisibleTask):

    CAMERA  = np.array([0., 0., 0.])
    LIGHT   = np.array([5., 5., -10.])
    AMBIENT = 0.3

    def __init__(self, width: int = 512, height: int = 512, difficulty: float = 1.0,
                 scene: list = None, row_start: int = None, row_end: int = None):

        super().__init__(difficulty)

        self.width = width
        self.height = height
        self.row_start = row_start if row_start is not None else 0
        self.row_end = row_end   if row_end   is not None else height

        np.random.seed(42)
        self.scene = scene if scene is not None else self._default_scene()

    def _default_scene(self) -> list[dict]:
        return [
            {'position': np.array([ 0.,  0., -5.]), 'radius': 1.0, 'color': np.array([ 75.,   56.,   0.])},
            {'position': np.array([ 2.,  1., -6.]), 'radius': 0.8, 'color': np.array([  67.,  10.,  73.])},
            {'position': np.array([-2., -1., -4.]), 'radius': 0.6, 'color': np.array([100., 100., 100.])},
        ]

    @staticmethod 
    def _normalize(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    @staticmethod
    def _intersect(ray_o, ray_d, pos, radius) -> float:
        oc = ray_o - pos
        b = 2 * np.dot(ray_d, oc)
        c = np.dot(oc, oc) - radius ** 2
        disc = b ** 2 - 4 * np.dot(ray_d, ray_d) * c
        if disc < 0:
            return np.inf
        t1 = (-b - np.sqrt(disc)) / (2 * np.dot(ray_d, ray_d))
        t2 = (-b + np.sqrt(disc)) / (2 * np.dot(ray_d, ray_d))
        return t1 if t1 > 0 else (t2 if t2 > 0 else np.inf)

    def _trace(self, ray_o, ray_d) -> np.ndarray:
        t = np.inf
        hit = None
        for obj in self.scene:
            d = self._intersect(ray_o, ray_d, obj['position'], obj['radius'])
            if d < t:
                t = d
                hit = obj
        if hit is None:
            return np.array([20., 20., 30.])

        point = ray_o + ray_d * t
        normal = self._normalize(point - hit['position'])
        to_light = self._normalize(self.LIGHT - point)

        shadow_o = point + normal * 0.0001
        in_shadow = any(
            self._intersect(shadow_o, to_light, o['position'], o['radius']) < np.inf
            for o in self.scene if o is not hit
        )
        color = hit['color'] * self.AMBIENT
        if not in_shadow:
            color += hit['color'] * max(np.dot(normal, to_light), 0)
        return np.clip(color, 0, 255)

    def execute(self) -> dict:
        self.status = "RUNNING"
        rows = self.row_end - self.row_start
        image = np.zeros((rows, self.width, 3), dtype=np.uint8)

        ys = np.linspace(-1, 1, self.height)[self.row_start:self.row_end]
        xs = np.linspace(-1, 1, self.width)

        for i, y in enumerate(ys):
            for j, x in enumerate(xs):
                ray_d = self._normalize(np.array([x, y, -1.]) - self.CAMERA)
                image[i, j] = self._trace(self.CAMERA, ray_d)

        self.result = {
            'image_data': image.tobytes(),
            'row_start':  self.row_start,   
            'row_end':    self.row_end,
            'shape':      image.shape,
        }
        self.status = "DONE"
        return self.result

    # ── split / merge ─────────────────────────────────────────────────────────

    def split_into_subtasks(self, num_workers: int = 1) -> list['RenderTask']:
        """
        Split the render by rows — each subtask gets an equal rows to work.
        Every subtask is fully independent (same scene(total img), different row range).
        """
        total_rows = self.row_end - self.row_start

        # not worth splitting if there are fewer rows than workers
        if num_workers <= 1 or total_rows < num_workers:
            return [self]

        rows_per_worker = total_rows // num_workers
        subtasks = []

        for i in range(num_workers):
            r_start = self.row_start + i * rows_per_worker
            r_end   = r_start + rows_per_worker if i < num_workers - 1 else self.row_end # last worker gets any leftover rows


            st = RenderTask(
                width = self.width,
                height = self.height,
                difficulty = self.task_difficulty / num_workers,
                scene = self.scene,        
                row_start = r_start,
                row_end = r_end,
            )
            st._mark_as_subtask(self.task_id, i)
            subtasks.append(st)

        return subtasks

    def merge_results(self, subtask_results: list[dict]) -> dict:
        """
        Reassemble the full image from subtask strips.
        subtask_results must be sorted by row_start before calling.
        """
        sorted_results = sorted(subtask_results, key=lambda r: r['row_start'])

        strips = [
            np.frombuffer(r['image_data'], dtype=np.uint8).reshape(r['shape'])
            for r in sorted_results
        ]
        full_image = np.vstack(strips) #combine all strips to full image.

        return {
            'image_data': full_image.tobytes(),
            'width': self.width,
            'height': self.height,
            'shape': full_image.shape,
        }

    def to_bytes(self) -> bytes:
        """Serialization is handled externally by packet_codec (pickle+zstd)."""
        return zstd.ZstdCompressor(level=3).compress(pickle.dumps(self))

    @classmethod
    def from_bytes(cls, data: bytes) -> 'RenderTask':
        return pickle.loads(zstd.ZstdDecompressor().decompress(data))

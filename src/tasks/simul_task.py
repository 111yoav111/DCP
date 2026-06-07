import numpy as np
from src.tasks.divisible_task import DivisibleTask


class SimulationTask(DivisibleTask):

    SIMULATION_TYPES = ['primes', 'matrix', 'monte_carlo']

    def __init__(self, simulation_type: str = 'primes', difficulty: float = 1.0,
                 start_range: int = None, end_range: int = None,
                 matrix_size: int = None, iterations: int = None,
                 num_samples: int = None):

        if simulation_type not in self.SIMULATION_TYPES:
            raise ValueError(f"simulation_type must be one of {self.SIMULATION_TYPES}")

        super().__init__(difficulty)
        self.simulation_type = simulation_type

        if simulation_type == 'primes':
            if start_range is not None:
                self.primes_start_range = start_range
            else:
                self.primes_start_range = 2

            if end_range is not None:
                self.primes_end_range = end_range
            else:
                self.primes_end_range = 1_000_000

        elif simulation_type == 'matrix':
            if matrix_size is not None:
                self.matrix_size = matrix_size
            else:
                self.matrix_size = 500

            if iterations is not None:
                self.round_matrix = iterations
            else:
                self.round_matrix = 10

        elif simulation_type == 'monte_carlo':
            if num_samples is not None:
                self.arrows_thrown = num_samples
            else:
                self.arrows_thrown = 10_000_000

    def find_primes(self, start: int, end: int) -> list[int]:
        primes_sieve = np.ones(end, dtype=bool)  # mark all True (primes)
        primes_sieve[:2] = False
        for i in range(2, int(end ** 0.5) + 1):  # need to check only up to sqrt(end)
            if primes_sieve[i]:
                primes_sieve[i*i::i] = False  # mark all the multiples of i as not prime

        return [int(x) for x in np.where(primes_sieve)[0] if x >= start]

    def matrix_simulation(self, size: int, round_matrix: int) -> float:
        matrix = np.random.rand(size, size)
        for _ in range(round_matrix):
            matrix = np.dot(matrix, matrix)
            matrix = matrix / np.max(matrix)
        return float(np.sum(matrix))

    def monte_carlo_pi(self, arrows_thrown: int) -> float:
        x = np.random.uniform(-1, 1, arrows_thrown)
        y = np.random.uniform(-1, 1, arrows_thrown)
        arrows_inside = int(np.sum((x ** 2 + y ** 2) <= 1))
        return (arrows_inside / arrows_thrown) * 4

    def execute(self) -> dict:
        self.status = "RUNNING"

        if self.simulation_type == 'primes':
            primes = self.find_primes(self.primes_start_range, self.primes_end_range)
            result = {
                'type': 'primes',
                'primes': primes,
                'count': len(primes),
                'range': (self.primes_start_range, self.primes_end_range)
            }

        elif self.simulation_type == 'matrix':
            sum_result = self.matrix_simulation(self.matrix_size, self.round_matrix)
            result = {
                'type': 'matrix',
                'sum': sum_result,
                'matrix_size': self.matrix_size,
                'rounds': self.round_matrix
            }

        elif self.simulation_type == 'monte_carlo':
            pi_estimate = self.monte_carlo_pi(self.arrows_thrown)
            result = {
                'type': 'monte_carlo',
                'pi_estimate': pi_estimate,
                'arrows_thrown': self.arrows_thrown
            }

        self.status = "DONE"
        self.result = result
        return result

    def _split_by_weights(self, weights: list[float]) -> list['SimulationTask']:
        """
        Task-specific split: routes to the correct split method by simulation type.

        matrix is not splittable so returns as is-[self].
        """
        if self.simulation_type == 'primes':
            return self._split_primes(weights)

        if self.simulation_type == 'monte_carlo':
            return self._split_monte_carlo(weights)

        # matrix: not splittable, run on a single worker as-is
        return [self]

    def _split_primes(self, weights: list[float]) -> list['SimulationTask']:
        """
        Split the prime-search range across workers based on to their free capacity.

        Ex with weights=[0.419, 0.581] and range 2->1_000_000:
            worker 0(1) gets range 2 -> 419_000
            worker 1(2) gets range 419_001 -> 1_000_000
        """
        total = self.primes_end_range - self.primes_start_range

        # convert weights to integer chunk sizes (must sum exactly to total)
        chunks = [int(w * total) for w in weights]
        chunks[-1] += total - sum(chunks)  # give leftover to the last worker

        subtasks = []
        curr = self.primes_start_range

        for idx, chunk in enumerate(chunks):
            start = curr
            end = start + chunk
            subtask = SimulationTask(
                simulation_type='primes',
                difficulty=self.task_difficulty * weights[idx],
                start_range=start,
                end_range=end,
            )
            subtask._mark_as_subtask(self.task_id, idx, weight=weights[idx])
            subtasks.append(subtask)
            curr = end

        return subtasks

    def _split_monte_carlo(self, weights: list[float]) -> list['SimulationTask']:
        """
        Split the arrow count across workers based on to their free capacity.
        """
        total = self.arrows_thrown

        chunks = [int(w * total) for w in weights]
        chunks[-1] += total - sum(chunks)  # leftover to worker, fix rounding.

        subtasks = []
        for idx, arrows in enumerate(chunks):
            subtask = SimulationTask(
                simulation_type='monte_carlo',
                difficulty=self.task_difficulty * weights[idx],
                num_samples=arrows,
            )
            subtask._mark_as_subtask(self.task_id, idx, weight=weights[idx])
            subtasks.append(subtask)

        return subtasks

    def merge_results(self, subtask_results: list[dict]) -> dict:
        if not subtask_results:
            raise ValueError("ERROR - merge_results: got empty list")

        sim_type = subtask_results[0]['type']

        if sim_type == 'primes':
            all_primes = []
            for r in subtask_results:
                all_primes.extend(r['primes'])
            all_primes.sort()
            return {
                'type': 'primes',
                'primes': all_primes,
                'count': len(all_primes),
                'range': (self.primes_start_range, self.primes_end_range),
            }

        if sim_type == 'monte_carlo':
            total_arrows = sum(r['arrows_thrown'] for r in subtask_results)
            weighted_pi = sum(
                r['pi_estimate'] * r['arrows_thrown'] for r in subtask_results
            ) / total_arrows
            return {
                'type': 'monte_carlo',
                'pi_estimate': weighted_pi,
                'arrows_thrown': total_arrows,
            }

        return subtask_results[0]  # for matrix since its not splittable...

    def to_dict(self) -> dict:
        """
        Serialize the SimulationTask to a plain dict for msgpack transport.
        """
        taskb_dict = {
            "type": "simulation",
            "task_id": self.task_id,
            "task_difficulty": self.task_difficulty,
            "status": self.status,
            "simulation_type": self.simulation_type,
            "result": self.result,
            # subtask fields
            "is_subtask": getattr(self, "is_subtask", False),
            "subtask_index": getattr(self, "subtask_index", None),
            "parent_task_id": getattr(self, "parent_task_id", None),
            "subtask_weight": getattr(self, "subtask_weight", None)
        }
        # find simulation type specific field - add them

        if self.simulation_type == "primes":
            taskb_dict["start_range"] = self.primes_start_range
            taskb_dict["end_range"] = self.primes_end_range

        elif self.simulation_type == "matrix":
            taskb_dict["matrix_size"] = self.matrix_size
            taskb_dict["iterations"] = self.round_matrix

        elif self.simulation_type == "monte_carlo":
            taskb_dict["num_samples"] = self.arrows_thrown

        else:
            raise ValueError(f"task type unknown")

        return taskb_dict

    @classmethod
    def from_dict(cls, data: dict) -> 'SimulationTask':
        """
        Reconstruct a SimulationTask from a plain dict received via msgpack.
        """
        simul_type = data["simulation_type"]

        # create object of task, and restore the actual task fields
        task = cls(
            simulation_type=simul_type,
            difficulty=data["task_difficulty"],
            start_range=data.get("start_range"),
            end_range=data.get("end_range"),
            matrix_size=data.get("matrix_size"),
            iterations=data.get("iterations"),
            num_samples=data.get("num_samples"),
        )

        # restore the base field of task
        task.task_id = data["task_id"]
        task.status = data["status"]
        task.result = data["result"]

        # restore subtask fields, if needed
        if data.get("is_subtask"):
            task.is_subtask = data["is_subtask"]
            task.subtask_index = data["subtask_index"]
            task.parent_task_id = data["parent_task_id"]
            task.subtask_weight = data.get("subtask_weight")

        return task
    
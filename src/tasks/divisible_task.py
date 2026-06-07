from abc import ABC, abstractmethod
from src.tasks.task import Task


class DivisibleTask(Task, ABC):
    """
    Abstract base class for tasks that can be divided into subtasks.
    """

    def __init__(self, task_difficulty: float):
        super().__init__(task_difficulty)
        self.is_subtask = False
        self.parent_task_id = None
        self.subtask_index = None
        self.subtask_weight: float | None = None

    def split_into_subtasks(self, worker_loads: list[float]) -> list:
        """
        The skeleton for splitting a task across workers.

        Subclasses only need to implement _split_by_weights with their task-specific logic.

        worker_loads: list of current CPU% for each available worker.
        """
        if len(worker_loads) <= 1:  # only 1 worker, cant subtask
            return [self]

        weights = self._compute_weights(worker_loads)
        return self._split_by_weights(weights)

    @abstractmethod
    def _split_by_weights(self, weights: list[float]) -> list:
        """
        Task-specific split logic. weights is already computed and normalized.
        Return [self] if this task type is not splittable.
        """
        pass

    @abstractmethod
    def merge_results(self, subtask_results: list[dict]) -> dict:
        """
        merge results from the subtask into a single result.
        """
        pass

    def _mark_as_subtask(self, parent_id: str, subtask_index: int, weight: float = None):
        """
        method for marking a task as a subtask.
        weight: this subtask share of the total work (0.0-1.0).
        """
        self.is_subtask = True
        self.parent_task_id = parent_id
        self.subtask_index = subtask_index
        self.subtask_weight = weight

    @staticmethod
    def _compute_weights(worker_loads: list[float]) -> list[float]:
        """
        Convert a list of CPU% values into work-share weights.

        Available capacity = 100 - cpu%, floored at 5 so even a fully loaded worker still get a small piece, 
        by doing this, it prevent divide by 0/1, handle edge case where all workers are at 100% use.

        Ex:
            worker_loads = [30.0, 3.0]
            capacities (free CPU%)   = [70.0, 97.0]
            weights      = [0.419, 0.581]  

        Returns a list of floats that sums to 1.0 (the 100% - full task).

        Note: weight are based on CPU% snapshot - not during task execution.
        """
        MIN_CAPACITY = 5.0
        capacities = [max(100.0 - cpu, MIN_CAPACITY) for cpu in worker_loads]
        total = sum(capacities)
        return [(capa / total) for capa in capacities]
    
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

    @abstractmethod
    def split_into_subtasks(self, worker_loads):
        pass

    @abstractmethod
    def merge_results(self, subtask_results: list[dict]) -> dict:
        """
        merge results from the subtask into a single result.
        """
        pass

    def _mark_as_subtask(self, parent_id: str, subtask_index): #inside functinon name is _ because it should not be called outside of the class
        """
        method for marking a task as a subtask.
        """
        self.is_subtask = True
        self.parent_task_id = parent_id
        self.subtask_index = subtask_index

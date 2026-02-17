from abc import ABC, abstractmethod
from .task import Task


class DivisibleTask(Task, ABC):
    """
    Abstract base class for tasks that can be divided into subtasks.
    """

    def __init__(self, task_difficulty : float):
        super().__init__(task_difficulty)
        self.is_subtask = False #flag to indicate if its subtask
        self.parent_task_id = None #refer to parent task id, if its subtask ofc

    @abstractmethod
    def split_into_subtasks(self, num_workers: int): #TD- understand what its returning
        pass 

    @abstractmethod
    def merge_results(self, subtask_results: list[dict]) -> dict:
        """
        merge results from the subtask into a single result.

        subtask_results is a list of a dict which contain the id: result of the tasks

        it will return a dict with the merged final result

        **this method is most likely to be called by the master computer after collecting all subtask results
        """

    def mention_subtask(self, parent_id : str, subtask_index : int):
        """
        method for marking a taks as a subtask, thats the only use of this method.
        """
        self.is_subtask = True
        self.parent_id = parent_id #ID of parent task
        self.subtask_index = subtask_index #index of this task in the parent subtask list.


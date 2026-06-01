import uuid
from abc import ABC, abstractmethod


class Task(ABC):
    """
    Abstract base class for all tasks in the system.

    every class must know how to:
    - execute itself (on worker side ofc)
    - serialize itself to dict (for msgpack transport)
    - deserialize itself from dict (for receiving)

    all the de/serialize will be done later.
    """

    def __init__(self, task_difficulty: float):
        self.task_id = str(uuid.uuid4())  # random id, 128bit
        self.task_difficulty = task_difficulty  # difficulty of the task, higher=harder
        self.status = "CREATED"  # status of the task: CREATED, RUNNING, DONE, FAILED
        self.result = None  # set by execute() on completion; None until then

    @abstractmethod
    def execute(self):
        pass

    @abstractmethod
    def to_dict(self) -> dict:
        """
        Serialize task to a plain dict, used for the msgpack transport.
        """
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict) -> "Task":
        """
        Reconstruct task from a plain dict received via msgpack.
        """
        pass
    
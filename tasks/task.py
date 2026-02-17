import uuid
from abc import ABC, abstractmethod


class Task(ABC):
    """
    Abstract base class for all tasks in the system.

    every class must know how to:
    - execute itself (on worker side ofc)
    - serialize itself to bytes (for network)
    - deserialize itself from bytes (for receiving)

    all the de/serialize will be done later.
    """

    def __init__(self, task_difficulty : float):
        self.task_id = str(uuid.uuid4()) #random id 128bit
        self.task_difficulty = task_difficulty #difficulty of the task, higher=harder
        self.status = "CREATED" #status of the task: CREATED, RUNNING, DONE, FAILD

    @abstractmethod
    def execute(self):
        pass

    @abstractmethod
    def to_bytes(self) -> bytes:
        pass

    @classmethod
    @abstractmethod
    def from_bytes(cls, data : bytes) -> "Task":
        pass
    

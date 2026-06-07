import asyncio
import heapq
import logging
import time
import uuid

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Optional

class WorkerStatus(Enum):
    """
    Represents the current lifecycle state of a worker in the load balancer system.

    States:
        READY - Worker is idle and available for new tasks.
        BUSY - Worker is actively executing a task.
        OVERLOADED - Worker is under high CPU pressure and should be avoided to be given tasks.
        OFFLINE - Worker is disconnected or not renspoding.
    """
    READY      = auto()
    BUSY       = auto()
    OVERLOADED = auto()
    OFFLINE    = auto()


class TaskStatus(Enum):
    """
    Represents the execution state of a task inside the load balancer.

    States:
        PENDING - Task is waiting in the queue.
        ASSIGNED - Task has been assigned to a worker but not started yet.
        RUNNING - Task is currently executing on a worker.
        DONE - Task completed successfully.
        FAILED - Task failed permanently after retries.
        REQUEUED - Task failed temporarily and was placed back in the queue.
    """
    PENDING  = auto()
    ASSIGNED = auto()
    RUNNING  = auto()
    DONE     = auto()
    FAILED   = auto()
    REQUEUED = auto()


class TaskPriority(int, Enum):
    """
    Priority levels for tasks in the system.

    Lower number = higher priority.

    Levels:
        CRITICAL - Highest priority, must be executed first.
        HIGH - Important tasks with elevated priority.
        NORMAL - Default task priority.
        LOW - Background or non-urgent tasks.
    """
    CRITICAL = 1
    HIGH     = 2
    NORMAL   = 3
    LOW      = 4


DIFFICULTY_CAPACITY_SCALE = 6.0  # Scale factor for converting task difficulty to capacity units


@dataclass
class WorkerState:
    """
    Represents a single worker managed by the LoadBalancer.

    This class tracks the runtime state, performance metrics, and availability
    of a worker participating in distributed task execution.

    Load model:
        load_score = cpu_usage + (20 if BUSY else 0)

    A worker is considered available when:
        It is in READY or BUSY state
        CPU usage is below 90%
    """
    worker_id: str  # unique id for the worker.
    address: tuple[str, int]  # host, port.

    status: WorkerStatus = WorkerStatus.READY  # current status of worker, defult as READY.
    cpu_usage: float = 0.0

    # set of task_ids currently assigned to this worker (includes subtasks), created new set for each instance to avoid shared mutable default.
    active_task_ids: set = field(default_factory=set)

    completed_tasks: int = 0  # number of tasks completed by this worker.
    failed_tasks: int = 0  # number of tasks failed by this worker.

    # start time just when instance is created.
    last_heartbeat_time: float = field(default_factory=time.monotonic)  # time of last heartbeat received by worker.
    connected_at: float = field(default_factory=time.monotonic)  # time when worker was first registered.

    @property
    def load_score(self):
        """
        Return how aviable is worker, chekcs by adding his current cpu use + 0(if READY)/ +20(if BUSY).
        """
        if_busy = 20.0 if self.status == WorkerStatus.BUSY else 0.0
        return self.cpu_usage + if_busy

    @property
    def free_capacity(self):
        """
        Return how free is worker, 100 - load_score, capped at 100, min 0.
        """
        return max(0.0, 100.0 - self.load_score)

    @property
    def is_available(self):
        """
        Return if worker is available for new tasks, checks if status is READY or BUSY and cpu usage is under 90%.
        """
        return self.status in (WorkerStatus.READY, WorkerStatus.BUSY) and self.cpu_usage < 90.0

    @property
    def uptime(self):
        """
        Return how long the worker has been connected.
        """
        return time.monotonic() - self.connected_at


@dataclass(order=True)
class TaskRecord:
    """
    Dataclass present a single task manged by LB.
    
    TaskRecord is the object stored inside the priority queue (heapq).

    The data class use order=True so TaskRecord can be compared and auto stored inside the heapq.
    Ordering based on:
        1. priority 
        2. enqueue_time inside same priority level (FIFO).
    
    This creates a priority queue with FIFO behavior inside the same priority level.

    Fields marked with compare=False since they shouldnt take part in the ordering - have no affect on queue sorting.

    Failed tasks can try resty up to 3 times untill they count as a fail.
    """
    # ordering fields for priority queue
    priority: int  # task priority.
    enqueue_time: float = field(compare=True, default_factory=time.monotonic)  # time when task was enqueued, used for FIFO ordering within same priority level.

    # non ordering fields
    task_id: str = field(compare=False, default_factory=lambda: str(uuid.uuid4()))  # create unique id for each task instance.
    payload: Any = field(compare=False, default=None)  # the actual task paylaod.
    difficulty: float = field(compare=False, default=1.0)  # difficulty score for the task.
    status: TaskStatus = field(compare=False, default=TaskStatus.PENDING)  # task status, set defult as PENDING when created.

    assigned_worker: Optional[str] = field(compare=False, default=None)  # worker_id of assigned worker, None if not assigned yet or after failure.

    # Task retry: if task fails, it can be retried up to 3 (max_retries) times before being marked as permanently failed.
    retries: int = field(compare=False, default=0)  # task retry count, set defult as 0 when created.
    max_retries: int = field(compare=False, default=3)  # max times to retry before permanent failure, set defult as 3 when created.

    # Number of times this task was skipped during dispatch attempts, used to trigger forced dispatch if too many skips occur.
    skipped_count: int = field(compare=False, default=0)

    # Task result or error message after completion.
    result: Any = field(compare=False, default=None)  # result of the task after completion, None if not completed or failed.
    error: Optional[str] = field(compare=False, default=None)  # error message if task failed, None if not failed.

    started_at: Optional[float] = field(compare=False, default=None)  # time when task was started on a worker, None if not started yet.
    task_finish_time: Optional[float] = field(compare=False, default=None)  # time when task was finished (either success or failure), None if not finished yet.

    @property
    def min_free_capacity(self):
        """
        Return the minimum free capacity a worker must have to be assigned this task, based on its difficulty * 8, capped at 95 to avoid unassignable too hard tasks.
        """
        return min(self.difficulty * DIFFICULTY_CAPACITY_SCALE, 95.0)

    @property
    def elapsed(self):
        """
        Return how long the task has been running since it was started, or total time if finished. None if not started yet.
        """
        if self.started_at and self.task_finish_time:
            return self.task_finish_time - self.started_at

        if self.started_at:
            return time.monotonic() - self.started_at

        return None


@dataclass
class SubtaskGroup:
    """
    Tracks a single split task that was divided into N subtasks.

    Part of LoadBalancer._subtask_groups[parent_task_id].

    When all subtask results arrive, the LB merges them and fires on_task_done with the parent TaskRecord (carrying the merged result).
    """
    parent_record: "TaskRecord"  # parent task object - TaskRecord.
    original_payload: Any  # task original paylaod before split.

    total_subtasks: int  # total subtask of this task.

    
    results: dict = field(default_factory=dict)  # dict of results for subtasks, {subtask_index: result_dict}, new dict for each instance.

    failed: int = field(default=0)  # number of subtasks failed.
    
    @property
    def done(self) -> bool:
        """
        Return True if all subtasks have reported a result (either success or fail) and the parent task can be finalised.
        """
        return (len(self.results) + self.failed) >= self.total_subtasks

    @property
    def all_succeeded(self) -> bool:
        """
        Return True if all subtasks successesed. 
        """
        return len(self.results) >= self.total_subtasks and self.failed == 0


@dataclass
class LBMetrics:
    """
    Snapshot of system load balancer metrics at a given time.

    Used for monitoring, analytics, and makeing decisions (auto), and output it in UI.
    """
    timestamp: float  # timestamp for when these metrics were recorded.

    total_workers: int  # total worker rn in the system.

    ready_workers: int  # num of ready workers.
    busy_workers: int  # num of busy worker.
    offline_workers: int  # num of unconnected (offline) workers.

    queue_size: int  # num tasks currently waiting in the queue.

    # tasks completed and failed since LB start.
    tasks_completed: int
    tasks_failed: int

    avg_cpu: float # average CPU usage across all active workers.
    throughput_per_min: float # number of tasks completed in the last.


class LoadBalancer:
    """
    The conductor coordinating and distributing tasks across connected workers.

    The LoadBalancer sits between the server and the workers, the server submits tasks to it, and it decides when and where to send them, it is used by master side only.
    It runs three continuous async loops: 
        - dispatch loop that pulls tasks from the priority queue and matches them to available workers
        - heartbeat loop that detects and handles worker disconnections
        - metrics loop that snapshots the system state for the UI every few seconds

    Tasks are stored in a heapq - heap with priorities, that ordered by priority and enqueue time,
    so critical tasks always dispatch first with a FIFO happening in the same priority level.
    If task cant match a worker, the task is skipped - but after MAX_SKIPS skips it is force-dispatched to prevent queue blocking.

    For divisible tasks (render, primes, monte carlo), LB coordinates splitting them across multi workers based on each worker's free CPU capacity;
    LB tracks all subtasks of task under a SubtaskGroup and automatically merge their results once they all done.

    Worker CPU usage is updated via heartbeat packets sent by each worker every few seconds.
    A worker that goes silent for HEARTBEAT_TIMEOUT seconds is marked OFFLINE,
    all tasks it was running are recovered back to the heapq via self.tasks (keeps every task alive in dict memory until completion).

    The LB communicates with callbacks:
        - send_task_cb - to actually send a task over the network
        - kick_worker_cb - to forcibly disconnect a worker
        - event lists (on_task_done, on_worker_change_cbs, on_metrics_cbs) - for giving server and UI real-time updates about LB responsibilities.
    """
    HEARTBEAT_TIMEOUT = 15.0
    DISPATCH_INTERVAL = 0.2
    METRICS_INTERVAL = 2.0
    MAX_SKIPS = 10
    MIN_WORKERS_TO_SPLIT = 2  # only split if there are at least 2 workers availabvle.

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.log = logger or logging.getLogger("LoadBalancer")

        # workers
        self.workers: dict[str, WorkerState] = {}
        self.workers_lock = asyncio.Lock()

        # task queue
        self.queue: list[TaskRecord] = []
        self.queue_lock = asyncio.Lock()

        # all tasks
        self.tasks: dict[str, TaskRecord] = {}

        # subtask tracking
        self.subtask_groups: dict[str, SubtaskGroup] = {} #{parent_task_id: SubtaskGroup}
        self.subtask_to_parent: dict[str, tuple[str, int]] = {} #{subtask_payload_uuid: (parent_task_id, subtask_index)}

        # callbacks
        self.send_task_cb: Optional[
            Callable[[str, TaskRecord], Coroutine]
        ] = None

        self.kick_worker_cb: Optional[
            Callable[[str], Coroutine]
        ] = None

        # event handlers
        self.on_worker_change_cbs: list[Callable[[WorkerState], None]] = [] 
        self.on_task_done: list[Callable[[TaskRecord], None]] = []
        self.on_metrics_cbs: list[Callable[[LBMetrics], None]] = []

        # internal state
        self.running = False

        self.tasks_completed = 0
        self.tasks_failed = 0

        self.completion_times: list[float] = []

    async def start(self):
        """
        Main loop of LB runs the 3 main event loops.
        """
        self.running = True

        self.log.info("LoadBalancer starting")

        await asyncio.gather(
            self._dispatch_loop(),
            self._heartbeat_loop(),
            self._metrics_loop(),
        )

    async def stop(self):
        self.running = False
        self.log.info("LoadBalancer stopping")

    # ---------------worker stuff------------------
    async def register_worker(self, worker_id: str, host: str, port: int):
        """
        Add worker to the system.
        """
        async with self.workers_lock:
            if worker_id in self.workers:
                self.log.warning("Worker %s already exists, refreshing", worker_id,)

                worker = self.workers[worker_id]
                worker.status = WorkerStatus.READY
                worker.last_heartbeat_time = time.monotonic()
                return

            worker = WorkerState(
                worker_id=worker_id,
                address=(host, port),
            )

            self.workers[worker_id] = worker

            self.log.info("Worker registered: %s @ %s:%d", worker_id, host, port)

            self._fire_worker_change(worker)

    async def unregister_worker(self, worker_id: str):
        """
        Remove worker from the system.
        """
        async with self.workers_lock:
            worker = self.workers.pop(worker_id, None)

        if worker:
            self.log.info("Worker unregistered: %s", worker_id)
            await self._handle_worker_failure(worker_id, worker)

    async def update_worker_stats(self, worker_id: str, cpu: float, status: Optional[WorkerStatus] = None):
        """
        Update the worker status.
        """
        async with self.workers_lock:
            worker = self.workers.get(worker_id)
            if not worker:
                self.log.warning( "Stats update for unknown worker %s",worker_id)
                return

            worker.cpu_usage = cpu
            worker.last_heartbeat_time = time.monotonic()

            if status is not None:
                worker.status = status

            if worker.cpu_usage >= 90.0:
                if worker.status != WorkerStatus.OVERLOADED:
                    worker.status = WorkerStatus.OVERLOADED
                    self.log.warning( "Worker %s overloaded (cpu=%.1f%%)", worker_id, cpu)

            elif worker.cpu_usage < 80.0:
                #update when worker isnt overloaded anymore.
                if worker.status == WorkerStatus.OVERLOADED:
                    if worker.active_task_ids:
                        worker.status = WorkerStatus.BUSY
                    else:
                        worker.status = WorkerStatus.READY

            self._fire_worker_change(worker)

    # -----------task stuff----------
    async def submit_task(self, payload: Any, priority: TaskPriority = TaskPriority.NORMAL, difficulty: float = 1.0, max_retries: int = 3,):
        """
        Add task to heapq.
        """
        task = TaskRecord(
            priority=priority.value,
            payload=payload,
            difficulty=difficulty,
            max_retries=max_retries,
        )

        async with self.queue_lock:
            heapq.heappush(self.queue, task)
            self.tasks[task.task_id] = task

        self.log.debug("Task %s added to queue", task.task_id)
        return task.task_id

    async def task_completed(self, worker_id: str, task_id: str, result: Any):
        """
        Handle task completion. 
        """
        task = self.tasks.get(task_id)
        if not task:
            self.log.error("Result for unknown task %s from %s", task_id, worker_id)
            return

        task.status = TaskStatus.DONE
        task.result = result
        task.task_finish_time = time.monotonic()

        self.tasks_completed += 1
        self.completion_times.append(time.monotonic())

        async with self.workers_lock:
            worker = self.workers.get(worker_id)
            if worker:
                worker.active_task_ids.discard(task_id)
                worker.completed_tasks += 1
                if not worker.active_task_ids:
                    worker.status = WorkerStatus.READY

        self.log.info("Task %s done by %s (%.2fs)", task_id, worker_id, task.elapsed or 0)
        self._fire_task_done(task)

    async def subtask_completed(self, worker_id: str, subtask_payload_uuid: str, subtask_index: int, result: Any):
        """
        Called by the server when a subtask_result packet arrives.

        Puhs results into the SubtaskGroup. When all subtasks finish,
        merges with the original payload's merge_results() and fires on_task_done on the parent TaskRecord with the merged result.
        """
        # look up which parent this subtask belongs to
        parent_info = self.subtask_to_parent.pop(subtask_payload_uuid, None)
        if parent_info is None:
            self.log.error("subtask_completed: no parent mapping for uuid %.8s", subtask_payload_uuid)
            return 

        parent_task_id, idx = parent_info   # parent_task_id = parent_task_id, idx = subtask_index
        group = self.subtask_groups.get(parent_task_id)  # instance of SubtaskGroup with this parent_task_id
        if group is None:
            self.log.error("subtask_completed: no SubtaskGroup for parent %.8s", parent_task_id)
            return

        # free the worker slot for this subtask
        async with self.workers_lock:
            worker = self.workers.get(worker_id)
            if worker:
                worker.active_task_ids.discard(subtask_payload_uuid)
                worker.completed_tasks += 1
                if not worker.active_task_ids:
                    worker.status = WorkerStatus.READY

        group.results[idx] = result  #upadate the group results.
        self.log.info("Subtask %d/%d done (parent=%.8s) by %s", idx + 1, group.total_subtasks, parent_task_id, worker_id)

        if not group.done:
            return  # still waiting for other subtasks

        # all subtasks accounted for — merge and finalise
        parent_record = group.parent_record
        parent_record.task_finish_time = time.monotonic()
        del self.subtask_groups[parent_task_id]  # cleanip

        if group.all_succeeded:
            try:
                sorted_results = [group.results[i] for i in range(group.total_subtasks)]
                merged = group.original_payload.merge_results(sorted_results)
            except Exception as exc:
                self.log.error("merge_results failed for parent %.8s: %s", parent_task_id, exc)
                parent_record.status = TaskStatus.FAILED
                parent_record.error = f"merge_failed: {exc}"
                self.tasks_failed += 1
                self._fire_task_done(parent_record)
                return 

            parent_record.status = TaskStatus.DONE
            parent_record.result = merged
            self.tasks_completed += 1
            self.completion_times.append(time.monotonic())
            self.log.info("Parent task %.8s fully merged (%d subtasks)", parent_task_id, group.total_subtasks)
        else:
            parent_record.status = TaskStatus.FAILED
            parent_record.error = f"{group.failed}/{group.total_subtasks} subtasks failed"
            self.tasks_failed += 1
            self.log.error("Parent %.8s failed — %d subtask(s) failed", parent_task_id, group.failed)

        self._fire_task_done(parent_record)

    async def subtask_failed(self, worker_id: str, subtask_payload_uuid: str, subtask_index: int, error: str):
        """
        Called by the server when a subtask result arrives with a failed status.
        Marks the slot as failed in the group; when all slots are filled, fires on_task_done.

        If subtask failed - all task failed.
        """
        parent_info = self.subtask_to_parent.pop(subtask_payload_uuid, None)
        if parent_info is None:
            self.log.error("subtask_failed: no parent mapping for uuid %.8s", subtask_payload_uuid)
            return

        parent_task_id, idx = parent_info
        group = self.subtask_groups.get(parent_task_id)
        if group is None:
            return

        async with self.workers_lock:
            worker = self.workers.get(worker_id)
            if worker:
                worker.active_task_ids.discard(subtask_payload_uuid)
                worker.failed_tasks += 1
                if not worker.active_task_ids:
                    worker.status = WorkerStatus.READY

        group.failed += 1
        self.log.warning("Subtask %d/%d FAILED (parent=%.8s): %s", idx + 1, group.total_subtasks, parent_task_id, error)

        if not group.done:
            return

        # all slots done (some failed) — finalise parent as failed
        parent_record = group.parent_record
        parent_record.task_finish_time = time.monotonic()
        parent_record.status = TaskStatus.FAILED
        parent_record.error = f"{group.failed}/{group.total_subtasks} subtasks failed"
        self.tasks_failed += 1
        del self.subtask_groups[parent_task_id]  # cleanup
        self._fire_task_done(parent_record) 

    async def task_failed(self, worker_id: str, task_id: str, error: str):
        """
        Handle task faliure, add 1 to the retries value.

        If tasks_failed > 3 count it as FAILED task.
        """
        task = self.tasks.get(task_id)

        if not task:
            return

        task.error = error
        task.retries += 1

        async with self.workers_lock:
            worker = self.workers.get(worker_id)
            if worker:
                worker.active_task_ids.discard(task_id)
                worker.failed_tasks += 1
                if not worker.active_task_ids:
                    worker.status = WorkerStatus.READY

        if task.retries <= task.max_retries:
            task.status = TaskStatus.REQUEUED
            task.assigned_worker = None
            task.started_at = None

            self.log.warning("Task %s failed (%d/%d), requeueing. Error: %s", task_id, task.retries, task.max_retries, error)

            async with self.queue_lock:
                heapq.heappush(self.queue, task)
        else:
            task.status = TaskStatus.FAILED
            task.task_finish_time = time.monotonic()
            self.tasks_failed += 1
            self.log.error("Task %s permanently failed after %d retries", task_id, task.retries)
            self._fire_task_done(task)

    async def _dispatch_loop(self):
        """
        Loop of dispatching tasks, happens every 0.2seconds.
        """
        self.log.debug("Dispatch loop started")

        while self.running:
            await self._try_dispatch()
            await asyncio.sleep(self.DISPATCH_INTERVAL)

    async def _try_dispatch(self):
        """
        Sending the tasks, look at the first 3 tasks in heapq, match them to worker.
        
        If tasks can be dispatched, skip it 10 times, if it been skiped more than 10 times - force dispatch.
        """
        async with self.queue_lock:
            if not self.queue:
                return

            tasks = heapq.nsmallest(3, self.queue)  # sort the the first 3 in queue will be smallest, prob the best- O(n log3)
        task = None
        worker = None

        for candidate in tasks[:3]:
            #match worker for task
            found_worker = await self._select_worker_for_task(candidate)

            if found_worker:
                task = candidate
                worker = found_worker
                break

        if task is None:
            #no match
            async with self.queue_lock:
                for candidate in tasks[:3]:
                    candidate.skipped_count += 1

                    if candidate.skipped_count >= self.MAX_SKIPS:
                        forced_worker = await self._select_worker_any()

                        if forced_worker:
                            task = candidate
                            worker = forced_worker

                            self.log.warning("Force to dispatch task %s after too many skips", task.task_id,)

                            break

            if task is None:
                return

        async with self.queue_lock:
            try:
                self.queue.remove(task)
                heapq.heapify(self.queue)

            except ValueError:
                return

        task.status = TaskStatus.ASSIGNED
        task.assigned_worker = worker.worker_id
        task.started_at = time.monotonic()

        async with self.workers_lock:
            current_worker = self.workers.get(worker.worker_id)

            if current_worker:
                current_worker.active_task_ids.add(task.task_id)
                current_worker.status = WorkerStatus.BUSY

        self.log.info("Dispatching task %s -> worker %s", task.task_id, worker.worker_id)

        if self.send_task_cb:
            success = await self.send_task_cb(worker.worker_id, task)

            if not success:
                self.log.warning("Send failed for task %s",task.task_id)

                task.status = TaskStatus.REQUEUED
                task.assigned_worker = None
                task.started_at = None

                async with self.workers_lock:
                    current_worker = self.workers.get(worker.worker_id)

                    if current_worker:
                        current_worker.active_task_ids.discard(task.task_id)
                        current_worker.status = WorkerStatus.READY

                async with self.queue_lock:
                    heapq.heappush(self.queue, task)

        else:
            task.status = TaskStatus.PENDING
            task.assigned_worker = None

            async with self.queue_lock:
                heapq.heappush(self.queue, task)

    async def _select_worker_for_task(self, task: TaskRecord):
        """
        Match a worker to task, match is made based on worker availablety and his cpu% (always choose the freeiest worker).
        """
        async with self.workers_lock:
            workers = [worker for worker in self.workers.values() if worker.is_available and worker.free_capacity >= task.min_free_capacity]

        if not workers:
            return None

        return min(workers, key=lambda w: w.load_score)  # return the most free worker

    async def _select_worker_any(self):
        """
        Match a worker to task, called when task must be force dispatched, then the matching is stupid - match to whoever can handle.
        """
        async with self.workers_lock:
            workers = [worker for worker in self.workers.values() if worker.is_available]  # search for a WorkerState that is available.

        if not workers:
            return None

        return min(workers, key=lambda w: w.load_score) # return the most free worker

    async def _heartbeat_loop(self):
        """
        A 7.5 seconds loop, checking if worker is still connected and alive - if make him OFFLINE.
        """
        self.log.debug("Heartbeat loop started")

        while self.running:
            await asyncio.sleep(self.HEARTBEAT_TIMEOUT / 2)

            time_now = time.monotonic()
            timed_out_workers = []

            async with self.workers_lock:
                for worker_id, worker in self.workers.items():
                    if worker.status == WorkerStatus.OFFLINE:
                        continue

                    if time_now - worker.last_heartbeat_time > self.HEARTBEAT_TIMEOUT:
                        timed_out_workers.append((worker_id, worker))

            for worker_id, worker in timed_out_workers:
                self.log.error("Worker %s heartbeat timeout", worker_id)

                worker.status = WorkerStatus.OFFLINE

                self._fire_worker_change(worker)

                await self._handle_worker_failure(worker_id, worker)

    async def _handle_worker_failure(self, worker_id: str, worker: WorkerState):
        """
        Handle any task that was running on the failed worker.
        """
        for task_id in list(worker.active_task_ids):
            if task_id in self.subtask_to_parent:
                continue  # handled below in the subtask block
            task = self.tasks.get(task_id)
            if task and task.status in (TaskStatus.ASSIGNED, TaskStatus.RUNNING):
                self.log.warning("Requeueing task %s after worker failure", task_id)
                await self.task_failed(worker_id, task_id, "worker_offline")

        # handle any subtasks that were assigned to this worker
        for subtask_uuid in list(worker.active_task_ids):
            parent_info = self.subtask_to_parent.get(subtask_uuid)
            if parent_info:
                parent_task_id, idx = parent_info
                self.log.warning("Subtask %.8s (parent %.8s idx=%d) lost — worker %s offline", subtask_uuid, parent_task_id, idx, worker_id)
                await self.subtask_failed(worker_id, subtask_uuid, idx, "worker_offline")

    def register_subtask_group(self, parent_record: "TaskRecord", original_payload: Any, subtask_uuids: list[tuple[str, int]]):
        """
        Called by the server immediately after splitting a DivisibleTask and register the SubtaskGroup so the LB can know.

        subtask_uuids: list of (payload uuid, subtask_index) for each dispatched subtask.
        """
        group = SubtaskGroup(
            parent_record=parent_record,
            original_payload=original_payload,
            total_subtasks=len(subtask_uuids),
        )
        self.subtask_groups[parent_record.task_id] = group

        for uuid, idx in subtask_uuids:
            self.subtask_to_parent[uuid] = (parent_record.task_id, idx)

        self.log.info("SubtaskGroup registered: parent=%.8s  subtasks=%d", parent_record.task_id, len(subtask_uuids))

    async def _metrics_loop(self):
        """
        A loop happening every 2 sec, for updating the UI.
        """
        self.log.debug("Metrics loop started")

        while self.running:
            await asyncio.sleep(self.METRICS_INTERVAL)

            metrics = await self._compute_metrics()

            for callback in self.on_metrics_cbs:
                try:
                    callback(metrics)

                except Exception as e:
                    self.log.warning("Metrics callback error: %s",e)

    async def _compute_metrics(self):
        """
        Create the snapshot of the system.
        """
        now = time.monotonic()

        async with self.workers_lock:
            workers = list(self.workers.values())
        #count how many workers are:
        ready = sum(1 for w in workers if w.status == WorkerStatus.READY)
        busy = sum(1 for w in workers if w.status == WorkerStatus.BUSY)
        off = sum(1 for w in workers if w.status == WorkerStatus.OFFLINE)

        cpus = [w.cpu_usage for w in workers if w.status != WorkerStatus.OFFLINE]

        cutoff = now - 60.0

        self.completion_times = [
            t for t in self.completion_times
            if t > cutoff
        ]

        throughput = len(self.completion_times)

        async with self.queue_lock:
            q_size = len(self.queue)

        return LBMetrics(
            timestamp=now,
            total_workers=len(workers),
            ready_workers=ready,
            busy_workers=busy,
            offline_workers=off,
            queue_size=q_size,
            tasks_completed=self.tasks_completed,
            tasks_failed=self.tasks_failed,
            avg_cpu=sum(cpus) / len(cpus) if cpus else 0.0,
            throughput_per_min=throughput,
        )

    def set_send_callback(self, cb):
        # set callback
        self.send_task_cb = cb

    def set_kick_callback(self, cb):
        # set callback
        self.kick_worker_cb = cb

    async def kick_worker(self, worker_id: str, reason: str = "normal-manual"):
        """
        Kick worker (not unregister) - used by user (UI).
        """
        self.log.warning("Kicking worker %s (reason: %s)", worker_id, reason)

        if self.kick_worker_cb:
            await self.kick_worker_cb(worker_id)

        await self.unregister_worker(worker_id)

    def on_worker_status_change(self, cb):
        # add cb to on_worker_change_cbs list - update.
        self.on_worker_change_cbs.append(cb)

    def on_task_done_register(self, cb):
        # add cb to on_task_done_register list - update.
        self.on_task_done.append(cb)

    def on_metrics_update(self, cb):
        # add cb to on_metrics_cbs list - update.
        self.on_metrics_cbs.append(cb)

    def _fire_worker_change(self, worker):
        for callback in self.on_worker_change_cbs:
            try:
                callback(worker)

            except Exception as e:
                self.log.warning("Worker callback error: %s", e)

    def _fire_task_done(self, task):
        for callback in self.on_task_done:
            try:
                callback(task)

            except Exception as e:
                self.log.warning(
                    "Task callback error: %s",
                    e,
                )

    async def get_snapshot(self):
        """
        Total snapshot of the system - workers, queue, stats.

        Used by task_pool to know if/how to create tasks.
        """
        async with self.workers_lock:
            workers_snapshot = {
                worker_id: {
                    "status": worker.status.name,
                    "cpu": worker.cpu_usage,
                    "active_tasks": list(worker.active_task_ids),
                    "completed": worker.completed_tasks,
                    "failed": worker.failed_tasks,
                    "uptime": round(worker.uptime, 1),
                    "load_score": round(worker.load_score, 1),
                }
                for worker_id, worker in self.workers.items()
            }

        async with self.queue_lock:
            queue_snapshot = [
                {
                    "task_id": task.task_id,
                    "priority": TaskPriority(task.priority).name,
                    "status": task.status.name,
                    "retries": task.retries,
                }
                for task in sorted(self.queue)
            ]

        return {
            "workers": workers_snapshot,
            "queue": queue_snapshot,
            "stats": { "completed": self.tasks_completed, "failed": self.tasks_failed,}
        }
    
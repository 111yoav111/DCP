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
        DRAINING - Worker is shutting down and should not receive new tasks.
    """
    READY      = auto()
    BUSY       = auto()
    OVERLOADED = auto()
    OFFLINE    = auto()
    DRAINING   = auto()


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


DIFFICULTY_CAPACITY_SCALE = 8.0


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
    worker_id: str
    address: tuple[str, int]

    status: WorkerStatus = WorkerStatus.READY
    cpu_usage: float = 0.0

    active_task_id: Optional[str] = None

    completed_tasks: int = 0
    failed_tasks: int = 0

    last_heartbeat_time: float = field(default_factory=time.monotonic) #start time just when instance is created.
    connected_at: float = field(default_factory=time.monotonic) #^

    @property
    def load_score(self):
        #how aviable is the worker, chekcs by adding his current cpu use + 0(if READY)/ +20(if BUSY)
        task_penalty = 20.0 if self.status == WorkerStatus.BUSY else 0.0
        return self.cpu_usage + task_penalty

    @property
    def free_capacity(self):
        return max(0.0, 100.0 - self.load_score)

    @property
    def is_available(self):
        return self.status in (WorkerStatus.READY, WorkerStatus.BUSY) and self.cpu_usage < 90.0

    @property
    def uptime(self):
        return time.monotonic() - self.connected_at


@dataclass(order=True)
class TaskRecord:
    """
    Dataclass present a single task manged by LB.
    
    TaskRecord is the object stored inside the priority queue (heapq).

    The data class use order=True so TaskRecord can be compared and auto stored inside the heapq.
    Ordering based on:
        1. priority 
        2. enqueue_time
    
    This creates a priority queue with FIFO behavior inside the same priority level.

    Fields marked with compare=False since they shouldnt take part in the ordering - have no affect on queue sorting.

    Failed tasks can try resty up to 3 times untill they count as a fail.
    """
    priority: int
    enqueue_time: float = field(compare=True, default_factory=time.monotonic)

    task_id: str = field(compare=False, default_factory=lambda: str(uuid.uuid4()))
    payload: Any = field(compare=False, default=None)
    difficulty: float = field(compare=False, default=1.0)
    status: TaskStatus = field(compare=False, default=TaskStatus.PENDING)

    assigned_worker: Optional[str] = field(compare=False, default=None)
    retries: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    skipped_count: int = field(compare=False, default=0)
    result: Any = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    started_at: Optional[float] = field(compare=False, default=None)
    task_finish_time: Optional[float] = field(compare=False, default=None)

    @property
    def min_free_capacity(self):
        return min(self.difficulty * DIFFICULTY_CAPACITY_SCALE, 95.0)

    @property
    def elapsed(self):
        if self.started_at and self.task_finish_time:
            return self.task_finish_time - self.started_at

        if self.started_at:
            return time.monotonic() - self.started_at

        return None


@dataclass
class LBMetrics:
    """
    Snapshot of system-wide load balancer metrics at a given timestamp.

    Used for monitoring, analytics, and autoscaling decisions, and output it in UI.
    """
    timestamp: float
    total_workers: int
    ready_workers: int
    busy_workers: int
    offline_workers: int
    queue_size: int
    tasks_completed: int
    tasks_failed: int
    avg_cpu: float
    throughput_per_min: float


class LoadBalancer:
    # main load balancer class

    HEARTBEAT_TIMEOUT = 15.0
    DISPATCH_INTERVAL = 0.2
    METRICS_INTERVAL = 2.0

    MAX_SKIPS = 10

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

        # callbacks
        self.send_task_cb: Optional[
            Callable[[str, TaskRecord], Coroutine]
        ] = None

        self._kick_worker_cb: Optional[
            Callable[[str], Coroutine]
        ] = None

        # event handlers
        self.on_worker_chage: list[Callable[[WorkerState], None]] = []
        self.on_task_done: list[Callable[[TaskRecord], None]] = []
        self.on_metrics: list[Callable[[LBMetrics], None]] = []

        # internal state
        self.running = False

        self._tasks_completed = 0
        self._tasks_failed = 0

        self._completion_times: list[float] = []

    async def start(self):
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
        async with self.workers_lock:
            if worker_id in self.workers:
                self.log.warning(
                    "Worker %s already exists, refreshing",
                    worker_id,
                )

                worker = self.workers[worker_id]
                worker.status = WorkerStatus.READY
                worker.last_heartbeat_time = time.monotonic()
                return

            worker = WorkerState(
                worker_id=worker_id,
                address=(host, port),
            )

            self.workers[worker_id] = worker

            self.log.info(
                "Worker registered: %s @ %s:%d",
                worker_id,
                host,
                port,
            )

            self._fire_worker_change(worker)

    async def unregister_worker(self, worker_id: str):
        async with self.workers_lock:
            worker = self.workers.pop(worker_id, None)

        if worker:
            self.log.info("Worker unregistered: %s", worker_id)
            await self._handle_worker_failure(worker_id, worker)

    async def update_worker_stats(self, worker_id: str, cpu: float, status: Optional[WorkerStatus] = None):
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
                    if worker.active_task_id:
                        worker.status = WorkerStatus.BUSY
                    else:
                        worker.status = WorkerStatus.READY

            self._fire_worker_change(worker)

    # -----------task stuff----------
    async def submit_task(self, payload: Any, priority: TaskPriority = TaskPriority.NORMAL, difficulty: float = 1.0, max_retries: int = 3,):
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
        task = self.tasks.get(task_id)
        if not task:
            self.log.error("Result for unknown task %s from %s", task_id, worker_id,)
            return

        task.status = TaskStatus.DONE
        task.result = result
        task.task_finish_time = time.monotonic()

        self._tasks_completed += 1
        self._completion_times.append(time.monotonic())

        async with self.workers_lock:
            worker = self.workers.get(worker_id)

            if worker:
                worker.active_task_id = None
                worker.completed_tasks += 1
                worker.status = WorkerStatus.READY

        self.log.info("Task %s done by %s (%.2fs)", task_id, worker_id, task.elapsed or 0,)

        self._fire_task_done(task)

    async def task_failed(self, worker_id: str, task_id: str, error: str):
        task = self.tasks.get(task_id)

        if not task:
            return

        task.error = error
        task.retries += 1

        async with self.workers_lock:
            worker = self.workers.get(worker_id)

            if worker:
                worker.active_task_id = None
                worker.failed_tasks += 1
                worker.status = WorkerStatus.READY

        if task.retries <= task.max_retries:
            #try again
            task.status = TaskStatus.REQUEUED
            task.assigned_worker = None
            task.started_at = None

            self.log.warning("Task %s failed (%d/%d), requeueing. Error: %s", task_id, task.retries, task.max_retries, error)

            async with self.queue_lock:
                heapq.heappush(self.queue, task)

        else:
            task.status = TaskStatus.FAILED
            task.task_finish_time = time.monotonic()

            self._tasks_failed += 1

            self.log.error("Task %s permanently failed after %d retries", task_id, task.retries)

            self._fire_task_done(task)

    async def _dispatch_loop(self):
        self.log.debug("Dispatch loop started")

        while self.running:
            await self._try_dispatch()
            await asyncio.sleep(self.DISPATCH_INTERVAL)

    async def _try_dispatch(self):
        async with self.queue_lock:
            if not self.queue:
                return

            tasks = sorted(self.queue)

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
                current_worker.active_task_id = task.task_id
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
                        current_worker.active_task_id = None
                        current_worker.status = WorkerStatus.READY

                async with self.queue_lock:
                    heapq.heappush(self.queue, task)

        else:
            task.status = TaskStatus.PENDING
            task.assigned_worker = None

            async with self.queue_lock:
                heapq.heappush(self.queue, task)

    async def _select_worker_for_task(self, task: TaskRecord):
        async with self.workers_lock:
            workers = [
                worker
                for worker in self.workers.values()
                if worker.is_available
                and worker.free_capacity >= task.min_free_capacity
            ]

        if not workers:
            return None

        return min(workers, key=lambda w: w.load_score)

    async def _select_worker_any(self):
        async with self.workers_lock:
            workers = [worker for worker in self.workers.values() if worker.is_available]

        if not workers:
            return None

        return min(workers, key=lambda w: w.load_score)

    async def _heartbeat_loop(self):
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
                self.log.error(
                    "Worker %s heartbeat timeout",
                    worker_id,
                )

                worker.status = WorkerStatus.OFFLINE

                self._fire_worker_change(worker)

                await self._handle_worker_failure(worker_id, worker)

    async def _handle_worker_failure(self, worker_id: str, worker: WorkerState):
        if worker.active_task_id:
            task = self.tasks.get(worker.active_task_id)

            if task and task.status in (
                TaskStatus.ASSIGNED,
                TaskStatus.RUNNING,
            ):
                self.log.warning(
                    "Requeueing task %s after worker failure",
                    worker.active_task_id,
                )

                await self.task_failed(
                    worker_id,
                    worker.active_task_id,
                    "worker_offline",
                )

    async def _metrics_loop(self):
        self.log.debug("Metrics loop started")

        while self.running:
            await asyncio.sleep(self.METRICS_INTERVAL)

            metrics = await self._compute_metrics()

            for callback in self.on_metrics:
                try:
                    callback(metrics)

                except Exception as e:
                    self.log.warning(
                        "Metrics callback error: %s",
                        e,
                    )

    async def _compute_metrics(self):
        now = time.monotonic()

        async with self.workers_lock:
            workers = list(self.workers.values())
        #count how many workers are:
        ready = sum(1 for w in workers if w.status == WorkerStatus.READY)
        busy = sum(1 for w in workers if w.status == WorkerStatus.BUSY)
        off = sum(1 for w in workers if w.status == WorkerStatus.OFFLINE)

        cpus = [w.cpu_usage for w in workers if w.status != WorkerStatus.OFFLINE]

        cutoff = now - 60.0

        self._completion_times = [
            t for t in self._completion_times
            if t > cutoff
        ]

        throughput = len(self._completion_times)

        async with self.queue_lock:
            q_size = len(self.queue)

        return LBMetrics(
            timestamp=now,
            total_workers=len(workers),
            ready_workers=ready,
            busy_workers=busy,
            offline_workers=off,
            queue_size=q_size,
            tasks_completed=self._tasks_completed,
            tasks_failed=self._tasks_failed,
            avg_cpu=sum(cpus) / len(cpus) if cpus else 0.0,
            throughput_per_min=throughput,
        )

    def set_send_callback(self, cb):
        self.send_task_cb = cb

    def set_kick_callback(self, cb):
        self._kick_worker_cb = cb

    async def kick_worker(self, worker_id: str, reason: str = "manual"):
        self.log.warning(
            "Kicking worker %s (reason: %s)",
            worker_id,
            reason,
        )

        if self._kick_worker_cb:
            await self._kick_worker_cb(worker_id)

        await self.unregister_worker(worker_id)

    def on_worker_status_change(self, cb):
        self.on_worker_chage.append(cb)

    def on_task_done(self, cb):
        self.on_task_done.append(cb)

    def on_metrics_update(self, cb):
        self.on_metrics.append(cb)

    def _fire_worker_change(self, worker):
        for callback in self.on_worker_chage:
            try:
                callback(worker)

            except Exception as e:
                self.log.warning(
                    "Worker callback error: %s",
                    e,
                )

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
        async with self.workers_lock:
            workers_snapshot = {
                worker_id: {
                    "status": worker.status.name,
                    "cpu": worker.cpu_usage,
                    "active_task": worker.active_task_id,
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
            "stats": { "completed": self._tasks_completed, "failed": self._tasks_failed,}
        }

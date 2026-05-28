import sys, asyncio, heapq, time
sys.path.insert(0, ".")

from src.loadbalancer.load_balancer import LoadBalancer, WorkerState, WorkerStatus, TaskRecord, TaskStatus, TaskPriority
from src.loadbalancer.task_pool import primes_difficulty, monte_difficulty, matrix_difficulty, render_difficulty, difficulty_to_priority

"""
Test lb mechs - iteration 5.
"""

def run(x): 
    return asyncio.get_event_loop().run_until_complete(x)

def make_lb():
    lb = LoadBalancer()
    async def test_send(wid, task): 
        return True
    lb.set_send_callback(test_send)
    return lb

ws = WorkerState(worker_id="w1", address=("127.0.0.1", 9000))
ws.cpu_usage = 50.0; ws.status = WorkerStatus.BUSY
assert ws.load_score == 70.0
assert ws.free_capacity == 30.0
assert ws.is_available
ws.cpu_usage = 95.0
assert not ws.is_available

heap = []
for p in [TaskPriority.LOW, TaskPriority.CRITICAL, TaskPriority.NORMAL]:
    heapq.heappush(heap, TaskRecord(priority=p))
assert heapq.heappop(heap).priority == TaskPriority.CRITICAL

lb = make_lb()
run(lb.register_worker("w1", "127.0.0.1", 9001))
assert "w1" in lb.workers
run(lb.unregister_worker("w1"))
assert "w1" not in lb.workers

lb = make_lb()
sent = []
async def track_send(wid, task): sent.append(wid); return True
lb.set_send_callback(track_send)
run(lb.register_worker("w1", "127.0.0.1", 9001))
run(lb.update_worker_stats("w1", cpu=10.0))
run(lb.submit_task(payload="job", priority=TaskPriority.NORMAL, difficulty=1.0))
run(lb._try_dispatch())
assert sent == ["w1"]

lb = make_lb()
run(lb.register_worker("w1", "127.0.0.1", 9001))
tid = run(lb.submit_task(payload="shalom haver", priority=TaskPriority.NORMAL, difficulty=1.0, max_retries=2))
lb.tasks[tid].status = TaskStatus.ASSIGNED
run(lb.task_failed("w1", tid, error="crash - working"))
assert lb.tasks[tid].status == TaskStatus.REQUEUED

assert primes_difficulty(50_000) == 1.0
assert monte_difficulty(50_000_000) == 9.0
assert matrix_difficulty(1000, 20) == 9.0
assert render_difficulty(1024, 1024) == 8.0
assert difficulty_to_priority(1.0) == TaskPriority.LOW
assert difficulty_to_priority(8.0) == TaskPriority.CRITICAL

print("lb is good yay")

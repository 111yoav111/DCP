import sys
sys.path.insert(0, ".")

from src.networking.packets import *
from src.tasks.simul_task import SimulationTask
from src.tasks.rend_task import RenderTask

"""
Test Packet build - iteration 4.
"""

task = SimulationTask(simulation_type='primes', start_range=2, end_range=50)
raw  = build_task_packet(task, priority=3)
[pkt] = parse_packets(raw)
assert pkt.packet_type == PACKET_FLAGS.task_request
assert pkt.priority == 3
assert pkt.unpack_payload().task_id == task.task_id

task.result = task.execute()
task.status = "DONE"
[pkt] = parse_packets(build_result_packet(task))
assert pkt.packet_type == PACKET_FLAGS.task_result

t = RenderTask(width=64, height=64)
for i, st in enumerate(t.split_into_subtasks(num_workers=2)):
    assert st.is_subtask and st.subtask_index == i

for raw, flag in [
    (build_hello(),      PACKET_FLAGS.ctrl_hello),
    (build_welcome(1),       PACKET_FLAGS.ctrl_welcome),
    (build_heartbeat(1),     PACKET_FLAGS.ctrl_heartbeat),
    (build_disconnect(1),    PACKET_FLAGS.ctrl_disconnect),
    (build_status(1, 55),    PACKET_FLAGS.ctrl_status),
]:
    assert parse_packets(raw)[0].packet_type == flag

print("all good :)")

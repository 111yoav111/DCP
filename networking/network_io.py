import asyncio
import struct
import logging
from typing import Union

from .packets import *

logger = logging.getLogger("NetworkIO")

MAX_PAYLOAD_BYTES = 64 * 1024 * 1024   # 64 MB sanity cap

TASK_FLAG_VALUES = {f.value for f in PACKET_FLAGS if not f.name.startswith("ctrl_")}


#-----------async read helpers ---------------------------------------------------------

async def read_one_packet(
    reader: asyncio.StreamReader,
) -> Union[ControlPacket, TaskPacket, None]:
    """
    Read exactly one packet from *reader*.
    Returns None on clean end of file, raises ValueError on bad data.
    """
    try:
        first = await reader.readexactly(1)
    except asyncio.IncompleteReadError:
        return None

    flag = first[0] #first byte determines packet type.

    #------control packet (1-byte flag 11 bytes - rest) - alwyas 12 bytes ----------------
    if flag in CTRL_VALUES_SET:
        try:
            rest = await reader.readexactly(CONTROL_HEADER_SIZE - 1)
        except asyncio.IncompleteReadError:
            return None
        pkt = ControlPacket.from_bytes(first + rest)
        logger.debug("recv  %-22s", pkt.packet_type.name)
        return pkt

    #------task packet (28 bytes header) + payload ------------------------------------------------------
    if flag in TASK_FLAG_VALUES:
        try:
            rest_header = await reader.readexactly(TASK_HEADER_SIZE - 1)
        except asyncio.IncompleteReadError:
            return None

        header_raw   = first + rest_header
        payload_size = struct.unpack("!I", header_raw[4:8])[0]   # bytes 4-8

        if payload_size > MAX_PAYLOAD_BYTES: #hopefull not more than 64mb lol
            raise ValueError(f"TaskPacket payload too massive: {payload_size} B")

        try:
            payload_raw = await reader.readexactly(payload_size)
        except asyncio.IncompleteReadError:
            return None

        pkt = TaskPacket.from_bytes(header_raw + payload_raw)
        logger.debug("recv  %-22s  payload=%d B", pkt.packet_type.name, payload_size)
        return pkt

    raise ValueError(f"Unknown flag byte: 0x{flag:02X}")


# ---------lock helper for writes ---------------------------------------------------------

async def _write_locked(writer: asyncio.StreamWriter, lock: asyncio.Lock, data: bytes) -> None:
    """
    Write *data* to *writer* while holding *lock*.
    Used to serialize writes to the same StreamWriter from multiple tasks.
    """
    async with lock:
        writer.write(data)
        await writer.drain() #flush his buffer after writing.


# --------send helper--------------------------------

async def net_send_task(writer, lock, task, priority: int = 1) -> None:
    """Master → worker: task_request packet."""
    await _write_locked(writer, lock, build_task_packet(task, priority=priority))
    logger.info("sent  task_request    uuid=%.8s", task.task_id)


async def net_send_result(writer, lock, task) -> None:
    """
    Worker → master: task_result or subtask_result packet.
    """
    if writer is None:  # connection may have dropped while task was running in executor
        logger.warning("net_send_result: writer is None, dropping result for %.8s", task.task_id)
        return
    await _write_locked(writer, lock, build_result_packet(task))
    logger.info("sent task_result uuid=%.8s", task.task_id)


async def net_send_status(writer, lock, worker_id: int, cpu: int) -> None:
    """
    Worker → master: ctrl_status (CPU %).
    """
    await _write_locked(writer, lock, build_status(worker_id, cpu))
    logger.debug("sent  ctrl_status     cpu=%d%%", cpu)


async def net_send_hello(writer, lock, port: int) -> None:
    """
    Worker → master: ctrl_hello.
    """
    await _write_locked(writer, lock, build_hello(port))
    logger.info("sent ctrl_hello port=%d", port)


async def net_send_welcome(writer, lock, worker_id: int) -> None:
    """
    Master → worker: ctrl_welcome.
    """
    await _write_locked(writer, lock, build_welcome(worker_id))
    logger.info("sent  ctrl_welcome    worker_id=%d", worker_id)


async def net_send_disconnect(writer, lock, worker_id: int, reason: DISCONNECT_FLAGS = DISCONNECT_FLAGS.clean) -> None:
    """
    Either side: ctrl_disconnect.
    """
    await _write_locked(writer, lock, build_disconnect(worker_id, reason))
    logger.info("sent  ctrl_disconnect worker_id=%d  reason=%s", worker_id, reason.name)
    
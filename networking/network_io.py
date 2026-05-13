import asyncio
import struct
import logging
from typing import Union

from .packets import *
from .encrypt_layer import SessionCrypto

logger = logging.getLogger("NetworkIO")

MAX_PAYLOAD_BYTES = 64 * 1024 * 1024   # 64 MB sanity cap

TASK_FLAG_VALUES = {f.value for f in PACKET_FLAGS if not f.name.startswith("ctrl_")}


#-----------async read helpers ---------------------------------------------------------

async def read_one_packet(reader: asyncio.StreamReader, crypto: SessionCrypto = None) -> Union[ControlPacket, TaskPacket, None]:
    """
    Read exactly one packet from *reader*.
    Returns None on clean end of file, raises ValueError on bad data.
    """
    if crypto is not None:
        #encryption path
        plaintext = await crypto.decrypt_from_reader(reader) #read + decrypt the frame into raw packet bytes
        if plaintext is None:
            return None
        
        return _parse_packet_from_bytes(plaintext) #parse the decrypted bytes into a packet object

    try: #plaintext path
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


def _parse_packet_from_bytes(data: bytes) -> Union[ControlPacket, TaskPacket]:
    """
    Parse a single complete packet from a decrypted bytes buffer.
    Only called from the encrypted read path — plaintext path reads incrementally.
    """
    if not data:
        raise ValueError("Empty decrypted packet")

    flag = data[0]

    if flag in CTRL_VALUES_SET:
        return ControlPacket.from_bytes(data)

    if flag in TASK_FLAG_VALUES:
        return TaskPacket.from_bytes(data)

    raise ValueError(f"Unknown flag byte after decryption.")

# ---------lock helper for writes ---------------------------------------------------------

async def _write_locked(writer: asyncio.StreamWriter, lock: asyncio.Lock, data: bytes, crypto: SessionCrypto = None) -> None:
    """
    Write *data* to *writer* while holding *lock*.
    Used to serialize writes to the same StreamWriter from multiple tasks.
    """
    if crypto is not None:
        data = crypto.encrypt(data) #encrypt before sending if session is encrypted

    async with lock:
        writer.write(data)
        await writer.drain() #flush his buffer after writing.


# --------send helper--------------------------------

async def net_send_task(writer, lock, task, priority: int = 1, crypto: SessionCrypto = None) -> None:
    """Master → worker: task_request packet."""
    await _write_locked(writer, lock, build_task_packet(task, priority=priority), crypto)
    logger.info("sent  task_request    uuid=%.8s", task.task_id)


async def net_send_result(writer, lock, task, crypto: SessionCrypto = None) -> None:
    """
    Worker → master: task_result or subtask_result packet.
    """
    if writer is None:  # connection may have dropped while task was running in executor
        logger.warning("net_send_result: writer is None, dropping result for %.8s", task.task_id)
        return
    await _write_locked(writer, lock, build_result_packet(task), crypto)
    logger.info("sent task_result uuid=%.8s", task.task_id)


async def net_send_status(writer, lock, worker_id: int, cpu: int, crypto: SessionCrypto = None) -> None:
    """
    Worker → master: ctrl_status (CPU %).
    """
    await _write_locked(writer, lock, build_status(worker_id, cpu), crypto)
    logger.debug("sent  ctrl_status     cpu=%d%%", cpu)


async def net_send_hello(writer, lock, port: int) -> None:
    """
    Worker → master: ctrl_hello.
    Intentionally no crypto — sent before the AES key is established.
    """
    await _write_locked(writer, lock, build_hello(port))
    logger.info("sent ctrl_hello port=%d", port)


async def net_send_welcome(writer, lock, worker_id: int) -> None:
    """
    Master → worker: ctrl_welcome.
    Intentionally no crypto — sent before the AES key is established.
    """
    await _write_locked(writer, lock, build_welcome(worker_id))
    logger.info("sent  ctrl_welcome    worker_id=%d", worker_id)


async def net_send_disconnect(writer, lock, worker_id: int, reason: DISCONNECT_FLAGS = DISCONNECT_FLAGS.clean, crypto: SessionCrypto = None) -> None:
    """
    Either side: ctrl_disconnect.
    """
    await _write_locked(writer, lock, build_disconnect(worker_id, reason), crypto)
    logger.info("sent  ctrl_disconnect worker_id=%d  reason=%s", worker_id, reason.name)

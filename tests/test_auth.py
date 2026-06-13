import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.networking.encrypt_layer import SessionCrypto, generate_rsa_keypair, send_token, receive_token
from src.networking.network_io import net_send_hello, net_send_welcome, read_one_packet
from src.networking.packets import ControlPacket, PACKET_FLAGS

load_dotenv()
TOKEN = os.getenv("DCP_TOKEN") or "dcp-test-token-placeholder"
print(f"TOKEN loaded: {TOKEN is not None}, value starts with: {str(TOKEN)[:5] if TOKEN else 'NONE'}")
DEMO_WRONG_TOKEN = "hahhsjkfhefhbjewqFGDGSG67hbfgeswhbjgsbh"

PORT = 9876


async def run_server(token_accept: str, result: list):
    """
    Server does the handshake + token check.
    Stores "accepted" or "rejected" in result[0].
    """
    rsa_key = generate_rsa_keypair()
    lock = asyncio.Lock()

    async def handle_connection(reader, writer):
        print("got connection")
        try:
            crypto = await SessionCrypto.master_handshake(reader, writer, rsa_key)
            print("crypto done")
        except Exception as e:
            result[0] = f"crypto handshake failed: {e}"
            writer.close()
            return

        try:
            await read_one_packet(reader)  # consume ctrl_hello
            print("hello received")
        except Exception as e:
            result[0] = f"hello failed: {e}"
            writer.close()
            return

        try:
            received = await receive_token(reader, crypto)
            print("token received")
        except Exception as e:
            result[0] = f"token receive failed: {e}"
            writer.close()
            return

        if received != token_accept:
            result[0] = "rejected"
            writer.close()
            return

        await net_send_welcome(writer, lock, 1)  # assign worker_id=1 for test
        result[0] = "accepted"
        writer.close()

    server = await asyncio.start_server(handle_connection, "127.0.0.1", PORT)
    async with server:
        await server.serve_forever()


async def run_worker(token_send: str) -> str:
    """
    Worker does the handshake + token send.
    Returns "connected" on ctrl_welcome, "rejected" if connection closed.
    """
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
    except Exception as e:
        return f"couldnt connect: {e}"

    lock = asyncio.Lock()

    try:
        crypto = await SessionCrypto.worker_handshake(reader, writer, None)
    except Exception as e:
        writer.close()
        return f"handshake failed: {e}"

    await net_send_hello(writer, lock)
    print("hello sent")

    # always send — token_send is guaranteed non-None from test helpers
    await send_token(writer, token_send, crypto)
    print("token sent")

    try:
        pkt = await read_one_packet(reader)
    except Exception:
        writer.close()
        return "rejected"

    writer.close()

    if pkt is None:
        return "rejected"

    if isinstance(pkt, ControlPacket) and pkt.packet_type == PACKET_FLAGS.ctrl_welcome:
        return "connected"

    return "went wrong"


async def _test_run(token_send: str, token_accept: str) -> tuple[str, str]:
    master_result = ["waiting"]
    master_task = asyncio.create_task(run_server(token_accept, master_result))
    await asyncio.sleep(0.2)

    try:
        worker_result = await asyncio.wait_for(run_worker(token_send), timeout=5)
    except asyncio.TimeoutError:
        master_task.cancel()
        print(f"TIMEOUT — master result so far: {master_result[0]}")
        return "timeout", master_result[0]

    await asyncio.sleep(0.3)
    master_task.cancel()
    return worker_result, master_result[0]


def test_correct_token():
    worker_result, master_result = asyncio.run(_test_run(TOKEN, TOKEN))
    assert worker_result == "connected", f"Worker output: {worker_result}"
    assert master_result == "accepted",  f"Master output: {master_result}"
    print("correct token test - passed")


def test_wrong_token():
    worker_result, master_result = asyncio.run(_test_run(DEMO_WRONG_TOKEN, TOKEN))
    assert worker_result == "rejected", f"Worker output: {worker_result}"
    assert master_result == "rejected",  f"Master output: {master_result}"
    print("wrong token test - passed")


if __name__ == "__main__":
    print("Running auth tests...\n")
    test_correct_token()
    test_wrong_token()
    print("\nALL GOOD - ALL TESTS PASSED :)")

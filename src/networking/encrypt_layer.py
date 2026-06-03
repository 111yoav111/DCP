import asyncio
import os
import struct
import logging
from typing import Optional
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM 

logger = logging.getLogger("EncryptLayer")

RSA_KEY_SIZE = 2048
AES_KEY_SIZE = 32  # 256 bits
AES_NONCE_SIZE = 12  # 96 bits 
FRAME_LAYER_FORMAT = "!I"  # 4 bytes for frame length
FRAME_LAYER_SIZE = struct.calcsize(FRAME_LAYER_FORMAT)


def generate_rsa_keypair():
    """"
    Generate a pair of RSA 2048 private keys.
    Called once by MasterServer at startup; the same key pair is used for all workers connecting to the master.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)
    logger.info("Generated RSA private key for server.")

    return private_key


def rsa_public_key_to_pem(public_key) -> bytes:
    """"
    Convert an RSA public key to PEM for transmission.
    """
    return public_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

def rsa_key_from_pem(pem_data: bytes):
    """"
    Load an RSA public key from PEM data.
    """
    return serialization.load_pem_public_key(pem_data)

def rsa_encrypt_aes_key(public_key, aes_key: bytes) -> bytes:
    """"
    Encrypt a 32-byte AES key using the master RSA public key.
    Called by the worker during handshake.
    """
    ciphertext = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return ciphertext

def rsa_decrypt_aes_key(private_key, ciphertext: bytes) -> bytes:
    """"
    Decrypt the RSA-encrypted AES key blob sent by worker.
    Called by the master during handshake.
    """
    aes_key = private_key.decrypt(
        ciphertext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return aes_key

#-------------AES-256-GCM helpers ---------------------------

def encrypt_packet(aes_key: bytes, plaintext: bytes) -> bytes:
    """
    Encrypt a plaintext using AES-256-GCM.

    Return a frame ready to write to socket:
    [4-byte frame length][12-byte nonce][ciphertext]

    16 + len(ciphertext) bytes - AES-GCM cipertext + authentication tag.

    The 4 byte frame let the rieceiver know how many bytes to read for the full packet (nonce + ciphertext) - reading all without knowing the original plaintext len.
    """
    aesgcm = AESGCM(aes_key)
    nonce = os.urandom(AES_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    payload = nonce + ciphertext

    return struct.pack(FRAME_LAYER_FORMAT, len(payload)) + payload 

def decrypt_packet(aes_key: bytes, frame: bytes) -> Optional[bytes]:
    """
    Decrypt a frame produced by encrypt_packet().

    *The frame isnt including the first 4 bytes (the length), the caller already stripped it off.
    """
    if len(frame) < AES_NONCE_SIZE + 16: #nonce + tag
        raise ValueError("Frame too short to contain valid AES-GCM packet")
    
    nonce = frame[:AES_NONCE_SIZE] #:12
    ciphertext = frame[AES_NONCE_SIZE:] #:12:
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ciphertext, None)

#------------------handshake helpers ------------------------------

def frame_bytes(data: bytes) -> bytes:
    """
    Create a length-prefixed frame from the given data.
    """
    return struct.pack(FRAME_LAYER_FORMAT, len(data)) + data

async def read_frame(reader: asyncio.StreamReader) -> bytes:
    """
    Read a length-prefixed blob from *reader*.
    Used during the handshake to receive the PEM public key and the
    RSA-encrypted AES key blob, both have variable length (so using the 4bytes length prefix).
    """
    raw_len = await reader.readexactly(FRAME_LAYER_SIZE) #first 4 bytes is the length of the frame
    len_frame = struct.unpack(FRAME_LAYER_FORMAT, raw_len)[0]
    if len_frame == 0 or len_frame > 10 * 1024: #10*1024 is 10kb - should be more than enough for PEM and RSA blobs, if more than that something is wrong.
        raise ValueError(f"Handshake frame length way out of range - {len_frame} bytes")
    
    return await reader.readexactly(len_frame)


#----------------AES session key management ------------------------------
class SessionCrypto:
    """
    Represnts the hold of the shared AES key for ONE master-worker connection session.

    Enable encrypting and decrypting packets for that session - presented here as simple instance methods.

    On master side it used for handling worker connection. On worker side it used for connect.

    After the handshake, both master and worker will have the same AES key for that session, SessionCrypto will be passed to the methods in network_io.py to encrypt and decrypt packets for that session.
    """

    def __init__(self, aes_key : bytes):
        if len(aes_key) != AES_KEY_SIZE:
            raise ValueError(f"AES key must be {AES_KEY_SIZE} bytes long")
        self.aes_key = aes_key
    
    #-------handshake class methods ----------------

    @classmethod
    async def master_handshake(cls, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rsa_private_key) -> "SessionCrypto":
        """"
        Master side of the handshake, which is basicly:
        1. send RSA public key to worker (PEM format, prefixed with 4-byte length)
        2. Read back the RSA-encrypted AES key blob from worker (also prefixed with 4-byte length).
        3. Decrypt for getting AES key.
        
        After all - return a ready to use SessionCrypto instance with the shared AES key for that session.
        """

        pem = rsa_public_key_to_pem(rsa_private_key)
        writer.write(frame_bytes(pem))
        await writer.drain()
        logger.debug("Sent RSA public key to worker for making handshake.")

        encrypted_aes_key = await read_frame(reader)
        logger.debug("Received RSA-encrypted AES key blob from worker, length=%d bytes", len(encrypted_aes_key))

        aes_key = rsa_decrypt_aes_key(rsa_private_key, encrypted_aes_key)
        logger.info("Handshake complete, established AES session key with worker.")
        return cls(aes_key)
    
    @classmethod
    async def worker_handshake(cls, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, master_rsa_public_key) -> "SessionCrypto":
        """
        Worker side of the handshake, which is basicly:
        1. Read the RSA public key from master (PEM format, prefixed with 4-byte length).
        2. Generate a random AES key for this session.
        3. Encrypt the AES key using the master's RSA public key.
        4. Send back the RSA-encrypted AES key blob to master (also prefixed with 4-byte length).

        After all - return a ready to use SessionCrypto instance with the shared AES key for that session.
        """
        pem = await read_frame(reader)
        logger.debug("Received RSA public key PEM from master, length=%d bytes", len(pem))
        master_public_key = rsa_key_from_pem(pem)

        aes_key = os.urandom(AES_KEY_SIZE) #generate random AES key for this session
        encrypted_aes_key = rsa_encrypt_aes_key(master_public_key, aes_key) 

        writer.write(frame_bytes(encrypted_aes_key))
        await writer.drain()
        logger.info("Handshake complete, established AES session key with master.")
        return cls(aes_key)
    

    #-------encryption/decryption methods for packets ----------------

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypt a plaintext packet using the session AES key, return a framed packet ready to write to socket.
        """
        return encrypt_packet(self.aes_key, plaintext)
    
    def decrypt(self, frame: bytes) -> bytes:
        """
        Decrypt a framed packet using the session AES key, return the original plaintext.
        """
        return decrypt_packet(self.aes_key, frame)

    async def decrypt_from_reader(self, reader: asyncio.StreamReader) -> Optional[bytes]:
        """
        Read one encrypted frame from *reader*, decrypt it using the session AES key, and return the plaintextbytes.

        This called by read_one_packet() in network_io.py.
        """

        try:
            len_raw = await reader.readexactly(FRAME_LAYER_SIZE) #first 4 bytes is the length of the frame
        except asyncio.IncompleteReadError:
            raise ValueError("Connection closed while reading frame length prefix")
        
        length = struct.unpack(FRAME_LAYER_FORMAT, len_raw)[0]
        if length == 0 or length > 30 * 1024 * 1024: #30mb should be more than enough for any single packet, if more than that something is wrong.
            raise ValueError(f"Encrypted frame length way out of range - {length} bytes")
        
        try:
            frame = await reader.readexactly(length)
        except asyncio.IncompleteReadError:
            return None #EOF/connection lost/etc.
        
        return self.decrypt(frame)
    
    async def encrypt_from_writer(self, writer: asyncio.StreamWriter, lock, plaintext: bytes) -> None:
        """
        Encrypt a plaintext packet and write the framed encrypted packet to *writer*.

        This called by write_packet() in network_io.py.

        holding lock so concurrent writes to the same StreamWriter from multiple tasks will be serialized, preventing interleaving of encrypted frames.
        """
        frame = self.encrypt(plaintext)
        async with lock:
            writer.write(frame)
            await writer.drain()

#----------------auth helper------------------------------------------

async def send_token(writer : asyncio.StreamWriter, token : str, crypto : SessionCrypto) -> None:
    """
    Encrypt and send the auto token to the master after the handshake.

    Token is encrypted with the session AES key so its not showed as plaintext (altought the actual packet is the token - plaintxt).
    """
    token_bytes = token.encode("utf-8")
    encrypted_token = crypto.encrypt(token_bytes)

    writer.write(encrypted_token)
    await writer.drain()

async def receive_token(reader : asyncio.StreamReader, crypto : SessionCrypto) -> str:
    """
    Read and decrypt the token sent by the worker after handshake (and ctrl_hello).

    Returns the token as plain string for checking if its the true token.
    """
    plain_token = await crypto.decrypt_from_reader(reader)
    
    if plain_token is None:
        raise ValueError("Connection closed during auto part")
    
    return plain_token.decode("utf-8")

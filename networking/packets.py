from enum import Enum 
import struct
import itertools
import pickle
import zstandard as zstd
from typing import Union


class PACKET_FLAGS(Enum):
    task_request    = 1   #master requesting worker to execute a task
    task_result     = 2   #worker sending result back to master
    task_ack        = 3   #task recive ok
    task_fail       = 4   #not ok
    subtask_request = 5   #master requesting worker to execute a subtask
    subtask_result  = 6   #worker sending subtask result back to master

    ctrl_hello         = 10  #worker connecting to master
    ctrl_welcome       = 11  #master accepting worker connection, giving worker_id
    ctrl_heartbeat     = 12  #update  request from master to worker.
    ctrl_heartbeat_ack = 13  #answer from worker to master"to hearbeat
    ctrl_disconnect    = 14  #worker disconnecting from master
    ctrl_status        = 15  #worker sending status update to master


class TASK_FLAGS(Enum):
    render     = 1
    simulation = 2


class STATUS_FLAGS(Enum):
    CREATED = 0 # task created but not yet started
    RUNNING = 1 # task is currently running
    DONE    = 2 # task completed successfully
    FAILED  = 3 # task completed with fail


class DISCONNECT_FLAGS(Enum):
    clean      = 0   # normal disconnect
    timeout    = 1   # heartbeat missed/smt with the heartbeat update interval
    task_crash = 2   # worker crashed mid-task
    overloaded = 3   # worker have voluntarily disconnecting due to load
    unknown    = 10 #dk why disconnect


TASK_HEADER_FORMAT = '!B B B B I Q Q H H'
TASK_HEADER_SIZE = struct.calcsize(TASK_HEADER_FORMAT)

CONTROL_HEADER_FORMAT = '!B B H Q'
CONTROL_HEADER_SIZE = struct.calcsize(CONTROL_HEADER_FORMAT)

ZSTD_COMPRESS_LEVEL = 3

NO_PARENT = 0
NO_SUBTASK = 0XFFFF

CTRL_VALUES_SET = set()
for f in PACKET_FLAGS:
    if f.name.startswith('ctrl_'): 
        CTRL_VALUES_SET.add(f.value)


#id counter for tasks and subtasks
id_counter = itertools.count(1) # start from 1 to infinty, 0 is saved for no parent

def next_id() -> int:
    """"
    returns the next id (unique), will be used by master once before building a packet.
    """
    return next(id_counter)

class TaskPacket:
    """
    Carries task or its result, over the network.
    
    format: header(28 bytes) + payload(varible: zstd(pickle(task)))
    
    loadbalancer read only the header, master and worker do the unpacking to the payload for getting the task object.
    """

    __slots__ = [
        "packet_type",
        "task_type",
        "status",
        "priority",
        "task_id",
        "parent_id",
        "subtask_index",
        "total_subtasks",
        "raw_payload"
    ]

    @classmethod
    def from_task(cls, task, packet_type: PACKET_FLAGS, task_type: TASK_FLAGS, status: STATUS_FLAGS, priority: int = 1, parent_id: int = None, subtask_index: int = None, total_subtasks: int = None) -> 'TaskPacket':
        """
        create a TaskPacker from task object. it return the oject as an instance of TaskPacker with all the field filled (expect the payload)

        ALL THIS HAPPEN ON SENDER SIDE.
        """
        pkt = cls.__new__(cls)
        pkt.packet_type = packet_type
        pkt.task_type = task_type
        pkt.status = STATUS_FLAGS[task.status]
        pkt.priority = max(0, min(255, priority)) # must be between 0 and 255
        pkt.task_id = task.task_id
        pkt.parent_id = parent_id 
        pkt.subtask_index = subtask_index
        pkt.total_subtasks = total_subtasks

        raw_pickle = pickle.dumps(task, protocol=pickle.HIGHEST_PROTOCOL)
        pkt.raw_payload = zstd.ZstdCompressor(level=ZSTD_COMPRESS_LEVEL).compress(raw_pickle)
        return pkt 

    @classmethod
    def from_bytes(cls, data: bytes) -> 'TaskPacket':
        """
        create a TaskPacket from bytes, kinda do the oppsite of from_task, it return the oject as an instance of TaskPacker with all the field filled (expect the payload).

        ALL THIS HAPPEN ON RECEIVER SIDE.
        """
        if len(data) < TASK_HEADER_SIZE:
            raise ValueError("Data too small for a packet like TaskPacket")
        
        pkt = cls().__new__(cls)

        header = struct.unpack(TASK_HEADER_FORMAT, data[:TASK_HEADER_SIZE])
        (packet_type,
        task_type,
        status,
        priority,
        payload_size,
        task_id,
        parent_id,
        subtask_id,
        total_subtasks) = header

        pkt.packet_type = PACKET_FLAGS(packet_type)
        pkt.task_type = TASK_FLAGS(task_type)
        pkt.status = STATUS_FLAGS(status)
        pkt.priority = priority
        pkt.task_id = task_id
        pkt.parent_id = parent_id if parent_id != NO_PARENT else None
        pkt.subtask_index = subtask_id if subtask_id != NO_SUBTASK else None
        pkt.total_subtasks = total_subtasks if total_subtasks != NO_SUBTASK else None

        paylod_end = TASK_HEADER_SIZE + payload_size
        if len(data) < paylod_end:
            raise ValueError("Data too small for the payload of this TaskPacket")
        
        pkt.raw_payload = data[TASK_HEADER_SIZE:paylod_end]
        return pkt

    def unpack_payload(self):
        """
        dicompress -> unpickle the payload, return object.
        """
        return pickle.loads(zstd.ZstdDecompressor().decompress(self.raw_payload))
    
    def to_bytes(self) -> bytes:
        """
        pack the header and payload to bytes, ready to send over the network.
        """
        header = struct.pack(
            TASK_HEADER_FORMAT,
            self.packet_type.value,
            self.task_type.value,
            self.status.value,
            self.priority,
            len(self.raw_payload),
            self.task_id,
            self.parent_id if self.parent_id is not None else NO_PARENT,
            self.subtask_index if self.subtask_index is not None else NO_SUBTASK,
            self.total_subtasks if self.total_subtasks is not None else NO_SUBTASK
        )
        return header + self.raw_payload

    @property
    def is_subtask(self) -> bool:
        return self.subtask_index is not None
    
    @property
    def payload_size(self) -> int:
        return len(self.raw_payload)
    
    def __repr__(self) -> str:
        return f"TaskPacket(type = {self.packet_type.name}) | task type = {self.task_type.name} | task status = {self.status.name} |task priority = {self.priority} | task id = {self.task_id}"
    

class ControlPacket:
    """
    carries the control of the connection over the network. 
    
    format: always 12 bytes.
    """
    
    __slots__ = [
        "packet_type",
        "packet_flags",
        "extra_info",
        "worker_id"
    ]

    def __init__(self, packet_type : PACKET_FLAGS, packet_flags : int, extra_info : int, worker_id : int):
        self.packet_type = packet_type
        self.packet_flags = packet_flags
        self.extra_info = extra_info
        self.worker_id = worker_id

    @classmethod
    def hello(cls, port : int):
        """
        worker -> master/loadbalancer, meaning start of connection - asking the master to assign an id.
        given paratmeter of port, so mastser know how to connect back.
        """
        return cls(packet_type = PACKET_FLAGS.ctrl_hello, packet_flags = 0, extra_info = port, worker_id = 0)
    
    @classmethod
    def welcome(cls, assigned_worker_id : int):
            """
            master/loadbalancer -> worker, after reciving hello_packer, master assing id to the worker and return it to him.
            """
            return cls(packet_type = PACKET_FLAGS.ctrl_welcome, packet_flags = 0, extra_info = 0, worker_id = assigned_worker_id)
    
    @classmethod
    def heartbeat(cls, worker_id : int):
            """
            either side gonna send it, meaning "still connected""
            """
            return cls(packet_type = PACKET_FLAGS.ctrl_heartbeat, packet_flags = 0, extra_info = 0, worker_id = worker_id)
    
    @classmethod
    def heartbeat_ack(cls, worker_id : int):
            """
            either side, meaning they got the heartbeat packet - confirm.
            """
            return cls(packet_type = PACKET_FLAGS.ctrl_heartbeat_ack, packet_flags = 0, extra_info = 0, worker_id = worker_id)

    @classmethod
    def disconnect(cls, worker_id : int, reason : DISCONNECT_FLAGS = DISCONNECT_FLAGS.clean):
            """
            worker -> master, tell master u disconnect + add the reason now based on DISCONNECT_FLAGS.
            """
            return cls(packet_type = PACKET_FLAGS.ctrl_disconnect, packet_flags = 0, extra_info = reason.value, worker_id = worker_id)
    
    @classmethod
    def status(cls, worker_id : int, cpu_precent : int):
            """
            worker -> master/loadbalancer, sending update of the cpu precent currently used.
            """
            return cls(packet_type = PACKET_FLAGS.ctrl_status, packet_flags = cpu_precent, extra_info = 0, worker_id = worker_id)
    
    def to_bytes(self) -> bytes:
         """
         convert ControlPacket into bytes, 
         always be 12 bytes.
         """
         return struct.pack(CONTROL_HEADER_FORMAT, self.packet_type.value, self.packet_flags, self.extra_info, self.worker_id)

    @classmethod
    def from_bytes(cls, data : bytes) -> 'ControlPacket':
            """
            make ControlPacket object from bytes
            """
            if len(data) != CONTROL_HEADER_SIZE:
                 raise ValueError("ControlPacket must be 12 bytes(CONTROL_HEADER_SIZE)")
            
            packet_type, packet_flags, extra_info, worker_id = struct.unpack(CONTROL_HEADER_FORMAT, data[:CONTROL_HEADER_SIZE])

            return cls(PACKET_FLAGS(packet_type), packet_flags, extra_info, worker_id)
    
    @property
    def cpu_precent(self) -> int:
         return self.packet_flags
    
    @property
    def disconnect_reason(self) -> DISCONNECT_FLAGS:
         return DISCONNECT_FLAGS(self.extra_info)
    
    @property
    def listen_port(self) -> int:
         return (self.extra_info)

    def __repr__(self) -> str:
        match self.packet_type:
            case PACKET_FLAGS.ctrl_welcome:
                info = f"worker_id={self.worker_id}"
            case PACKET_FLAGS.ctrl_hello:
                info = f"port={self.listen_port}"
            case PACKET_FLAGS.ctrl_disconnect:
                info = f"reason={self.disconnect_reason.name}"
            case PACKET_FLAGS.ctrl_status:
                info = f"cpu={self.cpu_precent}"
            case _:
                info = f"flags={self.packet_flags}, extra_info={self.extra_info}"

        return f"ControlPacket(type={self.packet_type.name}) | worker_id={self.worker_id} | details={info}"


#---------task type detection-----------
def detect_task_type(task) -> TASK_FLAGS:
     """
     check if task class name is RenderTask or SumulationTask.
     """
     classname = task.__class__.__name__
     if classname == "RenderTask":
          return TASK_FLAGS.render
     elif classname == "SimulationTask":
          return TASK_FLAGS.simulation
     else:
          raise TypeError("Task must either be Render of Simualtion")
          
#----------build the packet - task--------
def build_task_packet(task, priority : int = 1) -> bytes:
     """
     master -> worker, wrap task for sending

     get a task packet from task object(from_task), add the values needed, return it as packet(to_bytes)
     """
     pkt = TaskPacket.from_task(
          task=task,
          packet_type=PACKET_FLAGS.task_request,
          task_type=detect_task_type(task),
          status=task.status,
          priority=priority
     )
     return pkt.to_bytes()

def build_subtask_packet(subtask, parent_id : int, subtask_index : int, total_subtasks : int, priority : int = 1) -> bytes:
     """
     master -> worker, wrap subtask for sending
     """
     pkt = TaskPacket.from_task(
          task=subtask,
          packet_type=PACKET_FLAGS.subtask_request,
          task_type=detect_task_type(subtask),
          status=subtask.status,
          priority=priority,
          parent_id=parent_id,
          subtask_index=subtask_index,
          total_subtasks=total_subtasks
    )
     return pkt.to_bytes()

def build_result_packet(task) -> bytes:
     """
     worker -> master, a complete task to send back the results.
     """
     is_subtask = getattr(task, "is_subtask", False)
     if is_subtask:
          packet_type = PACKET_FLAGS.subtask_result
     else:
          packet_type = PACKET_FLAGS.task_result
     pkt = TaskPacket.from_task(
          task=task,
          packet_type=packet_type,
          task_type=detect_task_type(task),
          status=task.status
     )
     return pkt.to_bytes()

def split_task(task, num_workers : int, priority : int = 1) -> list[bytes]:
     """
     happens only on master side, split a divisble task and encode each subtask
     returns list of bytes ready to send, each part of the the list is a subtask.
     """

     subtasks = task.split_into_subtasks(num_workers)

     if len(subtasks) == 1 and subtasks[0] == task:
          return [build_task_packet(task, priority=priority)]
     
     parent_id = task.task_id
     total = len(subtasks)

     for sub in subtasks:
          sub.task_id = next_id()

     subtasks_list = []

     for idx, sub in enumerate(subtasks):
          packet = build_subtask_packet(sub, parent_id, idx, total, priority)
          subtasks_list.append(packet)
     
     return subtasks_list

#--------build the packet - control-----------

def build_hello(port : int) -> bytes:
    return ControlPacket.hello(port).to_bytes()

def build_welcome(worker_id : int) -> bytes:
     return ControlPacket.welcome(worker_id).to_bytes()

def build_heartbeat(worker_id : int) -> bytes:
     return ControlPacket.heartbeat(worker_id).to_bytes()

def build_heartbeat_ack(worker_id : int) -> bytes:
     return ControlPacket.heartbeat_ack(worker_id).to_bytes()

def build_disconnect(worker_id : int, reason : DISCONNECT_FLAGS = DISCONNECT_FLAGS.clean) -> bytes:
     return ControlPacket.disconnect(worker_id, reason).to_bytes()

def build_status(worker_id : int, cpu_precent : int) -> bytes:
     return ControlPacket.status(worker_id, cpu_precent).to_bytes()

#--------Parse packet--------

def parse_packets(data : bytes) -> list[Union[ControlPacket, TaskPacket]]:
     """
     parse the raw byte stream and retrun every packet found in it.

     reads the first byte as a flag, decide the type of packet (with control its ez since its always 12bytes),slice the exact number of bytes needed,
     move the cursor so it look for next packet to slice.

     returns a list of both ControlPacket and TaskPacket.
     will return None if smt unexpected happened.
     """
     if not data: #in case which is emepty somehow
          return []
     
     packets = [] #the list to return
     cursor = 0 #start from first byte.

     while cursor < len(data):
            # slice the first byte and the first value in the tuple (from the unpack).
            flag = struct.unpack("!B", data[cursor:cursor+1])[0] 

            if flag in CTRL_VALUES_SET: #if its ControlPacket - always 12bytes
                end = cursor + CONTROL_HEADER_SIZE #cursor index +12bytes, which should be the end of the control packet.
                if len(data) < end:
                    print(f"ERROR - parse_packets:this packet size have to be 12bytes, this one is {len(data)}")
                    return None 
                
                packets.append(ControlPacket.from_bytes(data[cursor:end])) #add the packet to the list
                cursor = end #go for next packet, move cursor to the end 

            elif flag in (f.value for f in PACKET_FLAGS if not f.name.startswith("ctrl_")): #check if flag is in PACKET_FLAGS + check that it isnt a Control flag.
                if len(data) - cursor < TASK_HEADER_SIZE:
                    print(f"ERROR - parse_packets:task header should be {TASK_HEADER_SIZE}, now its {len(data) - cursor}")
                    return None
                
                # read the size which is in bytes 4-8 in TaskPacket, then get just the value (size) from the tuple.
                payload_size = struct.unpack("!I", data[cursor + 4:cursor + 8])[0] 
                end = cursor + TASK_HEADER_SIZE + payload_size #end of the TaskPacket
                
                if len(data) < end:
                    print(f"ERROR - parse_packets:somehow the size of packet is longer than the whole stream.")
                    return None

                packets.append(TaskPacket.from_bytes(data[cursor:end]))
                cursor = end #go for next packet, move cursor to the end

            else: #unknowflag - not ControlPacker or TaskPacket
                print(f"ERROR:unknown flag - {flag} at byte {cursor}")
                return None
               
     return packets

def read_header(data : bytes) -> dict:
     """
     Decodes only the header of a raw TaskPacket - no payload decompression.
     useful for loadbalancer to make decisions about how to handle the packet.

     returns a dict which tells about the attributes the packet include.
     """
     pkt = TaskPacket.from_bytes(data)

     return {
          "packet_type": pkt.packet_type,
          "task_type": pkt.task_type,
          "status": pkt.status,
          "priority": pkt.priority,
          "task_id": pkt.task_id,
          "parent_id": pkt.parent_id,
          "subtask_index": pkt.subtask_index,
          "total_subtasks": pkt.total_subtasks,
          "payload_size": pkt.payload_size,
          "is_subtask": pkt.is_subtask
     }
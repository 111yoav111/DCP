# DCP - Dynamic Compute Power

DCP is a distributed computing system written in Python, designed to improve computational power (mostly CPU) by sharing tasks across multiple computers over a network. The master machine generates and distributes tasks, while worker machines execute them and return results.

## 🎥 Demo 
<img width="800" height="450" alt="DCPdemo" src="https://github.com/user-attachments/assets/751c88a2-a273-4f86-8b81-c5f109678451" />

## ✨ Features
- Custom binary packet protocol over TCP
- Zstandard payload compression for reduced network overhead
- RSA handshake for key exchange + AES-256-GCM transport encryption
- Non-blocking async networking via asyncio
- Multiprocessing task execution via ProcessPoolExecutor
- Dynamic load balancing based on real time CPU usage and task difficulty
- Priority based heap queue - critical tasks always dispatch first
- Forced dispatch after 10 skips - prevents task starvation
- Automatic task splitting - divisible tasks (render, primes, Monte Carlo) split into subtasks across workers and merged automatically; non-divisible tasks (matrix) run on a single worker
- Worker failure detection via heartbeat monitoring with automatic task re-queuing
- Automatic worker reconnect - retries connection every 5 seconds on disconnect
- Real time UI for master and worker - connected workers, CPU%, active tasks, metrics and task/worker cancellation

## ⚙️ Architecture Overview

- **Master** — runs the TCP server, load balancer, task pool, and UI on the main machine. It is responsible for distributing tasks and coordinating workers.

- **Worker** — connects to the master, receives tasks, executes them using a `ProcessPoolExecutor`, and returns results.

- **Networking** — custom binary protocol over TCP with AES-256-GCM encrypted communication for secure and efficient data transfer.

- **Tasks** — includes simulation workloads (primes, matrix operations, Monte Carlo simulations) and ray-tracing render tasks. Many tasks are divisible and can be split into subtasks for parallel execution across multiple workers.

### Task Flow
```
Master
  ↓
Task Pool
  ↓
Load Balancer
  ↓
Workers (parallel execution)
  ↓
Result aggregation
  ↓
Master
```

## 📁 Project Structure
```txt
DCP/
├── src/
│   ├── loadbalancer/
│   │   ├── load_balancer.py
│   │   └── task_pool.py
│   ├── master/
│   │   ├── main_master.py
│   │   ├── master_ui.py
│   │   └── server.py
│   ├── networking/
│   │   ├── encrypt_layer.py
│   │   ├── network_io.py
│   │   └── packets.py
│   ├── tasks/
│   │   ├── task.py
│   │   ├── divisible_task.py
│   │   ├── simul_task.py
│   │   └── rend_task.py
│   └── worker/
│       ├── main_worker.py
│       ├── client.py
│       └── worker_ui.py
├── tests/
│   ├── test_tasks.py
│   ├── test_packets.py
│   └── test_lb.py
├── assets/
├── renders/
├── .gitignore
├── README.md
├── requirements.txt
└── start.bat
```

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Network-connected machines
- Windows recommended 

### Installation
```bash
# Clone the repo
git clone https://github.com/111yoav111/DCP
cd DCP

# Create virtual environment
python -m venv venv

# Active venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```
> Note: Some dependency versions may not support older/newer Python releases.

### Run
#### Option 1 - start.bat (Windows only)
```bat
start.bat
```
#### Option 2 - manually
Master:
```bash
python src/master/main_master.py
```

Worker:
```bash
python src/worker/main_worker.py
```

## 🌐 Network Setup
- The master node should bind to the host machine’s local IP address
- Worker nodes must connect using the master’s IP address
- Default communication port: 9000

## ✅ Tests
To test individual components of the project:
```
python tests/test_tasks.py   # render + simulation tasks
python tests/test_packets.py # packet protocol
python tests/test_lb.py      # load balancer logic
```

## 📦 Built With 
- [asyncio](https://docs.python.org/3/library/asyncio.html) - asynchronous networking and concurrency
- [cryptography](https://cryptography.io/en/latest/) - RSA key exchange and AES-256-GCM encryption
- [psutil](https://psutil.readthedocs.io/) - real-time CPU monitoring
- [numpy](https://numpy.org/) - simulation and render tasks
- [zstandard](https://python-zstandard.readthedocs.io/) - payload compression
- [tkinter](https://docs.python.org/3/library/tkinter.html) - GUI

## Author
Yoav - @111yoav111


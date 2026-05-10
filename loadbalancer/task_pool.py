import asyncio
import random
import logging

from tasks.simul_task import SimulationTask
from tasks.rend_task import RenderTask
from loadbalancer.load_balancer import LoadBalancer, TaskPriority

logger = logging.getLogger("TaskPool")

BASE_INTERVAL = 3.0 #second between task gen per 1 worker
MIN_INTERVAL = 0.5 #never gen faster than this
MAX_QUEUE_PER_WORKER = 4 #stop gen if queue has more than 4 tasks per worker


#--------difficulty messurements-------------
def primes_difficulty(end_range : int) -> float:
    if end_range < 100_000:
        return 1.0
    elif end_range < 500_000:
        return 3.0
    elif end_range < 1_000_000:
        return 6.0
    else:
        return 9.0
    
def monte_difficulty(num_samples : int) -> float:
    if num_samples < 1_000_000:
        return 1.0
    elif num_samples < 5_000_000:
        return 3.0
    elif num_samples < 10_000_000:
        return 6.0
    else:
        return 9.0
    
def matrix_difficulty(size : int, iterations : int) -> float:
    difclt_score = (size / 100) + (iterations * 0.5)
    if difclt_score < 5:
        return 1.0
    elif difclt_score < 10:
        return 3.0
    elif difclt_score < 20:
        return 6.0
    else:
        return 9.0
    
def render_difficulty(width : int, height : int) -> float:
    pixles = width * height
    if pixles < 256 * 256:
        return 2.0
    if pixles < 512 * 512:
        return 5.0
    else:
        return 8.0
    
#-----------convert priority from difficulty--------
def difficulty_to_priority(difficulty : float) -> TaskPriority:
    if difficulty < 3.0:
        return TaskPriority.LOW
    elif difficulty < 5.0:
        return TaskPriority.NORMAL
    elif difficulty < 7.0:
        return TaskPriority.HIGH
    else:
        return TaskPriority.CRITICAL
    
#-----------genarte tasks--------------------
def genarte_primes_task() -> 'SimulationTask':
    end_range = random.choice([50_000, 100_000, 300_000, 500_000, 750_000, 1_000_000, 2_000_000])
    difficulty = primes_difficulty(end_range)
    task = SimulationTask(simulation_type="primes", difficulty=difficulty, end_range=end_range)
    logger.debug(f"Genarted primes task: range = {end_range}, task difficulty = {difficulty}")
    return task 

def genarte_monte_task() -> 'SimulationTask':
    num_samples = random.choice([500_000, 1_000_000, 3_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000])
    difficulty = monte_difficulty(num_samples)
    task = SimulationTask(simulation_type="monte_carlo", difficulty=difficulty, num_samples=num_samples)
    logger.debug(f"Genarted monte task: arrows = {num_samples}, task difficulty = {difficulty}")
    return task

def genarte_matrix_task() -> 'SimulationTask':
    size = random.choice([100, 200, 300, 500, 800, 1000])
    iterations = random.choice([2, 5, 10, 15, 20])
    difficulty = matrix_difficulty(size, iterations)
    task = SimulationTask(simulation_type="matrix", difficulty=difficulty, matrix_size=size, iterations=iterations)
    return task

def genarte_render_task() -> 'RenderTask':
    width  = random.choice([128, 256, 512, 1024])
    height = random.choice([128, 256, 512, 1024])
    difficulty = render_difficulty(width=width, height=height)
    task = RenderTask(difficulty=difficulty, width=width, height=height)
    return task

#------------main genartion loop---------------
async def task_pool_loop(lb : LoadBalancer) -> None:
    """
    Genarte random tasks and sumbit them to LB.
    The gen rate is based on the number of connected workers:
        interval = max((BASE_INTERVAL / worker_num) , MIN_INTERVAL)
    """
    genartion_options = [genarte_primes_task, genarte_monte_task, genarte_matrix_task, genarte_render_task]

    while True:
        snapshot = await lb.get_snapshot()
        num_workers = len([
            worker 
            for worker in snapshot["workers"].values()
            if worker["status"] != "OFFLINE"
        ])
        q_size = len(snapshot["queue"])

        if num_workers == 0:
            logger.debug("No worker connected - no reason to gen tasks.")
            await asyncio.sleep(BASE_INTERVAL)
            continue

        if q_size >= num_workers * MAX_QUEUE_PER_WORKER:
            #queue is large enough, no reason to gen tasks
            logger.debug(f"Queue size {q_size} >= {num_workers * MAX_QUEUE_PER_WORKER}, pause genartion.")
            await asyncio.sleep(BASE_INTERVAL)
            continue

        task = random.choice(genartion_options)()  # () — call the function, not just select it
        priority = difficulty_to_priority(task.task_difficulty)

        await lb.submit_task(
            payload=task,
            priority=priority,
            difficulty=task.task_difficulty
        )

        logger.info(
            f"sumbitted: [{task.__class__.__name__}] | id ={task.task_id[:8]} | difficulty = {task.task_difficulty} | priority = {priority.name} | workers ={num_workers} | queue ={q_size}"
        )

        interval = max((BASE_INTERVAL / num_workers), MIN_INTERVAL)
        await asyncio.sleep(interval)
        
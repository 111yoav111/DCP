import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image
from src.tasks.rend_task import RenderTask
from src.tasks.simul_task import SimulationTask

"""
Test tasks back from iteration 1. Back then these files were 2 seprate, combined them into 1.
"""

RENDERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "renders")

def test_render_task():
    print("Starting RenderTask test...")

    task1 = RenderTask(width=250, height=250)

    result = task1.execute()

    image_data = np.frombuffer(result['image_data'], dtype=np.uint8).reshape(result['shape'])

    img = Image.fromarray(image_data, 'RGB')
    img.save(RENDERS_DIR, "render_test_output.png")

    print("render test")

def test_simulation_task():
    print("shalom! Starting SimulationTask tests...\n")

    print("primes...")
    primes_task = SimulationTask(simulation_type='primes', start_range=2, end_range=50)
    primes_result = primes_task.execute()
    print(f"Primes result: {primes_result['primes']}")
    print(f"Count: {primes_result['count']}\n")

    print("matrix...")
    matrix_task = SimulationTask(simulation_type='matrix', matrix_size=5, iterations=3)
    matrix_result = matrix_task.execute()
    print(f"Matrix sum: {matrix_result['sum']}")
    print(f"Matrix size: {matrix_result['matrix_size']}, Rounds: {matrix_result['rounds']}\n")

    print("monte_carlo...")
    monte_task = SimulationTask(simulation_type='monte_carlo', num_samples=100000)
    monte_result = monte_task.execute()
    print(f"Monte Carlo pi estimate: {monte_result['pi_estimate']}")
    print(f"Arrows thrown: {monte_result['arrows_thrown']}\n")


if __name__ == "__main__":
    test_render_task()
    print("\n ayayayayyayayayayayyayayayayayayyayayayaya \n")
    test_simulation_task()
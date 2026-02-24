from simul_task import SimulationTask

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
    test_simulation_task()
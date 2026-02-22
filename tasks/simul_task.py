import numpy as np
from .divisible_task import DivisibleTask


class SimulationTask(DivisibleTask):

    SIMULATION_TYPES = ['primes', 'matrix', 'monte_carlo']

    def __init__(self, simulation_type: str = 'primes', difficulty: float = 1.0, #set default values for all parameters
                 start_range: int = None, end_range: int = None,
                 matrix_size: int = None, iterations: int = None,
                 num_samples: int = None):

        if simulation_type not in self.SIMULATION_TYPES: 
            raise ValueError(f"simulation_type must be one of {self.SIMULATION_TYPES}")

        super().__init__(difficulty)
        self.simulation_type = simulation_type

        if simulation_type == 'primes':
            if start_range is not None: #meaning if the user provided a value for start_range, use it. Otherwise, default to 2
                self.start_range = start_range
            else:
                self.start_range = 2
            
            if end_range is not None:
                self.end_range = end_range #meaning if the user provided a value for end_range, use it. Otherwise, default to 1 million
            else:
                self.end_range = 1_000_000 

        elif simulation_type == 'matrix':
            if matrix_size is not None:
                self.matrix_size = matrix_size #meaning if the user provided a value for matrix_size, use it. Otherwise, default to 500
            else:
                self.matrix_size = 500
            
            if iterations is not None: 
                self.round_matrix = iterations #meaning if the user provided a value for iterations, use it. Otherwise, default to 10
            else:
                self.round_matrix = 10

        elif simulation_type == 'monte_carlo':
            if num_samples is not None:
                self.arrows_thrown = num_samples #meaning if the user provided a value for num_samples, use it. Otherwise, default to 10 million
            else:
                self.arrows_thrown = 10_000_000

    #prime number generation task
    def is_prime(self, n: int) -> bool:
        """
        find all the prime numbers in the range [2, n).
        """
        if n < 2:
            return False
        for i in range(2, n):
            if n % i == 0:
                return False
        return True
    
    def find_primes(self, start: int, end: int) -> list[int]:
        """
        send list with all the prime numbers in the range [start, end) which is the task for this specific worker.
        """
        prime_numbers = []
        for n in range(start, end):
            if self.is_prime(n):
                prime_numbers.append(n)
        return prime_numbers

    #matrix simulation task
    def matrix_simulation(self, size: int, round_matrix: int) -> float:
        matrix = np.random.rand(size, size) 
        for _ in range(round_matrix):
            matrix = np.dot(matrix, matrix)
            matrix = matrix / np.max(matrix)
        return float(np.sum(matrix))

    #monte carlo simulation task
    def monte_carlo_pi(self, arrows_thrown: int) -> float:
        x = np.random.uniform(-1, 1, arrows_thrown)
        y = np.random.uniform(-1, 1, arrows_thrown)
        arrows_inside = int(np.sum((x ** 2 + y ** 2) <= 1))
        return (arrows_inside / arrows_thrown)* 4

    def execute(self) -> dict:
        self.status = "RUNNING"

        if self.simulation_type == 'primes':
            primes = self.find_primes(self.start_range, self.end_range)
            result = {
                'type': 'primes',
                'primes': primes,
                'count': len(primes),
                'range': (self.start_range, self.end_range)
            }

        elif self.simulation_type == 'matrix':
            sum_result = self.matrix_simulation(self.matrix_size, self.round_matrix)
            result = {
                'type': 'matrix',
                'sum': sum_result,
                'matrix_size': self.matrix_size,
                'rounds': self.round_matrix
            }

        elif self.simulation_type == 'monte_carlo':
            pi_estimate = self.monte_carlo_pi(self.arrows_thrown)
            result = {
                'type': 'monte_carlo',
                'pi_estimate': pi_estimate,
                'arrows_thrown': self.arrows_thrown
            }

        self.status = "DONE"
        return result

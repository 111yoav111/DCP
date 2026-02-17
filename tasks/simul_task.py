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
                self.iterations = iterations #meaning if the user provided a value for iterations, use it. Otherwise, default to 10
            else:
                self.iterations = 10

        elif simulation_type == 'monte_carlo':
            if num_samples is not None:
                self.num_samples = num_samples #meaning if the user provided a value for num_samples, use it. Otherwise, default to 10 million
            else:
                self.num_samples = 10_000_000

    #prime number generation task
    def _is_prime(self, n: int) -> bool:
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
            if self._is_prime(n):
                prime_numbers.append(n)
        return prime_numbers


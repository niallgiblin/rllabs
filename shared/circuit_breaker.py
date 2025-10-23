import time
from functools import wraps

# Starting implementation of the Circuit Breaker pattern.
# This helps prevent a service from repeatedly trying to execute an operation
# that is likely to fail, allowing it to fail fast and prevent system overload.

class CircuitBreaker:
    def __init__(self, failure_threshold, recovery_timeout):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "CLOSED"
        self.last_failure_time = None

    def call(self, func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if self.state == "OPEN":
                if self.last_failure_time and (time.time() - self.last_failure_time > self.recovery_timeout):
                    # Transition to HALF-OPEN state
                    self.state = "HALF_OPEN"
                else:
                    raise Exception("Circuit is OPEN")

            try:
                result = await func(*args, **kwargs)
                # If the call was successful, reset the failure count
                self.failure_count = 0
                if self.state == "HALF_OPEN":
                    self.state = "CLOSED"
                return result
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if self.failure_count >= self.failure_threshold:
                    # Transition to OPEN state
                    self.state = "OPEN"
                raise e
        return wrapper

# Example usage:
# breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
# @breaker.call
# async def potentially_failing_operation():
#     # ... call to another service
#     pass

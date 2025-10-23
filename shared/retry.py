import asyncio
import random
from functools import wraps

# Implementation of a retry decorator with exponential backoff and jitter.
# Pattern for resilient communication, handling transient failures gracefully.

def retry_with_exponential_backoff(retries=3, initial_delay=1, max_delay=16):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            delay = initial_delay
            for i in range(retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if i == retries - 1:
                        raise e
                    # Apply jitter to avoid thundering herd problem
                    jitter = random.uniform(0, delay * 0.25)
                    sleep_time = delay + jitter
                    
                    print(f"Attempt {i+1} failed. Retrying in {sleep_time:.2f} seconds...")
                    await asyncio.sleep(sleep_time)
                    
                    delay = min(delay * 2, max_delay)
        return wrapper
    return decorator

# Example Usage:
# @retry_with_exponential_backoff(retries=5)
# async def make_request_to_another_service():
#     # ... http request logic
#     pass

import time
import logging

# # Logger 설정
# logger = logging.getLogger("timing_logger")
# 로거 설정
logger = logging.getLogger("uvicorn.access")
logger.setLevel(logging.INFO)

# 데코레이터 정의
def timing_decorator(func):
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        logger.info(f"{func.__name__} Execution Time: {end - start:.6f} seconds")
        return result
    return wrapper
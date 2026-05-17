"""Injectable bugs. Each scenario flips exactly one flag."""
from dataclasses import dataclass


@dataclass
class BugFlags:
    memory_leak: bool = False          # /order leaks a 1MB buffer per request
    slow_db_query: bool = False        # checkout query takes 5s instead of 20ms
    bad_deploy_500: bool = False       # /order returns 500 on a code path
    dependency_timeout: bool = False   # payment service times out
    cache_stampede: bool = False       # cache eviction causes DB pile-up


FLAGS = BugFlags()


def reset() -> None:
    global FLAGS
    FLAGS = BugFlags()

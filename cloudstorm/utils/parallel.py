"""
Parallel execution utilities for cloudstorm.
"""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypeVar

from .display import print_error

T = TypeVar('T')


def run_parallel(
    func: Callable[..., T],
    items: list[Any],
    max_workers: int = 10,
    item_name: str = "item"
) -> dict[Any, T]:
    """
    Run a function in parallel for each item.

    Args:
        func: Function to run. First argument should accept the item.
        items: List of items to process.
        max_workers: Maximum number of parallel workers.
        item_name: Name of items for logging.

    Returns:
        Dictionary mapping items to results.
    """
    results = {}
    workers = min(max_workers, len(items))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_item = {
            executor.submit(func, item): item
            for item in items
        }

        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                results[item] = future.result()
            except Exception as e:
                print_error(f"Failed to process {item_name} {item}: {e}")
                results[item] = None

    return results


def run_parallel_regions(
    func: Callable[[str], T],
    regions: list[str],
    max_workers: int = 10
) -> dict[str, T]:
    """
    Run a function in parallel for each region.

    Args:
        func: Function to run. Takes region as argument.
        regions: List of AWS regions.
        max_workers: Maximum number of parallel workers.

    Returns:
        Dictionary mapping regions to results.
    """
    return run_parallel(func, regions, max_workers, "region")

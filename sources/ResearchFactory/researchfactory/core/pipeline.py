"""HEP 批量构建和审核共用的有界并发执行器。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Sequence

def parallel_map(
    fn: Callable[[Any], Any],
    items: Sequence[Any],
    *,
    workers: int,
    on_done: Callable[[int, int, Any], None] | None = None,
) -> list[Any]:
    """Run ``fn`` over ``items`` on a thread pool.

    Returns the results in completion order. ``on_done(done, total, result)``
    fires as each future completes — used for progress logging. ``fn`` is
    expected to trap its own errors and return an error-tuple result so that a
    single failed item does not abort the whole map.
    """
    items = list(items)
    total = len(items)
    results: list[Any] = []
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = [ex.submit(fn, it) for it in items]
        for done, fut in enumerate(as_completed(futures), start=1):
            res = fut.result()
            if on_done is not None:
                on_done(done, total, res)
            results.append(res)
    return results


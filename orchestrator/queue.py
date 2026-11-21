"""
Per-Node Task Queue.
Prevents concurrent task collisions on the same physical device.
Tasks for each node are processed sequentially in FIFO order.
"""
import asyncio
import logging
from typing import Callable, Dict, Any, Awaitable

logger = logging.getLogger("fleet-queue")


class PerNodeTaskQueue:
    def __init__(self):
        self.queues: Dict[str, asyncio.Queue] = {}
        self.workers: Dict[str, asyncio.Task] = {}
        self.is_running = True

    def _get_queue(self, node_id: str) -> asyncio.Queue:
        if node_id not in self.queues:
            self.queues[node_id] = asyncio.Queue()
        return self.queues[node_id]

    async def enqueue_task(
        self,
        node_id: str,
        task_fn: Callable[[], Awaitable[Any]],
        description: str = ""
    ) -> Any:
        """
        Enqueue a task for a specific node and await its execution.
        """
        future = asyncio.get_running_loop().create_future()
        q = self._get_queue(node_id)
        await q.put((task_fn, future, description))

        # Ensure worker is started for this node
        if node_id not in self.workers or self.workers[node_id].done():
            self.workers[node_id] = asyncio.create_task(self._node_worker(node_id))

        return await future

    async def _node_worker(self, node_id: str) -> None:
        """Worker loop processing tasks sequentially for a single node."""
        q = self._get_queue(node_id)
        while self.is_running:
            try:
                task_fn, future, desc = await q.get()
                try:
                    res = await task_fn()
                    if not future.done():
                        future.set_result(res)
                except Exception as e:
                    logger.error(f"Error executing queued task on node {node_id} ({desc}): {e}")
                    if not future.done():
                        future.set_exception(e)
                finally:
                    q.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker exception on node {node_id}: {e}")

    async def shutdown(self) -> None:
        self.is_running = False
        for task in self.workers.values():
            task.cancel()

"""
Serial reward queue for long-running redemptions (TTS, Flashbang, Spelling Bee, etc.).

Short SFX should bypass this module and run immediately so they can overlap.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


CoroFactory = Callable[[], Awaitable[Any]]


@dataclass
class QueuedReward:
    action_id: str
    title: str
    user_name: str
    factory: CoroFactory
    enqueued_at: float = field(default_factory=time.time)


class RewardQueue:
    """Process heavy rewards one at a time; support skip of the active item."""

    def __init__(self):
        self._queue: asyncio.Queue[QueuedReward] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._current_task: Optional[asyncio.Task] = None
        self._current: Optional[QueuedReward] = None
        self._skip_requested = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def current(self) -> Optional[QueuedReward]:
        return self._current

    def status_text(self) -> str:
        current = self._current
        if current is None and self.pending_count == 0:
            return "queue empty"
        if current is None:
            return f"queue idle, {self.pending_count} waiting"
        return (
            f"playing {current.title} from {current.user_name}; "
            f"{self.pending_count} waiting"
        )

    async def start(self):
        loop = asyncio.get_running_loop()
        # Module-level singleton can outlive an old loop (e.g. after re-auth / re-run).
        if self._loop is not None and self._loop is not loop:
            self._worker_task = None
            self._current_task = None
            self._current = None
            self._queue = asyncio.Queue()
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._loop = loop
        self._worker_task = asyncio.create_task(self._worker(), name="reward-queue-worker")
        print("🚦 Reward queue started (serial mode for heavy rewards)")

    async def stop(self):
        self.skip_current(reason="shutdown")
        task = self._worker_task
        self._worker_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RuntimeError):
                # RuntimeError: queue/task bound to a different event loop during shutdown
                pass

        # Drain without assuming we own the same loop that created the queue
        try:
            while True:
                self._queue.get_nowait()
                try:
                    self._queue.task_done()
                except ValueError:
                    pass
        except asyncio.QueueEmpty:
            pass
        except RuntimeError:
            # Recreate empty queue for a future loop
            self._queue = asyncio.Queue()

    async def enqueue(
        self,
        *,
        action_id: str,
        title: str,
        user_name: str,
        factory: CoroFactory,
    ) -> int:
        """Add a heavy reward. Always schedules onto the queue's owning loop."""
        item = QueuedReward(
            action_id=action_id,
            title=title,
            user_name=user_name,
            factory=factory,
        )

        loop = self._loop
        if loop is None:
            # Fallback if start() was never called
            await self._queue.put(item)
        else:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            if running is loop:
                await self._queue.put(item)
            else:
                # EventSub / chat callback on another thread/loop → hop to worker loop
                future = asyncio.run_coroutine_threadsafe(self._queue.put(item), loop)
                await asyncio.wrap_future(future)

        position = self.pending_count + (1 if self._current is not None else 0)
        print(
            f"🧾 Queued '{title}' from {user_name} "
            f"(action={action_id}, position≈{position}, waiting={self.pending_count})"
        )
        return position

    def skip_current(self, reason: str = "manual skip") -> bool:
        if self._loop is not None and self._loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not self._loop:
                self._loop.call_soon_threadsafe(self._skip_current_sync, reason)
                return True
        return self._skip_current_sync(reason)

    def _skip_current_sync(self, reason: str) -> bool:
        current = self._current
        task = self._current_task
        if current is None or task is None or task.done():
            print(f"⏭️ Skip requested ({reason}) but nothing is running")
            return False

        self._skip_requested = True
        task.cancel()
        print(
            f"⏭️ Skipping current reward: '{current.title}' "
            f"from {current.user_name} ({reason})"
        )
        try:
            from audio_handler import stop_current_local_playback
            stop_current_local_playback()
        except Exception:
            pass
        return True

    async def _worker(self):
        while True:
            item = await self._queue.get()
            self._current = item
            self._skip_requested = False
            print(
                f"▶️ Starting queued reward: '{item.title}' "
                f"from {item.user_name} (action={item.action_id})"
            )
            self._current_task = asyncio.create_task(
                item.factory(), name=f"reward-{item.action_id}"
            )
            try:
                await self._current_task
                if self._skip_requested:
                    print(f"⏹️ Queued reward skipped: '{item.title}'")
                else:
                    print(f"✅ Queued reward finished: '{item.title}'")
            except asyncio.CancelledError:
                print(f"⏹️ Queued reward cancelled: '{item.title}'")
            except Exception as error:
                print(f"❌ Queued reward failed '{item.title}': {error}")
            finally:
                self._current_task = None
                self._current = None
                self._skip_requested = False
                self._queue.task_done()


reward_queue = RewardQueue()

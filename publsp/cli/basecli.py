import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Coroutine

from publsp.nostr.client import NostrClient
from publsp.nostr.nip17 import RumorHandler, Nip17Listener
from publsp.settings import PublspSettings

import logging
logger = logging.getLogger(name=__name__)


class BaseCLI(ABC):
    _running: bool = True
    nostr_client: NostrClient
    rumor_handler: RumorHandler
    nip17_listener: Nip17Listener

    def __init__(self, **kwargs):
        pass

    @abstractmethod
    def startup(self) -> Coroutine[None, None, None]:
        pass

    @abstractmethod
    def shutdown(self) -> Coroutine[None, None, None]:
        pass


class HotReloader:
    def __init__(self):
        self._env_file_mtime = None
        self._env_watcher_task = None

    def _get_env_file_mtime(self, file_path: str) -> float:
        """Get the modification time of the .env file."""
        try:
            return os.path.getmtime(file_path)
        except (OSError, FileNotFoundError):
            return 0.0

    async def _watch_env_file(self):
        """Watch for changes to the .env file and trigger hot reload."""
        # Use the PublspSettings class to determine which env file to watch
        env_file_path = PublspSettings().env_file
        file_path = Path(env_file_path)

        logger.info(f"Watching {file_path.as_posix()} for changes...")

        try:
            last_modified = file_path.stat().st_mtime if file_path.exists() else 0

            while not self.shutdown_event.is_set():
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=2.0)
                    break  # Shutdown was triggered
                except asyncio.TimeoutError:
                    pass  # Continue checking

                # Check if file was modified
                current_modified = file_path.stat().st_mtime if file_path.exists() else 0
                if current_modified > last_modified:
                    logger.info(f"Detected changes in {file_path.as_posix()}")
                    last_modified = current_modified
                    await self.nostr_client.reload_relays()
                    self.ad_handler = await self.ad_handler.reload()
                    self.order_handler.ad_handler = self.ad_handler
                    self._render_active_ad()

        except asyncio.CancelledError:
            logger.info("Env file watcher cancelled")
        except Exception as e:
            logger.error(f"Error in env file watcher: {e}")

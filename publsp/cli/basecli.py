from abc import ABC, abstractmethod
from typing import Coroutine

from publsp.nostr.client import NostrClient
from publsp.nostr.nip17 import RumorHandler, Nip17Listener


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

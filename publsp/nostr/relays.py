from dataclasses import dataclass, field
from typing import List
from urllib.parse import urlparse

from publsp.settings import Environment, EnvironmentSettings, NostrSettings


@dataclass
class Relays:
    prod: List[str] = field(default_factory=lambda: NostrSettings().nostr_relays)
    dev: List[str] = field(default_factory=lambda: NostrSettings().dev_relays)

    def _is_valid_websocket_url(self, url: str) -> bool:
        try:
            result = urlparse(url)
            return result.scheme in ['ws', 'wss'] and bool(result.netloc)
        except ValueError:
            return False

    """
    def add_relay(
            self,
            relay: str,
            env: Environment = EnvironmentSettings().environment) -> None:
        if self._is_valid_websocket_url(relay):
            if env == Environment.PROD:
                self.prod.append(relay)
            else:
                self.dev.append(relay)
    """

    def get_relays(self, env: Environment = EnvironmentSettings().environment) -> List[str]:
        env_map = {
            Environment.PROD: self.prod,
            Environment.DEV: self.dev
        }
        return env_map.get(env)

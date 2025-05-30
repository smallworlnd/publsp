from nostr_sdk import (
    Client,
    EventBuilder,
    Kind,
    NostrSigner,
    Tag,
)

from publsp.nostr.keyhandler import KeyHandler
from publsp.nostr.relays import Relays
from publsp.settings import Environment, EnvironmentSettings, NostrSettings

import logging

logger = logging.getLogger(name=__name__)


class NostrClient(Client):
    def __init__(
            self,
            client_for='lsp',
            write_keys: bool = NostrSettings().write_keys,
            reuse_keys: bool = NostrSettings().reuse_keys,
            ask_encrypt: bool = NostrSettings().ask_encrypt):
        self.client_for = client_for
        self.key_handler = KeyHandler(
            client=client_for,
            reuse_keys=reuse_keys,
            write_keys=write_keys,
            ask_encrypt=ask_encrypt)
        self.signer = NostrSigner.keys(self.key_handler.keys)
        super().__init__(self.signer)

    def build_event(self, tags: [Tag], content: str, kind: Kind):
        # build the event with the kind, content, tags and sign with keys
        builder = EventBuilder(kind, content).tags(tags)
        return builder.sign_with_keys(self.key_handler.keys)

    async def connect_relays(self, env: Environment = EnvironmentSettings().environment) -> None:
        # Add relays and connect
        for relay in Relays().get_relays(env=env):
            await self.add_relay(relay)

        await self.connect()

    async def disconnect_relays(self, env: Environment = EnvironmentSettings().environment) -> None:
        for relay in Relays().get_relays(env=env):
            await self.disconnect_relay(relay)

    def get_npub(self) -> str:
        return self.key_handler.keys.public_key().to_bech32()

    def get_public_key_hex(self) -> str:
        return self.key_handler.keys.public_key().to_hex()

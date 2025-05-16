import asyncio
from functools import partial
import click
from typing import Callable, Awaitable

from publsp.cli.basecli import BaseCLI
from publsp.ln.lnd import LndBackend
# from publsp.ln.cln import ClnBackend  # not yet implemented
from publsp.nostr.client import NostrClient
from publsp.nostr.nip17 import RumorHandler, Nip17Listener
from publsp.marketplace.lsp import AdHandler, OrderHandler
from publsp.settings import CustomAdSettings, LnImplementation

import logging
logger = logging.getLogger(name=__name__)


async def async_prompt(text: str) -> str:
    """Run click.prompt in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(click.prompt, text)


class LspCLI(BaseCLI):
    def __init__(self, **kwargs):
        # state
        self._running = True

        # core services
        rest_host = kwargs.get('rest_host')
        permissions_file_path = kwargs.get('permissions_file_path')
        cert_file_path = kwargs.get('cert_file_path')
        ln_backend = kwargs.get('node')
        if ln_backend == LnImplementation.LND:
            self.ln_backend = LndBackend(
                rest_host=rest_host,
                permissions_file_path=permissions_file_path,
                cert_file_path=cert_file_path
            )
        else:
            raise NotImplementedError

        reuse_keys = kwargs.get("reuse_keys")
        self.nostr_client = NostrClient(client_for="lsp", reuse_keys=reuse_keys)
        self.rumor_handler = RumorHandler()
        self.nip17_listener = Nip17Listener(
            nostr_client=self.nostr_client,
            rumor_handler=self.rumor_handler,
        )
        self.ad_handler = AdHandler(
            nostr_client=self.nostr_client,
            ln_backend=self.ln_backend,
            **kwargs,
        )
        self.order_handler = OrderHandler(
            ln_backend=self.ln_backend,
            ad_handler=self.ad_handler,
            rumor_handler=self.rumor_handler,
            nostr_client=self.nostr_client,
        )

        # menu command registry: key -> (description, coroutine handler)
        msg = kwargs.get('value_prop')
        marketing_content = msg if msg else CustomAdSettings().value_prop
        self.commands: dict[str, tuple[str, Callable[[], Awaitable[None]]]] = {
            "1": (
                "Publish ad",
                partial(self.cmd_publish_ad, marketing_content),
            ),
            "2": ("View active ad", self.cmd_view_ad),
            "3": ("Inactivate ads", self.cmd_update_ad),
            "4": ("Exit", self.cmd_exit),
        }

    async def startup(self) -> None:
        """Connect relays and start background listeners."""
        await self.nostr_client.connect_relays()
        self.nip17_listener.start()
        self.order_handler.start()

    async def shutdown(self) -> None:
        """Tear down ads, listeners, relays, then exit."""
        await self.ad_handler.update_ad_events()
        await self.order_handler.stop()
        await self.nip17_listener.stop()
        await self.nostr_client.disconnect_relays()

    def _render_menu(self) -> None:
        menu = "\nChoose an option:\n"
        for key, (desc, _) in self.commands.items():
            menu += f"  {key}. {desc}\n"
        click.echo(menu)

    # ------------------------------------------
    # Command handlers
    # ------------------------------------------

    async def cmd_publish_ad(self, content: str = '') -> None:
        await self.ad_handler.publish_ad(content=content)
        click.echo("\nPublished ad:")
        self._render_active_ad()

    async def cmd_view_ad(self) -> None:
        self._render_active_ad()

    async def cmd_update_ad(self) -> None:
        await self.ad_handler.update_ad_events()
        click.echo("\nAds updated to inactive")

    async def cmd_exit(self) -> None:
        click.echo("Exiting...")
        self._running = False

    # ------------------------------------------
    # Helpers
    # ------------------------------------------

    def _render_active_ad(self) -> None:
        if self.ad_handler.active_ads:
            click.echo(self.ad_handler.active_ads)
        else:
            click.echo("\nNo active ads")

    # ------------------------------------------
    # Main loop
    # ------------------------------------------

    async def run(self) -> None:
        await self.startup()

        try:
            while self._running:
                self._render_menu()
                choice = await async_prompt("Choice (1-4)")

                handler_entry = self.commands.get(choice)
                if handler_entry:
                    _, handler = handler_entry
                    try:
                        await handler()
                    except Exception as exc:
                        logger.error(f"Command {choice} failed: {exc}")
                else:
                    click.echo("Invalid choice, please enter 1-4")

        except KeyboardInterrupt:
            logger.info("Interrupted, shutting down...")
        finally:
            await self.shutdown()
            raise SystemExit(0)


async def run_lsp_cli(**kwargs):
    cli = LspCLI(**kwargs)
    await cli.run()

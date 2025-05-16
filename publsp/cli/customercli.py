import asyncio
import click
import sys
import logging
from typing import Awaitable, Callable, List

from publsp.cli.basecli import BaseCLI
from publsp.nostr.client import NostrClient
from publsp.nostr.nip17 import RumorHandler, Nip17Listener
from publsp.marketplace.customer import CustomerHandler, OrderResponseHandler

logger = logging.getLogger(name=__name__)


async def async_prompt(text: str) -> str:
    """Run click.prompt in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(click.prompt, text)


async def pick_from_list(
    prompt_text: str,
    choices: List[str],
    default_index: int = 0,
) -> str:
    """
    Prompt the user with a list of choices using click.prompt and click.Choice.
    """
    return await asyncio.to_thread(
        click.prompt,
        prompt_text,
        type=click.Choice(choices),
        default=choices[default_index],
    )


class CustomerCLI(BaseCLI):
    def __init__(self, **kwargs):
        # reactor state
        self._running = True

        # core components
        reuse_keys = kwargs.get("reuse_keys")
        self.nostr_client = NostrClient(client_for="customer", reuse_keys=reuse_keys)
        self.rumor_handler = RumorHandler()
        self.nip17_listener = Nip17Listener(
            nostr_client=self.nostr_client,
            rumor_handler=self.rumor_handler,
        )
        self.customer_handler = CustomerHandler(
            nostr_client=self.nostr_client,
            **kwargs,
        )
        self.order_response_handler = OrderResponseHandler(
            customer_handler=self.customer_handler,
            rumor_handler=self.rumor_handler,
            **kwargs,
        )

        # menu → (description, coroutine)
        self.commands: dict[str, tuple[str, Callable[[], Awaitable[None]]]] = {
            "1": ("Show discovered ads", self.cmd_show_ads),
            "2": ("Get liquidity cost breakdown", self.cmd_summarize_costs),
            "3": ("Request a channel", self.cmd_request_channel),
            "4": ("Exit", self.cmd_exit),
        }

    async def startup(self) -> None:
        """Connect relays, fetch ads, and start NIP-17 listener."""
        await self.nostr_client.connect_relays()
        await self.customer_handler.get_ad_info()
        self.nip17_listener.start()
        self.order_response_handler.start()

    async def shutdown(self) -> None:
        """Stop listeners, disconnect, then exit."""
        await self.nip17_listener.stop()
        await self.order_response_handler.stop()
        await self.nostr_client.disconnect_relays()
        # finally terminate the process for real
        sys.exit(0)

    def _render_menu(self) -> None:
        text = "\nChoose an option:\n"
        for key, (desc, _) in self.commands.items():
            text += f"  {key}. {desc}\n"
        click.echo(text)

    # ------------------------------------------
    # Command handlers
    # ------------------------------------------

    async def cmd_show_ads(self) -> None:
        ads = self.customer_handler.active_ads
        if ads and ads.ads:
            click.echo(ads)
        else:
            click.echo("\nNo ads discovered yet.")

    async def cmd_show_ads_short(self) -> None:
        ads = self.customer_handler.active_ads
        if ads and ads.ads:
            click.echo("\nAvailable offers:")
            opts = self.customer_handler.options
            capacity = opts.get('lsp_balance_sat') + opts.get('client_balance_sat')
            click.echo(
                self.customer_handler.summarise_channel_prices(capacity=capacity)
            )
        else:
            click.echo("\nNo ads discovered")

    async def cmd_summarize_costs(self) -> None:
        while True:
            click.echo(
                "\nCost summary:\n"
                "  1. Enter desired capacity (sats)\n"
                "  2. Back\n"
                "  3. Exit\n"
            )
            c = await async_prompt("Choice (1-3)")
            if c == "1":
                cap = await async_prompt("Capacity (sats): ")
                try:
                    summary = self.customer_handler.summarise_channel_prices(
                        capacity=int(cap)
                    )
                    click.echo(str(summary))
                except ValueError:
                    click.echo("Please enter a valid integer.")
            elif c == "2":
                return
            elif c == "3":
                await self.cmd_exit()
                return
            else:
                click.echo("Invalid choice, enter 1-3.")

    async def cmd_request_channel(self) -> None:
        ads = self.customer_handler.active_ads
        if not (ads and ads.ads):
            click.echo("No ads available.")
            return

        ad_ids = list(ads.ads.keys())

        await self.cmd_show_ads_short()

        # replace arrow-list selector with click.Choice
        choice = await pick_from_list(
            "Select Ad ID:",
            ad_ids
        )

        if choice not in ads.ads:
            click.echo(f"Ad ID {choice!r} not found.")
            return

        ad = ads.ads[choice]
        order = self.customer_handler.build_order(ad_id=choice)
        expected_cost = int(
            ad.fixed_cost_sats + ad.variable_cost_ppm * 1e-6 * order.total_capacity
        )
        peer_pk = ads.get_nostr_pubkey(ad_id=choice, as_PublicKey=True)
        click.echo(
            f"\nRequesting channel of {order.total_capacity} sats\n"
            f"From {ad.lsp_pubkey} (ad ID: {choice})\n"
            f"Target node: {order.target_pubkey_uri}\n"
            f"Outbound sats: {order.client_balance_sat}, "
            f"Inbound sats: {order.lsp_balance_sat}\n"
            f"Expected order cost: {expected_cost} sats\n"
        )
        ok = await async_prompt("Confirm? [y/n]: ")
        if ok.lower() not in ("y", "yes"):
            click.echo("Cancelled.")
            return
        # register the selected ad
        self.order_response_handler.selected_ad = ad
        # send a dm with the order request for the selected ad
        await self.nostr_client.send_private_msg(
            peer_pk,
            "order request",
            rumor_extra_tags=order.model_dump_tags(),
        )
        click.echo("Order request sent.")

    async def cmd_exit(self) -> None:
        click.echo("Exiting...")
        self._running = False

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


async def run_customer_cli(**kwargs):
    cli = CustomerCLI(**kwargs)
    await cli.run()

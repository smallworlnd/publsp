import sys
import asyncio

import click
from pydantic import ValidationError

from publsp.cli.customercli import run_customer_cli
from publsp.cli.helpers import format_errors
from publsp.settings import (
    OrderSettings,
    CustomerSettings,
)


# --- CUSTOMER SUBCOMMAND --------------------------------------------
@click.command("customer", help="Search and request liquidity as a customer")
@click.option(
    "--target-pubkey-uri",
    "target_pubkey_uri",
    type=str,
    required=True,
    default=OrderSettings().target_pubkey_uri,
    show_default=True,
    help="pubkey@host:port of customer node receiving liquidity"
)
@click.option(
    "--token",
    "token",
    type=str,
    required=False,
    default=OrderSettings().token,
    show_default=True,
    help="coupon code (if any)"
)
@click.option(
    "--lsp-balance",
    "lsp_balance_sat",
    type=int,
    required=False,
    default=OrderSettings().lsp_balance_sat,
    show_default=True,
    help="desired inbound sats"
)
@click.option(
    "--local-balance",
    "client_balance_sat",
    type=int,
    required=False,
    default=OrderSettings().client_balance_sat,
    show_default=True,
    help="desired outbound sats"
)
@click.option(
    "--announce-channel",
    "announce_channel",
    type=bool,
    required=False,
    default=OrderSettings().announce_channel,
    show_default=True,
    help="whether to publicly announce the channel"
)
@click.option(
    "--req-chan-confs",
    "required_channel_confirmations",
    type=int,
    required=False,
    default=OrderSettings().required_channel_confirmations,
    show_default=True,
    help="confirms required before channel_ready"
)
@click.option(
    "--funding-confs",
    "funding_confirms_within_blocks",
    type=int,
    required=False,
    default=OrderSettings().funding_confirms_within_blocks,
    show_default=True,
    help="max blocks to wait for funding confirm"
)
@click.option(
    "--chan-expiry",
    "channel_expiry_blocks",
    type=int,
    required=False,
    default=OrderSettings().channel_expiry_blocks,
    show_default=True,
    help="lease duration in blocks"
)
@click.option(
    "--onchain-refund-addr",
    "refund_onchain_address",
    type=str,
    required=False,
    default=OrderSettings().refund_onchain_address,
    show_default=True,
    help="on-chain refund address (if desired/supported)"
)
@click.option(
    "--reuse-nostr-keys",
    "reuse_keys",
    is_flag=True,
    default=False,
    help="Use this flag to reuse existing Nostr keys under "
    "output/nostr-keys.json. Default is to "
    "automatically regenerate new keys each time publsp is launched "
    "for improved privacy.",
)
def customerargs(**kwargs):
    """
    Launch the interactive Customer REPL with all order‐building parameters.
    """
    try:
        settings = CustomerSettings(**kwargs)
    except ValidationError as e:
        click.secho(format_errors(e), fg="red", err=True)
        sys.exit(1)

    asyncio.run(run_customer_cli(**settings.model_dump()))

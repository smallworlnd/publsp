import asyncio
import click
import sys

from publsp.cli.lspcli import run_lsp_cli
from publsp.cli.helpers import format_errors
from publsp.settings import (
    AdSettings,
    CustomAdSettings,
    LnBackendSettings,
    LnImplementation,
    LspSettings,
)
from pydantic import ValidationError


# --- lsp subcommand -----------
@click.command("lsp", help="Publish, manage and handle orders as an LSP")
# --- ln backend -----------
@click.option(
    "--node",
    "node",
    type=click.Choice(LnImplementation.choices(), case_sensitive=False),
    default=None,
    help="which LN implementation"
)
@click.option(
    "--rest-host",
    'rest_host',
    type=str,
    default=None,
    help="REST endpoint (ip:port or http(s)://ip:port)"
)
@click.option(
    "--permissions-file-path",
    'permissions_file_path',
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="path to macaroon/rune"
)
@click.option(
    "--cert-file-path",
    'cert_file_path',
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="path to tls cert"
)
# --- ad settings -----------
@click.option(
    "--value-proposition",
    'value_prop',
    type=str,
    metavar="'MESSAGE HERE'",
    default=CustomAdSettings().value_prop,
    show_default=True,
    help="your value proposition to distinguish your ad from others"
)
# --- chain settings -----------
@click.option(
    "--min-req-chan-confs",
    "min_required_channel_confirmations",
    type=int,
    default=AdSettings().min_required_channel_confirmations,
    show_default=True,
    help="min confirmations before channel_ready from LSP"
)
@click.option(
    "--min-funding-confs",
    "min_funding_confirms_within_blocks",
    type=int,
    default=AdSettings().min_funding_confirms_within_blocks,
    show_default=True,
    help="max blocks to confirm funding tx"
)
@click.option(
    "--zero-reserve",
    "supports_zero_channel_reserve",
    is_flag=True,
    default=AdSettings().supports_zero_channel_reserve,
    help="allow zero reserve channels (currently not implemented so has no effect"
)
@click.option(
    "--no-private-channels",
    is_flag=True,
    help="use this option to refuse private channels, otherwise default "
    "behavior is to accept private channels"
)
@click.option(
    "--max-channel-expiry",
    "max_channel_expiry_blocks",
    type=int,
    default=AdSettings().max_channel_expiry_blocks,
    show_default=True,
    help="max time in blocks the channel can be leased for"
)
# --- channel settings -----------
@click.option(
    "--min-client-bal",
    "min_initial_client_balance_sat",
    type=int,
    default=AdSettings().min_initial_client_balance_sat,
    show_default=True,
    help="min sats the *client* can start with"
)
@click.option(
    "--max-client-bal",
    "max_initial_client_balance_sat",
    type=int,
    default=AdSettings().max_initial_client_balance_sat,
    show_default=True,
    help="max sats the *client* can start with"
)
@click.option(
    "--min-lsp-bal",
    "min_initial_lsp_balance_sat",
    type=int,
    default=AdSettings().min_initial_lsp_balance_sat,
    show_default=True,
    help="min sats LSP must hold"
)
@click.option(
    "--max-lsp-bal",
    "max_initial_lsp_balance_sat",
    type=int,
    default=AdSettings().max_initial_lsp_balance_sat,
    show_default=True,
    help="max sats LSP must hold"
)
@click.option(
    "--min-capacity",
    "min_channel_balance_sat",
    type=int,
    default=AdSettings().min_channel_balance_sat,
    show_default=True,
    help="minimum channel size"
)
@click.option(
    "--max-capacity",
    "max_channel_balance_sat",
    type=int,
    default=AdSettings().max_channel_balance_sat,
    show_default=True,
    help="maximum channel size"
)
# --- fee settings -----------
@click.option(
    "--fixed-cost",
    "fixed_cost_sats",
    type=int,
    default=AdSettings().fixed_cost_sats,
    show_default=True,
    help="flat sats fee to open channel"
)
@click.option(
    "--dynamic-fixed-cost",
    "dynamic_fixed_cost",
    is_flag=True,
    help="use this flag to dynamically set the fixed cost based on chain fees "
    "instead of setting a value for --fixed-cost. see the .env.example for "
    "ways to modify the formula for calculating dynamic fixed costs"
)
@click.option(
    "--sum-of-utxos-as-max-capacity",
    "sum_utxos_as_max_capacity",
    is_flag=True,
    help="use this flag to dynamically set the max channel capacity as the "
    "sum of utxos in the wallet (less reserve and on-chain fees)"
)
@click.option(
    "--variable-cost",
    "variable_cost_ppm",
    type=int,
    default=AdSettings().variable_cost_ppm,
    show_default=True,
    help="variable fee in ppm per year of capacity"
)
@click.option(
    "--max-promised-fee-rate",
    "max_promised_fee_rate",
    type=int,
    default=AdSettings().max_promised_fee_rate,
    show_default=True,
    help="max promised fee rate"
)
@click.option(
    "--max-promised-base-fee",
    "max_promised_base_fee",
    type=int,
    default=AdSettings().max_promised_base_fee,
    show_default=True,
    help="max promised base fee"
)
@click.option(
    "--daemon",
    "daemon",
    is_flag=True,
    default=LspSettings().daemon,
    help="run publsp in daemon mode to skip the interactive menu, useful for "
    "automating publsp"
)
@click.option(
    "--include-node-sig",
    "include_node_sig",
    is_flag=True,
    default=LspSettings().include_node_sig,
    help="sign your nostr pubkey with your ln node and include it in your ad "
    "for clients to verify authenticity (may be helpful in a future where "
    "spam and scams become prevalent)"
)
@click.option(
    "--lease-history-file-path",
    'lease_history_file_path',
    type=click.Path(exists=False, dir_okay=False),
    default=LspSettings().lease_history_file_path,
    show_default=True,
    help="file path to record successful channel lease information"
)
def lspargs(**kwargs):
    """
    Launch the interactive LSP REPL with all LN + Ad configuration.
    """
    # Try to build a partial settings object so we can pick up .env for missing ones
    try:
        # 1) Only hand Pydantic the CLI values that are not None, so
        # .env/defaults fill in the rest.
        init = {k: v for k, v in kwargs.items() if v is not None}
        settings = LspSettings(**init)
    except ValidationError as e:
        click.secho(format_errors(e), fg="red", err=True)
        sys.exit(1)

    # 2) Enforce that these four truly exist (CLI or .env)
    missing = []
    for field in ("node", "rest_host", "permissions_file_path", "cert_file_path"):
        if getattr(settings, field) is None:
            missing.append(field)

    if missing:
        names = ", ".join(f"--{f.replace('_','-')}" for f in missing)
        raise click.UsageError(
            f"Missing required parameters (either via CLI or .env): {names}"
        )

    # check for the no-private-channels flag
    if kwargs.get('no_private_channels'):
        settings.supports_private_channels = False

    # 3) Fire up the CLI
    try:
        asyncio.run(run_lsp_cli(**settings.model_dump()))
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        click.echo("\nShutdown complete.", err=True)
        sys.exit(0)
    except SystemExit:
        # Re-raise SystemExit to maintain expected behavior
        raise
    except Exception as e:
        click.secho(f"Failed to run LSP: {e}", fg="red", err=True)
        sys.exit(1)

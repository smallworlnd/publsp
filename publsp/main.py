import click

from publsp.cli.lspargs import lspargs
from publsp.cli.customerargs import customerargs
from publsp.cli.logger import LoggerSetup
from publsp.settings import LogLevel, NostrSettings, PublspSettings

LOG_LEVELS = [lvl.value.lower() for lvl in LogLevel]


@click.group(
    context_settings={
        "help_option_names": ["-h", "--help"],
        "max_content_width": 120,
        "terminal_width": 120,
    }
)
@click.option(
    "--log-level",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    default=PublspSettings().log_level.value.lower(),
    show_default=True,
    help="logging level, e.g. DEBUG, info, WaRnInG, etc.",
)
@click.option(
    "--reuse-keys",
    "reuse_keys",
    is_flag=True,
    default=NostrSettings().reuse_keys,
    help="reuse nostr keys generated from a previous session, new keys will be"
    " automatically generated without this option"
)
@click.option(
    "--write-keys",
    "write_keys",
    is_flag=True,
    default=NostrSettings().write_keys,
    help="use this option to write newly generated nostr keys to file, default"
    " is output/nostr-keys.json"
)
@click.option(
    "--encrypt-keys",
    "encrypt_keys",
    is_flag=True,
    default=NostrSettings().encrypt_keys,
    help="use this option to encrypt the nsec when writing keys to file"
)
@click.pass_context
def cli(ctx, log_level, reuse_keys, write_keys, encrypt_keys):
    """
    publsp — a tool for finding/offering Lightning Network liquidity over Nostr
    """
    level_enum = LogLevel[log_level.upper()]
    LoggerSetup(level_enum).setup_logging()

    ctx.ensure_object(dict)
    ctx.obj["log_level"] = level_enum
    ctx.obj["reuse_keys"] = reuse_keys
    ctx.obj["write_keys"] = write_keys
    ctx.obj["encrypt_keys"] = encrypt_keys


def register_commands(group: click.Group):
    group.add_command(lspargs)
    group.add_command(customerargs)


def main():
    # register our two subcommands
    register_commands(cli)
    # invoke the click group
    cli()


if __name__ == "__main__":
    main()

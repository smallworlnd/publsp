import click

from publsp.cli.lspargs import lspargs
from publsp.cli.customerargs import customerargs
from publsp.cli.logger import LoggerSetup
from publsp.settings import LogLevel, PublspSettings

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
@click.pass_context
def cli(ctx, log_level):
    """
    publsp — a tool for finding/offering Lightning Network liquidity over Nostr
    """
    level_enum = LogLevel[log_level.upper()]
    LoggerSetup(level_enum).setup_logging()

    ctx.ensure_object(dict)
    ctx.obj["log_level"] = level_enum


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

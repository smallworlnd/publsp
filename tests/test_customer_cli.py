from click.testing import CliRunner
from publsp.main import cli, register_commands


def test_lsp_exit_immediately():
    runner = CliRunner()
    register_commands(cli)
    result = runner.invoke(
        cli,
        ["customer"],
        input="1\n4\n",
        catch_exceptions=False
    )
    output = result.output
    assert 'Exiting...' in output

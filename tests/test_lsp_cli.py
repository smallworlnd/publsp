from click.testing import CliRunner
from publsp.main import cli, register_commands


def test_lsp_exit_immediately():
    runner = CliRunner()
    register_commands(cli)
    result = runner.invoke(
        cli,
        ["lsp"],
        input="1\n2\n3\n4\n",
        catch_exceptions=False
    )
    output = result.output
    assert '29cff27c-ec05-b50b-fc6c-0a2ca3063d6e' in output
    assert 'Min required channel confirmations' in output
    assert 'Exiting...' in output

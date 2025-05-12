import pytest


@pytest.mark.asyncio
async def test_nostr_relays_connect(connected_lsp_nostr_client):
    relays, client = connected_lsp_nostr_client
    print(f'relays: {relays}\nclient: {client}')
    if not all(r.is_connected() for r in relays):
        pytest.exit('could not connect to local nostr relay, halting tests', returncode=1)


@pytest.mark.asyncio
async def test_lnd_backend(lsp_lnd_client):
    if not lsp_lnd_client:
        pytest.exit(
            'could not connect to lnd backend, halting tests',
            pytrace=False
        )

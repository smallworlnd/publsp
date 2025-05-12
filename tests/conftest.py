import asyncio
import os
import pytest
import pytest_asyncio
from pydantic import ValidationError

from publsp.ln.lnd import LndBackend
from publsp.marketplace.lsp import AdHandler, OrderHandler
from publsp.nostr.nip17 import RumorHandler, Nip17Listener
from publsp.nostr.client import NostrClient
from publsp.settings import AdSettings, Environment, LnBackendSettings, NostrSettings

os.environ["ENVIRONMENT"] = "dev"


@pytest.fixture(scope="session")
def lsp_nostr_client():
    return NostrClient(client_for='lsp')


@pytest.fixture(scope="session")
def customer_nostr_client():
    return NostrClient(client_for='customer')


@pytest_asyncio.fixture(loop_scope='session')
async def lsp_lnd_client():
    try:
        return LndBackend(
            rest_host=LnBackendSettings().rest_host.unicode_string(),
            permissions_file_path=LnBackendSettings().permissions_file_path.as_posix(),
            cert_file_path=LnBackendSettings().cert_file_path.as_posix()
        )
    except ValidationError:
        pytest.exit('could not connect to lnd backend, halting tests', returncode=1)


@pytest_asyncio.fixture(loop_scope='session')
async def customer_lnd_client():
    try:
        customer_settings = LnBackendSettings(
            node='lnd',
            rest_host='https://127.0.0.1:8083',
            permissions_file_path='~/.polar/networks/1/volumes/lnd/carol/data/chain/bitcoin/regtest/admin.macaroon',
            cert_file_path='~/.polar/networks/1/volumes/lnd/carol/tls.cert'
        )
        return LndBackend(
            rest_host=customer_settings.rest_host.unicode_string(),
            permissions_file_path=customer_settings.permissions_file_path.as_posix(),
            cert_file_path=customer_settings.cert_file_path.as_posix()
        )
    except ValidationError:
        pytest.exit('could not connect to lnd backend, halting tests', returncode=1)


@pytest_asyncio.fixture(scope='session')
async def connected_lsp_nostr_client(lsp_nostr_client):
    client: NostrClient = lsp_nostr_client
    await client.connect_relays(env=Environment.DEV)
    relays = await asyncio.gather(*[
        client.relay(url)
        for url in NostrSettings().dev_relays])
    yield relays, client


@pytest_asyncio.fixture(scope='session')
async def connected_customer_nostr_client(customer_nostr_client):
    client: NostrClient = customer_nostr_client
    await client.connect_relays(env=Environment.DEV)
    relays = await asyncio.gather(*[
        client.relay(url)
        for url in NostrSettings().dev_relays])
    yield relays, client


@pytest_asyncio.fixture
async def ad_handler(lsp_nostr_client, lsp_lnd_client):
    return AdHandler(
        nostr_client=lsp_nostr_client,
        ln_backend=lsp_lnd_client,
        **AdSettings().model_dump(),
    )


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def lsp_rumor_handler():
    return RumorHandler()


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def customer_rumor_handler():
    return RumorHandler()


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def lsp_nip17_listener(lsp_nostr_client, lsp_rumor_handler):
    return Nip17Listener(
        nostr_client=lsp_nostr_client,
        rumor_handler=lsp_rumor_handler
    )


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def customer_nip17_listener(customer_nostr_client, customer_rumor_handler):
    return Nip17Listener(
        nostr_client=customer_nostr_client,
        rumor_handler=customer_rumor_handler
    )


@pytest_asyncio.fixture
async def order_handler(lsp_lnd_client, ad_handler, lsp_rumor_handler, lsp_nostr_client):
    return OrderHandler(
        ln_backend=lsp_lnd_client,
        ad_handler=ad_handler,
        rumor_handler=lsp_rumor_handler,
        nostr_client=lsp_nostr_client
    )

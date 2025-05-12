import json
import pytest

from publsp.ln.requesthandlers import Preimage


@pytest.mark.asyncio
async def test_check_node_connection(lsp_lnd_client):
    conn = await lsp_lnd_client.check_node_connection()
    assert conn.healthy
    assert conn.synced_to_chain
    assert conn.synced_to_graph
    assert not conn.error_message


@pytest.mark.asyncio
async def test_get_node_id(lsp_lnd_client):
    info = await lsp_lnd_client.get_node_id()
    assert info.pubkey == '0270088f0411fd71c2ef1e0fc6116d408069c504ed71966330721370d87be3f449'
    assert info.alias == 'alice'
    assert not info.error_message


@pytest.mark.asyncio
async def test_get_node_properties(
        lsp_lnd_client,
        pubkey='0270088f0411fd71c2ef1e0fc6116d408069c504ed71966330721370d87be3f449'):
    prop = await lsp_lnd_client.get_node_properties(pubkey=pubkey)
    assert not prop.error_message
    assert prop.total_capacity == 1000000
    assert prop.num_channels == 1
    assert prop.median_outbound_ppm == 1
    assert prop.median_inbound_ppm == 10


@pytest.mark.asyncio
async def test_create_and_settle_hodl_inv(lsp_lnd_client, customer_lnd_client):
    preimage = Preimage.generate()
    inv = await lsp_lnd_client.create_hodl_invoice(
        base64_hash=preimage.base64_hash,
        amt=10)
    assert not inv.error_message
    assert inv.created
    assert inv.inv_hash
    assert inv.payment_request.startswith('ln')
    assert inv.expiry == 1200

    # create client lnd here to pay invoice then settle
    params = {'payment_request': inv.payment_request, 'fee_limit_sat': '1000', 'timeout_seconds': 15}
    async with customer_lnd_client.http_client.stream("POST", "/v2/router/send", json=params, timeout=15) as r:
        async for json_line in r.aiter_lines():
            line = json.loads(json_line)
            if line.get('result'):
                if line.get('result').get('status') == 'SUCCEEDED':
                    break
    await lsp_lnd_client.settle_hodl_invoice(preimage.base64)

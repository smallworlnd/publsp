import asyncio
import json
import sys

from publsp.ln.lnd import LndBackend
from publsp.settings import LnBackendSettings


async def main():
    customer_settings = LnBackendSettings(
        node='lnd',
        rest_host='https://127.0.0.1:8083',
        permissions_file_path='~/.polar/networks/1/volumes/lnd/carol/data/chain/bitcoin/regtest/admin.macaroon',
        cert_file_path='~/.polar/networks/1/volumes/lnd/carol/tls.cert'
    )
    lnd = LndBackend(
        rest_host=customer_settings.rest_host.unicode_string(),
        permissions_file_path=customer_settings.permissions_file_path.as_posix(),
        cert_file_path=customer_settings.cert_file_path.as_posix()
    )

    inv = sys.argv[-1]

    r = await lnd.http_client.post(
        url="/v1/channels/transactions",
        json={"payment_request": inv, "fee_limit": {"fixed_msat": '100000'}},
        timeout=None,
    )
    print("Invoice paid")


if __name__ == "__main__":
    asyncio.run(main())

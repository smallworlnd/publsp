import codecs
import httpx
import json
import logging
import statistics
from typing import Any, AsyncIterator, Dict

from publsp.blip51.order import Order
from publsp.ln.base import NodeBase, Utxo, UtxoOutpoint
from publsp.ln.requesthandlers import (
    ChannelState,
    ChannelOpenResponse,
    ConnectPeerResponse,
    HodlInvoiceResponse,
    HodlInvoiceState,
    GetBestBlockResponse,
    GetNodePropertyResponse,
    GetNodeIdResponse,
    GetUtxosResponse,
    PaymentStatus,
    NodeStatusResponse,
    SignMessageResponse,
    WalletReserveResponse,
)

logger = logging.getLogger(name=__name__)
GetUtxosResponse.model_rebuild()


class LndBackend(NodeBase):
    def __init__(
            self,
            rest_host: str,
            permissions_file_path: str,
            cert_file_path: str):

        self.rest_host = rest_host
        self.macaroon_path = permissions_file_path
        self.macaroon = codecs.encode(
            open(self.macaroon_path, 'rb').read(), 'hex'
        )
        self.headers = {'Grpc-Metadata-macaroon': self.macaroon}
        self.cert_path = cert_file_path
        import ssl

        ssl_ctx = ssl.create_default_context(cafile=self.cert_path)
        self.http_client = httpx.AsyncClient(
            base_url=self.rest_host,
            verify=self.cert_path,
            headers=self.headers
        )

    async def check_node_connection(self) -> NodeStatusResponse:
        """
        try:
        https://lightning.engineering/api-docs/api/lnd/lightning/get-info/
        else throw a connection error
        """
        try:
            r = await self.http_client.get('/v1/getinfo')
            r.raise_for_status()
        except (httpx.ConnectError, httpx.RequestError) as error:
            msg = f'could not connect to {self.rest_host}, {error}'
            logger.error(msg)
            raise Exception(msg)

        try:
            data = r.json()
            if r.is_error:
                raise Exception
        except Exception:
            return NodeStatusResponse(
                healthy=False,
                error_message=r.text[:200]
            )

        if not data['synced_to_chain'] or not data['synced_to_graph']:
            return NodeStatusResponse(
                healthy=False,
                synced_to_chain=data['synced_to_chain'],
                synced_to_graph=data['synced_to_graph'],
                error_message=f"synced to chain: {data['synced_to_chain']}, "
                f"synced to graph: {data['synced_to_graph']}, "
                "cannot proceed"
            )

        return NodeStatusResponse(
            healthy=True,
            synced_to_chain=data['synced_to_chain'],
            synced_to_graph=data['synced_to_graph'],
            error_message=None
        )

    async def close_rest_client(self) -> None:
        try:
            await self.http_client.aclose()
        except RuntimeError as e:
            logger.error(f"Could not close rest client: {e}")

    async def get_node_id(self) -> GetNodeIdResponse:
        try:
            r = await self.http_client.get('/v1/getinfo')
        except Exception as e:
            raise Exception(f"failed to get info: {e}")

        if r.is_error:
            error_message = r.text
            return GetNodeIdResponse(
                pubkey='',
                alias='',
                error_message=error_message
            )

        pubkey = r.json().get('identity_pubkey')
        alias = r.json().get('alias')
        if pubkey and alias:
            return GetNodeIdResponse(pubkey=pubkey, alias=alias)

        return GetNodeIdResponse(
            pubkey='',
            alias='',
            error_message='could not getinfo'
        )

    async def get_reserve_amount(self) -> WalletReserveResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/wallet-kit/required-reserve/
        """
        try:
            r = await self.http_client.get('/v2/wallet/reserve')
        except Exception:
            return WalletReserveResponse(
                required_reserve=100000,
                error_message='failed to get response from ln backend, using max default reserve'
            )

        reserve = r.json().get('required_reserve')
        if reserve:
            return WalletReserveResponse(required_reserve=reserve)

        return WalletReserveResponse(required_reserve=0)

    async def get_best_block(self) -> GetBestBlockResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/chain-kit/get-best-block/
        """
        try:
            r = await self.http_client.get('/v2/chainkit/bestblock')
        except Exception:
            return GetBestBlockResponse(
                block_hash=None,
                block_height=None,
                error_message="could not fetch best block from ln backend"
            )

        block_hash = r.json().get('block_hash')
        block_height = r.json().get('block_height')
        if block_height and block_hash:
            return GetBestBlockResponse(
                block_hash=block_hash,
                block_height=block_height
            )

        return GetBestBlockResponse(error_message="response 200 did not give block heigh")

    def _get_median_fee_rates(self, node_info: Dict[str, Any]) -> Dict[str, int]:
        pubkey = node_info.get("node").get("pub_key")
        outbound = []
        inbound  = []

        for ch in node_info.get("channels", []):
            if ch["node1_pub"] == pubkey:
                out_rate = int(ch["node1_policy"]["fee_rate_milli_msat"])
                in_rate = int(ch["node2_policy"]["fee_rate_milli_msat"])
            else:
                out_rate = int(ch["node2_policy"]["fee_rate_milli_msat"])
                in_rate = int(ch["node1_policy"]["fee_rate_milli_msat"])

            outbound.append(out_rate)
            inbound.append(in_rate)

        # compute medians (or None if no channels)
        median_outbound_fee_rate = statistics.median(outbound) if outbound else None
        median_inbound_fee_rate = statistics.median(inbound) if inbound else None

        return {
            'median_outbound_fee_rate': median_outbound_fee_rate,
            'median_inbound_fee_rate': median_inbound_fee_rate,
        }

    async def get_node_properties(self, pubkey: str) -> GetNodePropertyResponse:
        try:
            params = {'include_channels': True}
            r = await self.http_client.get(f'/v1/graph/node/{pubkey}', params=params)
        except Exception as e:
            raise Exception(f"failed to get node properties: {e}")

        if r.is_error:
            error_message = r.text
            return GetNodePropertyResponse(
                error_message=error_message
            )

        total_capacity = r.json().get('total_capacity')
        num_channels = r.json().get('num_channels')
        fee_rates = self._get_median_fee_rates(node_info=r.json())
        if total_capacity and num_channels:
            return GetNodePropertyResponse(
                total_capacity=total_capacity,
                num_channels=num_channels,
                median_outbound_ppm=int(fee_rates.get('median_outbound_fee_rate')),
                median_inbound_ppm=int(fee_rates.get('median_inbound_fee_rate'))
            )

        return GetNodePropertyResponse(
            error_message='could not fetch lnd node info'
        )


    async def get_utxo_set(
            self,
            min_confs: int = None,
            max_confs: int = None,
            account: str = None,
            unconfirmed_only: bool = False) -> GetUtxosResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/wallet-kit/list-unspent/
        """
        data = {
            'min_confs': min_confs,
            'max_confs': max_confs,
            'account': account,
            'unconfirmed_only': unconfirmed_only,
        }
        try:
            r = await self.http_client.post('/v2/wallet/utxos', json=data)
        except Exception as e:
            msg = 'failed to connect to ln backend to get utxos'
            logger.error(msg)
            logger.error(f'get utxo set error: {e}')
            return GetUtxosResponse(error_message=msg)

        data = r.json()
        utxos_json = data["utxos"]

        if not utxos_json:
            msg = 'utxo set empty'
            logger.error(msg)
            return GetUtxosResponse(error_message=msg)

        utxos = list()
        for line in utxos_json:
            outpoint = line.get('outpoint')
            utxo_output = UtxoOutpoint(
                txid_bytes=outpoint.get('txid_bytes'),
                txid_str=outpoint.get('txid_str'),
                output_index=outpoint.get('output_index'),
            )
            utxo = Utxo(
                address_type=line.get('address_type'),
                address=line.get('address'),
                amount_sat=line.get('amount_sat'),
                pk_script=line.get('pk_script'),
                outpoint=utxo_output,
                confirmations=line.get('confirmations'),
            )
            utxos.append(utxo)

        return GetUtxosResponse(utxos=utxos)

    async def create_hodl_invoice(
            self,
            base64_hash: str,
            amt: int,
            expiry: int = 1200) -> HodlInvoiceResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/invoices/add-hold-invoice/
        """
        data = {'hash': base64_hash, 'value': amt, 'expiry': expiry}
        try:
            r = await self.http_client.post('/v2/invoices/hodl', json=data)
        except Exception as e:
            msg = 'failed to create hodl invoice'
            logger.error(msg)
            logger.error(f"failed to create invoice: {e}")
            return HodlInvoiceResponse(
                created=False,
                inv_hash=base64_hash,
                payment_request=None,
                expiry=None,
                error_message=msg,
            )

        if r.is_error:
            error_message = r.text
            try:
                error_message = r.json()["error"]
            except Exception:
                pass
            return HodlInvoiceResponse(
                created=False,
                inv_hash=base64_hash,
                payment_request=None,
                expiry=None,
                error_message=error_message,
            )

        data = r.json()
        payment_request = data["payment_request"]

        return HodlInvoiceResponse(
            created=True,
            inv_hash=base64_hash,
            payment_request=payment_request,
            expiry=expiry,
            error_message=None,
        )

    async def subscribe_to_hodl_invoice(
            self,
            base64_hash: str) -> AsyncIterator[PaymentStatus]:
        """
        https://lightning.engineering/api-docs/api/lnd/invoices/subscribe-single-invoice/

        listen for state changes in an invoice
        """
        endpoint = f'/v2/invoices/subscribe/{base64_hash}'
        async with self.http_client.stream("GET", endpoint, timeout=None) as r:
            async for json_line in r.aiter_lines():
                try:
                    line = json.loads(json_line)

                    if line.get("error"):
                        message = (
                            line["error"]["message"]
                            if "message" in line["error"]
                            else line["error"]
                        )
                        logger.error(f"could not get invoice status: {message}")
                        yield PaymentStatus(
                            result=PaymentStatus.UNKNOWN,
                            error_message=message
                        )

                    payment = line.get("result")

                    if payment.get("state"):
                        yield PaymentStatus(
                            result=HodlInvoiceState.from_lnd(payment["state"])
                        )
                    else:
                        yield PaymentStatus(
                            result=HodlInvoiceState.UNKNOWN,
                            error_message="no payment status",
                        )
                except Exception:
                    continue

        yield PaymentStatus(
            result=HodlInvoiceState.UNKNOWN,
            error_message="timeout"
        )

    async def settle_hodl_invoice(self, base64_preimage: str) -> PaymentStatus:
        """
        https://lightning.engineering/api-docs/api/lnd/lightning/connect-peer/
        """
        data = {'preimage': base64_preimage}
        try:
            r = await self.http_client.post('/v2/invoices/settle', json=data)
        except Exception as e:
            msg = 'could not settle hodl invoice'
            logger.error(msg)
            logger.error(f'settle hodl invoice error: {e}')
            return PaymentStatus(
                result=HodlInvoiceState.UNKNOWN,
                error_message=msg
            )

        if not r.json():
            # presumably settled since an empty response implies we released
            # the preimage but api doesn't provide more info so we should keep
            # subscription to /v2/invoices/subscribe/{r_hash} for monitoring
            return PaymentStatus(result=HodlInvoiceState.PAID)

        elif r.is_error:
            msg = r.json().get('message')
            details = r.json().get('details')
            error_message = f'{msg}, {details}'
        else:
            error_message = 'unknown failure reason'

        return PaymentStatus(
            result=HodlInvoiceState.UNKNOWN,
            error_message=error_message
        )

    async def connect_peer(
            self,
            pubkey_uri: str,
            timeout: int = 15) -> ConnectPeerResponse:
        uri_components = pubkey_uri.split('@')
        data = {
            'addr': {
                'pubkey': uri_components[0],
                'host': uri_components[1],
            },
            'perm': False,
            'timeout': timeout
        }
        try:
            r = await self.http_client.post('/v1/peers', json=data)
        except Exception as e:
            msg = f'could not connect to peer {pubkey_uri}'
            logger.error(msg)
            logger.error(f'connect peer error: {e}')
            return ConnectPeerResponse(
                connected=False,
                error_message=msg
            )

        if r.is_error:
            msg = r.json().get('message')
            if 'already connected to peer' in msg:
                connected = True
                error_message = msg
            elif 'timeout' in msg:
                connected = False
                error_message = 'connection try to {pubkey_uri} timed out'
            elif 'EOF' in msg:
                connected = False
                error_message = 'pubkey uri error or node does not exist'
            elif msg:
                connected = False
                error_message = msg
            else:
                connected = False
                error_message = 'unknown error occurred'
            return ConnectPeerResponse(
                connected=connected,
                error_message=error_message
            )

        return ConnectPeerResponse(connected=True)

    async def open_channel(self, order: Order) -> AsyncIterator[ChannelOpenResponse]:
        """
        * requires connection to node via `connect_peer` first
        https://lightning.engineering/api-docs/api/lnd/lightning/open-channel/
        """
        data = {
          'node_pubkey': order.pubkey_base64,
          'local_funding_amount': str(order.total_capacity),
          'push_sat': str(order.client_balance_sat),
          'private': False if order.announce_channel else True,
          # set these to defaults for now to get fastest confirmation times
          #'min_confs': order.required_channel_confirmations,
          #'target_conf': order.funding_confirms_within_blocks,
        }
        endpoint = '/v1/channels/stream'
        # TODO: need some connection retry logic here in case the stream
        # disconnects so we can keep track of the open status
        # probably not an issue if this is running on the same hardware as the
        # node but should be safer
        async with self.http_client.stream("POST", endpoint, json=data, timeout=None) as r:
            async for json_line in r.aiter_lines():
                try:
                    line = json.loads(json_line)

                    if line.get("error"):
                        msg = line.get('message')
                        logger.debug(f'error field in open response: {msg}')
                        yield ChannelOpenResponse(
                            channel_state=ChannelState.UNKNOWN,
                            txid_bytes=None,
                            output_index=None,
                            error_message=msg
                        )

                    chan_state = line.get('result')

                    if chan_state.get('chan_pending'):
                        pending_state = chan_state\
                            .get('chan_pending')
                        txid_bytes = pending_state.get('txid')
                        output_index = pending_state.get('output_index')
                        yield ChannelOpenResponse(
                            channel_state=ChannelState.PENDING,
                            txid_bytes=txid_bytes,
                            output_index=output_index
                        )

                    if chan_state.get('chan_open'):
                        open_state = chan_state\
                            .get('chan_open')\
                            .get('channel_point')
                        txid_bytes = open_state.get('funding_txid_bytes')
                        output_index = open_state.get('output_index')
                        yield ChannelOpenResponse(
                            channel_state=ChannelState.OPEN,
                            txid_bytes=txid_bytes,
                            output_index=output_index
                        )

                except Exception as e:
                    # if some error happens then listen for the next line
                    logger.error(f'unhandled chan open error: {e}')
                    yield ChannelOpenResponse(
                        channel_state=ChannelState.UNKNOWN,
                        txid_bytes=None,
                        output_index=None,
                        error_message="LSP could not open channel"
                    )

        msg = 'channel stream broke, LSP no longer following opening status'
        logger.error(msg)
        yield ChannelOpenResponse(
            channel_state=ChannelState.UNKNOWN,
            txid_bytes=None,
            output_index=None,
            error_message=msg
        )

    async def sign_message(self, message: str) -> SignMessageResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/lightning/sign-message/
        """
        data = {
            'msg': message,
            'single_hash': False,
        }
        try:
            r = await self.http_client.post('/v1/signmessage', json=data)
        except Exception as e:
            msg = 'failed to connect to ln backend to sign message'
            logger.error(msg)
            logger.error(f'sign message error: {e}')
            return SignMessageResponse(error_message=msg)

        data = r.json()
        err = data.get('message')

        if err and err == 'permission denied':
            msg = 'need to bake a macaroon with message sign permissions'
            logger.error(msg)
            raise SystemExit(1)

        sig = r.json().get('signature')

        if not sig:
            msg = 'signature empty'
            logger.error(msg)
            return SignMessageResponse(error_message=msg)

        return SignMessageResponse(signature=sig)

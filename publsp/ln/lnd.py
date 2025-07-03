import base64
import codecs
import httpx
import json
import logging
import statistics
from typing import Any, AsyncIterator, Dict, List

from publsp.blip51.order import Order
from publsp.ln.base import NodeBase, Utxo, UtxoOutpoint
from publsp.ln.requesthandlers import (
    CancelInvoiceResponse,
    ChannelState,
    ChannelOpenResponse,
    ConnectPeerResponse,
    EstimateChainFeeResponse,
    HodlInvoiceResponse,
    HodlInvoiceState,
    GetBestBlockResponse,
    GetNodePropertyResponse,
    GetNodeIdResponse,
    GetUtxosResponse,
    PaymentStatus,
    MacaroonPermissionsResponse,
    NodeStatusResponse,
    SignMessageResponse,
    WalletReserveResponse,
)
from publsp.settings import LndPermissions

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
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=None)
        self.http_client = httpx.AsyncClient(
            base_url=self.rest_host,
            verify=self.cert_path,
            headers=self.headers,
            timeout=timeout,
        )

    async def verify_macaroon_permissions(
            self,
            methods: List[str] = LndPermissions().methods) -> MacaroonPermissionsResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/lightning/check-macaroon-permissions/

        /lnrpc.Lightning/CheckMacaroonPermissions
        """
        macaroon_raw = bytes.fromhex(self.macaroon.decode())
        macaroon_base64 = base64.urlsafe_b64encode(macaroon_raw).decode()
        lnd_perms = await self.list_permissions()
        if not lnd_perms:
            msg = "failed to get lnd permissions list, either the macaroon is missing uri:/lnrpc.Lightning/ListPermissions or there was a connection error"
            logger.error(msg)
            return MacaroonPermissionsResponse(error_message=msg)
        invalid_perms = []
        valid_perms = []

        try:
            for uri_method in methods:
                method = uri_method.removeprefix('uri:')
                method_perms = lnd_perms[method]['permissions']
                data = {
                    'macaroon': macaroon_base64,
                    'permissions': method_perms,
                    'fullMethod': method,
                }
                r = await self.http_client.post('/v1/macaroon/checkpermissions', json=data)
                resp = r.json()
                perm_validated = resp.get('valid')
                if perm_validated:
                    valid_perms.append(method)
                else:
                    invalid_perms.append(method)
        except Exception as e:
            msg = f"failed to validate macaroon permissions, stopping: {e}"
            logger.error(msg)
            return MacaroonPermissionsResponse(error_message=msg)

        return MacaroonPermissionsResponse(
            valid_perms=valid_perms,
            invalid_perms=invalid_perms
        )

    async def list_permissions(self) -> Dict[str, Dict[str, List[Dict[str, str]]]]:
        """
        https://lightning.engineering/api-docs/api/lnd/lightning/list-permissions/

        /lnrpc.Lightning/ListPermissions
        """
        try:
            r = await self.http_client.get('/v1/macaroon/permissions')
        except Exception as e:
            raise Exception(f"failed to get permissions list: {e}")

        if r.is_error:
            logger.error(r.text)
            return None

        resp = r.json()

        method_permissions = resp.get('method_permissions')
        if method_permissions:
            return method_permissions
        return None

    async def check_node_connection(self) -> NodeStatusResponse:
        """
        try:
        https://lightning.engineering/api-docs/api/lnd/lightning/get-info/
        else throw a connection error

        /lnrpc.Lightning/GetInfo
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
        """
        /walletrpc.WalletKit/RequiredReserve
        /lnrpc.Lightning/GetInfo
        """
        try:
            r = await self.http_client.get('/v1/getinfo')
        except Exception as e:
            raise Exception(f"failed to get info: {e}")

        if r.is_error:
            error_message = r.text
            logger.error(f'error in getinfo response: {r.json()}')
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

        /walletrpc.WalletKit/RequiredReserve
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

    async def estimate_chain_fee(self, conf_target: int = 2) -> EstimateChainFeeResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/wallet-kit/estimate-fee/

        /walletrpc.WalletKit/EstimateFee
        """
        try:
            r = await self.http_client.get(f'/v2/wallet/estimatefee/{conf_target}')
        except Exception as e:
            logger.error(f'exception occurred in estimating chain fee: {e}')
            return EstimateChainFeeResponse(
                error_message='exception occurred in estimate chain fee from ln backend, ignoring'
            )

        conf_target_kw = r.json().get('sat_per_kw')
        if conf_target_kw:
            return EstimateChainFeeResponse(
                sat_per_kw=conf_target_kw,
                min_relay_fee_sat_per_kw=r.json().get('min_relay_fee_sat_per_kw')
            )

        msg = 'got empty response from estimate fee, maybe macaroon perms issue'
        logger.error(msg)
        return EstimateChainFeeResponse(error_message=msg)

    async def get_best_block(self) -> GetBestBlockResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/chain-kit/get-best-block/

        /chainrpc.ChainKit/GetBestBlock
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
        """
        https://lightning.engineering/api-docs/api/lnd/lightning/get-node-info/

        /lnrpc.Lightning/GetNodeInfo
        """
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

        /walletrpc.WalletKit/ListUnspent
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
        utxos_json = data.get("utxos")

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

        /invoicesrpc.Invoices/AddHoldInvoice
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

        /invoicesrpc.Invoices/SubscribeSingleInvoice
        """
        endpoint = f'/v2/invoices/subscribe/{base64_hash}'
        async with self.http_client.stream("GET", endpoint, timeout=None) as r:
            async for json_line in r.aiter_lines():
                try:
                    line = json.loads(json_line)

                    if line and line.get("error"):
                        logger.error(f'error line: {line}')
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

                    if payment and payment.get("state"):
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
        https://lightning.engineering/api-docs/api/lnd/invoices/settle-invoice/

        /invoicesrpc.Invoices/SettleInvoice
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

    async def cancel_hodl_invoice(self, base64_hash: str) -> CancelInvoiceResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/invoices/cancel-invoice/

        /invoicesrpc.Invoices/CancelInvoice
        """
        data = {'payment_hash': base64_hash}
        error_msg = 'failed to cancel hodl invoice, will need to wait for timeout to get refund'
        try:
            r = await self.http_client.post('/v2/invoices/cancel', json=data)
        except Exception as e:
            logger.error(f"failed to cancel invoice: {e}")
            return CancelInvoiceResponse(
                cancelled=False,
                error_message=error_msg,
            )

        if r.is_error:
            logger.error(f'error in cancelling invoice: {r.json()}')
            return CancelInvoiceResponse(
                cancelled=False,
                error_message=error_msg,
            )

        # empty response means successfully cancelled
        if not r.json():
            logger.info(f'refunded invoice with hash {base64_hash}')
            return CancelInvoiceResponse(cancelled=True)

        # any other unhandled response check should error out
        return CancelInvoiceResponse(cancelled=False, error_message=error_msg)

    async def connect_peer(
            self,
            pubkey_uri: str,
            timeout: int = 15,
            retry_connect: bool = True) -> ConnectPeerResponse:
        """
        https://lightning.engineering/api-docs/api/lnd/lightning/connect-peer/

        /lnrpc.Lightning/ConnectPeer
        """
        uri_components = pubkey_uri.split('@')
        data = {
            'addr': {
                'pubkey': uri_components[0],
                'host': uri_components[1],
            },
            'perm': retry_connect,
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

        /lnrpc.Lightning/OpenChannel
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
                    if not line:
                        logger.error('channel open response line empty, maybe lag')
                        continue

                    if line and line.get("error"):
                        logger.error(f'error line: {line}')
                        yield ChannelOpenResponse(
                            channel_state=ChannelState.UNKNOWN,
                            txid_bytes=None,
                            output_index=None,
                            error_message='LSP could not open channel, please try again later'
                        )

                    chan_state = line.get('result')

                    if chan_state and chan_state.get('chan_pending'):
                        pending_state = chan_state\
                            .get('chan_pending')
                        txid_bytes = pending_state.get('txid')
                        output_index = pending_state.get('output_index')
                        yield ChannelOpenResponse(
                            channel_state=ChannelState.PENDING,
                            txid_bytes=txid_bytes,
                            output_index=output_index
                        )

                    if chan_state and chan_state.get('chan_open'):
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
                    logger.error(f'unhandled chan open error, continuing to next iteration: {e}')
                    continue

        msg = 'LSP could not open the channel, refund being issued'
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

        /lnrpc.Lightning/SignMessage
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

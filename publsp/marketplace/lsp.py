import asyncio
import contextlib
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from nostr_sdk import (
    Kind, KindStandard,
    PublicKey,
    Tag,
)
from typing import Dict, Literal, Union

from publsp.blip51.info import Ad
from publsp.blip51.order import (
    Order, OrderErrorCode,
    OrderErrorResponse,
    OrderResponse,
    Payment,
)
from publsp.blip51.payment import Bolt11, HodlInvoiceState
from publsp.blip51.utils import calculate_lease_cost
from publsp.ln.requesthandlers import (
    ChannelState,
    GetNodeSummaryResponse,
    Preimage,
)
from publsp.marketplace.base import AdEventData, MarketplaceAgent
from publsp.nostr.client import NostrClient
from publsp.nostr.kinds import PublspKind
from publsp.nostr.nip17 import RumorHandler
from publsp.settings import AdStatus, LnImplementation, LspSettings

# init_logger(LogLevel.INFO)
logger = logging.getLogger(name=__name__)


class AdHandler(MarketplaceAgent):
    """
    Create/remove/modify/manage LSP ad events on nostr
    """
    def __init__(
            self,
            nostr_client: NostrClient,
            ln_backend: LnImplementation,
            **kwargs):
        self.nostr_client = nostr_client
        self.ln_backend = ln_backend
        self.kind = PublspKind
        self.active_ads: AdEventData = None
        self.options = kwargs

    def generate_ad_id(self, pubkey: str) -> str:
        """
        in the future we may generate a hash of the self.options json order
        to allow lsps to create multiple ads
        """
        pubkey_hash = hashlib.sha256(pubkey.encode())
        hash_digest = pubkey_hash.digest()
        uuid_value = str(uuid.UUID(bytes=hash_digest[:16]))

        return uuid_value

    async def build_ad(self, pubkey: str, **kwargs) -> Ad:
        """
        build an ad given the cli arguments (**kwargs) and pubkey pulled from
        ln node backend

        idea would be to create multiple different ads by running a uuid5 on
        the parameter set or something like that to set as `ad_id`
        """
        ad_id = self.generate_ad_id(pubkey=pubkey)
        lsp_ad = Ad(lsp_pubkey=pubkey, d=ad_id, **kwargs)
        include_sig_in_ad = kwargs.get('include_node_sig')
        if include_sig_in_ad:
            nostr_pubkey = self.nostr_client.key_handler.keys.public_key().to_hex()
            lsp_sig = await self.ln_backend.sign_message(message=nostr_pubkey)
            lsp_ad.lsp_sig = lsp_sig.signature
        return lsp_ad

    async def get_lsp_data(self) -> GetNodeSummaryResponse:
        """
        put together lsp pubkey, alias, total capacity and number of channels
        into a dict with the idea that it goes into the ad content field
        """
        get_info = await self.ln_backend.get_node_id()
        get_node_info = await self.ln_backend.get_node_properties(
            pubkey=get_info.pubkey)
        return GetNodeSummaryResponse(
            pubkey=get_info.pubkey,
            alias=get_info.alias,
            total_capacity=get_node_info.total_capacity,
            num_channels=get_node_info.num_channels,
            median_outbound_ppm=get_node_info.median_outbound_ppm,
            median_inbound_ppm=get_node_info.median_inbound_ppm,
        )

    async def publish_ad(
            self,
            status: AdStatus = AdStatus.ACTIVE,
            content: str = '') -> None:
        """
        currently only set up to publish one ad and sets the single event to
        the active_ads attributes

        in the future we'll build out functionality for multiple ads per lsp
        pubkey, this could be done by the user specifying the parameters in a
        json file (and cli helper to create those ads in a json file) for each
        distinct ad they want to make
        """
        node_stats = await self.get_lsp_data()
        lsp_ad = await self.build_ad(pubkey=node_stats.pubkey, **self.options)
        lsp_ad.status = status
        ad_tags = lsp_ad.model_dump_tags()
        # assemble custom content
        ad_content = {
            'lsp_message': content,
            'node_stats': node_stats.model_dump_str(
                exclude={"error_message", "pubkey"}),
        }
        # build the nostr event using the ad
        event = self.nostr_client.build_event(
            tags=ad_tags,
            content=json.dumps(ad_content),
            kind=self.kind.AD.as_kind_obj)

        # publish the event
        await self.nostr_client.send_event(event)
        ads = {lsp_ad.d: lsp_ad}
        ad_events = {lsp_ad.d: event}
        self.active_ads = AdEventData(ads=ads, ad_events=ad_events)

    async def update_ad_events(
            self,
            update_type: Literal['inactivate', 'delete'] = 'inactivate') -> None:
        """
        update an event to either set 'inactive' or simply request deletion

        updating is preferrable to deletion request for the following reasons:
        1) relays may not respect addressable events, so pushing another event
        with a later timestamp and status (like inactive/active) makes it
        easier to parse for the customer
        2) relays may not respect deletion requests
        """
        if not self.active_ads:
            return
        for ad in self.active_ads.ads.values():
            # build the tags
            tags_dict = {
                "e": ad.d,
                "k": str(self.kind.AD.value)
            }
            tags = [
                Tag.parse([tag, value])
                for tag, value in tags_dict.items()
            ]
            # build the event with the kind, content, tags and sign with keys
            content = "updating ad"

            if update_type == 'inactivate':
                await self.publish_ad(content=content, status=AdStatus.INACTIVE)
                continue

            # else send deletion request
            event = self.nostr_client.build_event(
                tags=tags,
                content=content,
                kind=Kind.from_std(KindStandard.EVENT_DELETION)
            )
            output = await self.nostr_client.send_event(event)
            if output.success:
                logger.info(f'Ad {ad.d} deleted')
                self.active_ads = None
            else:
                logger.error(f'error deleting ad {ad.d}')

        return


class OrderHandler:
    def __init__(
            self,
            ln_backend: LnImplementation,
            ad_handler: AdHandler,
            rumor_handler: RumorHandler,
            nostr_client: NostrClient,
            lease_history_file_path: str = LspSettings().lease_history_file_path):
        self.ln_backend = ln_backend
        self.ad_handler = ad_handler
        self.rumor_handler = rumor_handler
        self.nostr_client = nostr_client
        self.lease_history_file_path = lease_history_file_path
        self._channel_point: str = None

    async def verify_order_and_connection(
            self,
            order: Order) -> Union[OrderResponse, None]:
        ad = self.ad_handler.active_ads.ads[order.d]
        # validate the order request first
        checked_order = order.validate_order(ad=ad)
        if not checked_order.is_valid:
            logger.error("order has an invalid option, cancelling")
            return OrderErrorResponse(
                code=checked_order.error_code,
                error_message=checked_order.error_message
            )

        # verify that we have enough funds to fill the order
        # need sum of confirmed utxos, less reserve amount, to be greater than
        # order total capacity
        utxos = await self.ln_backend.get_utxo_set()
        if utxos.error_message:
            logger.error("could not fetch utxo set to fulfill order")
            return OrderErrorResponse(
                code=OrderErrorCode.invalid_params,
                error_message="LSP could not successfully fill order at this moment, please try again later"
            )
        reserve = await self.ln_backend.get_reserve_amount()
        if utxos.spendable_amount - reserve.required_reserve < order.total_capacity:
            logger.error("order total capacity greater than available utxo set")
            return OrderErrorResponse(
                code=OrderErrorCode.invalid_params,
                error_message="LSP could not successfully fill order at this moment, please try again later"
            )

        # try connecting to pubkey uri to make sure we can open channel
        # before taking any payments
        peer_connection = await self.ln_backend.connect_peer(
            pubkey_uri=order.target_pubkey_uri
        )
        if not peer_connection:
            logger.error("failed to connect to peer, cancelling order")
            return OrderErrorResponse(
                code=OrderErrorCode.connection_error,
                error_message=f'Could not connect to {order.pubkey_uri}, '
                'try connecting to the LSP node first'
            )

    def get_order_costs(self, order: Order) -> Dict[str, int]:
        ad = self.ad_handler.active_ads.ads[order.d]
        total_fee = calculate_lease_cost(
            fixed_cost=ad.fixed_cost_sats,
            variable_cost_ppm=ad.variable_cost_ppm,
            capacity=order.total_capacity,
            channel_expiry_blocks=order.channel_expiry_blocks,
            max_channel_expiry_blocks=ad.max_channel_expiry_blocks
        )
        total_cost = total_fee + order.client_balance_sat
        return ({'total_fee': total_fee, 'total_cost': total_cost})

    async def _prepare_order(self, order: Order):
        preimage = Preimage.generate()
        costs = self.get_order_costs(order)
        inv = await self.ln_backend.create_hodl_invoice(
            base64_hash=preimage.base64_hash,
            amt=costs['total_cost']
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=inv.expiry)
        bolt11 = Bolt11(
            state=HodlInvoiceState.EXPECT_PAYMENT,
            expires_at=expires_at,
            fee_total_sat=costs['total_fee'],
            order_total_sat=costs['total_cost'],
            invoice=inv.payment_request
        )
        payment = Payment(bolt11=bolt11, onchain=None)
        return preimage, OrderResponse.from_order(order, payment)

    async def _payment_listener(
            self,
            preimage: Preimage,
            client_pubkey: str) -> bool:
        """
        Listen on the hodl‐invoice subscription and only return True
        once we see a PAID state.  Return False if the stream ends first.
        """
        async for status in self.ln_backend.subscribe_to_hodl_invoice(preimage.base64_hash):
            logger.debug("invoice update: %s", status)
            if status.result == HodlInvoiceState.HOLD:
                logger.info("Invoice paid, notifying client")
                return True

        # if we drop out of the loop without seeing PAID:
        logger.warning("Invoice subscription closed without PAID")
        return False

    async def _channel_open_listener(
            self,
            order: Order,
            preimage: Preimage,
            client_pubkey: PublicKey) -> None:
        """
        Send a DM for every channel_state update.  When we finally get OPEN,
        settle the hodl invoice and send a final DM, and write lease details to
        file for record-keeping
        """
        async for update in self.ln_backend.open_channel(order=order):
            state = update.channel_state
            logger.info(f'"Channel state is now {state}')
            await self.nostr_client.send_private_msg(
                client_pubkey,
                f"Channel status update",
                rumor_extra_tags=update.model_dump_tags(),
            )
            if state == ChannelState.OPEN:
                # finally release the invoice preimage
                await self.ln_backend.settle_hodl_invoice(preimage.base64)
                # send a message saying payment settled
                await self.nostr_client.send_private_msg(
                    client_pubkey,
                    "Channel tx confirmed, preimage released",
                    rumor_extra_tags=update.model_dump_tags(),
                )
                # append channel open details to file
                channel_point = f'{update.txid_hex}:{update.output_index}'
                await self._append_lease_sale_to_output_file(
                    order=order,
                    preimage=preimage,
                    channel_point=channel_point
                )
                return

    def _read_lease_output_file(self):
        if os.path.exists(self.lease_history_file_path):
            with open(self.lease_history_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        return data

    def _write_lease_output_file(self, lease_history_data):
        with open(self.lease_history_file_path, "w", encoding="utf-8") as f:
            json.dump(lease_history_data, f, indent=4)

    async def _append_lease_sale_to_output_file(
            self,
            order: Order,
            preimage: Preimage,
            channel_point: str):
        best_block = await self.ln_backend.get_best_block()
        if best_block.block_height:
            lease_start_block = best_block.block_height
            lease_end_block = lease_start_block + order.channel_expiry_blocks
        else:
            lease_start_block = best_block.error_message
            lease_end_block = f'in {order.channel_expiry_blocks} blocks'
        lease_price = self.get_order_costs(order=order)
        lease_sale_info = {
            'pubkey_uri': order.target_pubkey_uri,
            'lsp_balance_sat': order.lsp_balance_sat,
            'client_balance_sat': order.client_balance_sat,
            'total_capacity': order.total_capacity,
            'channel_expiry_blocks': order.channel_expiry_blocks,
            'lease_start_block': lease_start_block,
            'lease_end_block': lease_end_block,
            'total_fee': lease_price['total_fee'],
            'total_cost': lease_price['total_cost'],
            'payment_hash': preimage.hex_hash,
            'channel_point': channel_point,
        }
        lease_history_data = self._read_lease_output_file()
        lease_history_data.setdefault("leases", [])
        lease_history_data["leases"].append(lease_sale_info)
        self._write_lease_output_file(lease_history_data=lease_history_data)
        logger.debug(f'wrote lease sale data to {self.lease_history_file_path}')

    async def process_payment_and_channel_open(
            self,
            customer_nostr_pubkey: PublicKey,
            order: Order,
            preimage: Preimage) -> None:

        # 1) Wait for the invoice to be paid
        paid = await self._payment_listener(
            preimage=preimage,
            client_pubkey=customer_nostr_pubkey)
        logger.debug(f'started payment listener: {paid}')
        if not paid:
            logger.debug('not paid, returning')
            return

        # 2) Once paid, start sending channel‐update DMs
        logger.debug('starting channel open listener')
        await self._channel_open_listener(
            order=order,
            preimage=preimage,
            client_pubkey=customer_nostr_pubkey)

    async def _handle_channel_request(self, rumor, order):
        logger.info(f'received and verifying order: {order}')
        client_nostr_pubkey = rumor.author()
        err = await self.verify_order_and_connection(order)
        if isinstance(err, OrderErrorResponse):
            logger.info(f'notifying client of error: {err.error_message}')
            return await self.nostr_client.send_private_msg(
                client_nostr_pubkey,
                'failed to process order',
                rumor_extra_tags=err.model_dump_tags()
            )
        logger.debug(f'order verified: {order}')

        logger.info('preparing order')
        preimage, resp = await self._prepare_order(order)
        logger.info('sending nip17 dm order response to customer')
        await self.nostr_client.send_private_msg(
            client_nostr_pubkey,
            "please pay invoice to open channel",
            rumor_extra_tags=resp.model_dump_tags()
        )
        await self.process_payment_and_channel_open(
            customer_nostr_pubkey=client_nostr_pubkey,
            order=order,
            preimage=preimage)

    async def _listen(self):
        try:
            async for rumor, order in self.rumor_handler.order_requests():
                # fire‑and‑forget, so multiple orders run concurrently
                asyncio.create_task(self._handle_channel_request(rumor, order))
        except asyncio.CancelledError:
            pass

    def start(self):
        if getattr(self, "_task", None) is None or self._task.done():
            self._task = asyncio.create_task(self._listen())

    async def stop(self):
        if hasattr(self, "_task"):
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

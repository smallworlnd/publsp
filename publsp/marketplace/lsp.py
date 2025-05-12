import asyncio
import contextlib
import hashlib
import json
import logging
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
from publsp.ln.requesthandlers import (
    ChannelState,
    GetNodeSummaryResponse,
    Preimage,
)
from publsp.marketplace.base import AdEventData, MarketplaceAgent
from publsp.nostr.client import NostrClient
from publsp.nostr.kinds import PublspKind
from publsp.nostr.nip17 import RumorHandler
from publsp.settings import AdStatus, LnImplementation

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
        self.options = {
            key: value
            for key, value in kwargs.items()
            if key in list(Ad.model_fields.keys())
        }

    def generate_ad_id(self, pubkey: str) -> str:
        """
        in the future we may generate a hash of the self.options json order
        to allow lsps to create multiple ads
        """
        pubkey_hash = hashlib.sha256(pubkey.encode())
        hash_digest = pubkey_hash.digest()
        uuid_value = str(uuid.UUID(bytes=hash_digest[:16]))

        return uuid_value

    def build_ad(self, pubkey: str, **kwargs) -> Ad:
        """
        build an ad given the cli arguments (**kwargs) and pubkey pulled from
        ln node backend

        idea would be to create multiple different ads by running a uuid5 on
        the parameter set or something like that to set as `ad_id`
        """
        ad_id = self.generate_ad_id(pubkey=pubkey)
        lsp_ad = Ad(lsp_pubkey=pubkey, d=ad_id, **kwargs)
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
        lsp_ad = self.build_ad(pubkey=node_stats.pubkey, **self.options)
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
            nostr_client: NostrClient):
        self.ln_backend = ln_backend
        self.ad_handler = ad_handler
        self.rumor_handler = rumor_handler
        self.nostr_client = nostr_client

    async def verify_order_and_connection(
            self,
            order: Order) -> Union[OrderResponse, None]:
        ad = self.ad_handler.active_ads.ads[order.d]
        # validate the order request first
        checked_order = order.validate_order(ad=ad)
        if not checked_order.is_valid:
            return OrderErrorResponse(
                code=checked_order.error_code,
                error_message=checked_order.error_message
            )

        # try connecting to pubkey uri to make sure we can open channel
        # before taking any payments
        peer_connection = await self.ln_backend.connect_peer(
            pubkey_uri=order.target_pubkey_uri
        )
        if not peer_connection:
            return OrderErrorResponse(
                code=OrderErrorCode.connection_error,
                error_message=f'Could not connect to {order.pubkey_uri}, '
                'try connecting to the LSP node first'
            )

    def get_order_costs(self, order: Order) -> Dict[str, int]:
        ad = self.ad_handler.active_ads.ads[order.d]
        total_fee = ad.fixed_cost_sats +\
            ad.variable_cost_ppm * 1e-6 * order.total_capacity
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
        Send a DM for every channel_state update.  When we finally
        get OPEN, settle the hodl invoice and send a final DM.
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
                await self.nostr_client.send_private_msg(
                    client_pubkey,
                    "Channel tx confirmed, preimage released",
                    rumor_extra_tags=update.model_dump_tags(),
                )
                return

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
            logger.error(f'something went wrong: {err.error_message}')
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

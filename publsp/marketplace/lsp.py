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
from publsp.ln.utils import spend_all_cost
from publsp.marketplace.base import AdEventData, MarketplaceAgent
from publsp.nostr.client import NostrClient
from publsp.nostr.kinds import PublspKind
from publsp.nostr.nip17 import RumorHandler
from publsp.settings import (
    AdSettings,
    AdStatus,
    CustomAdSettings,
    LnImplementation,
    LspSettings,
)

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
        # Store kwargs for potential reload
        self._init_kwargs = kwargs

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
            **kwargs) -> None:
        """
        currently only set up to publish one ad and sets the single event to
        the active_ads attributes

        in the future we'll build out functionality for multiple ads per lsp
        pubkey, this could be done by the user specifying the parameters in a
        json file (and cli helper to create those ads in a json file) for each
        distinct ad they want to make

        kwargs here is strictly used to overwrite any self.options
        """
        node_stats = await self.get_lsp_data()
        ad_fields = {**self.options, **kwargs}
        lsp_ad = await self.build_ad(pubkey=node_stats.pubkey, **ad_fields)
        # adjust status and max capacity fields
        lsp_ad.status = status
        channel_max_bucket = self.options.get('channel_max_bucket', CustomAdSettings().channel_max_bucket)
        sum_utxos_as_max_capacity = self.options.get('sum_utxos_as_max_capacity', CustomAdSettings().sum_utxos_as_max_capacity)
        adjusted_max_capacity = await self.adjust_ad_max_capacity(
            ad=lsp_ad,
            channel_max_bucket=channel_max_bucket,
            sum_utxos_as_max_capacity=sum_utxos_as_max_capacity
        )
        if not adjusted_max_capacity:
            # short circuit: utxo set insufficient to sell channel as advertise
            logger.warning('max capacity < min_capacity, cannot publish ad')
            if self.active_ads:
                logger.info('inactivating ads since max capacity < min capacity')
                await self.inactivate_ads()
            return
        dynamically_set_fixed_cost = self.options.get('dynamic_fixed_cost')
        if dynamically_set_fixed_cost:
            lsp_ad.fixed_cost_sats = await self.adjust_fixed_cost()
        lsp_ad.max_channel_balance_sat = adjusted_max_capacity
        lsp_ad.max_initial_lsp_balance_sat = adjusted_max_capacity

        # build the event components
        ad_tags = lsp_ad.model_dump_tags()
        # assemble custom content
        ad_content = {
            'lsp_message': self.options.get('value_prop', CustomAdSettings().value_prop),
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

    async def inactivate_ads(self, update_type: Literal['inactivate', 'delete'] = 'inactivate') -> None:
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
        for ad_id, ad_event in self.active_ads.ad_events.items():
            # first fetch tags from the existing event
            ad_tags = [
                tag
                if not tag.kind().is_status()
                else Tag.parse(['status', 'inactive'])
                for tag in ad_event.tags().to_vec()
            ]
            content = ad_event.content()
            event_kind = self.kind.AD.as_kind_obj \
                if update_type == 'inactivate' \
                else Kind.from_std(KindStandard.EVENT_DELETION)
            # build an event with the tags, content, kind
            event = self.nostr_client.build_event(
                tags=ad_tags,
                content=content,
                kind=event_kind
            )

            output = await self.nostr_client.send_event(event)
            if output.success:
                logger.info(f'successfully sent {update_type} event for ad {ad_id}')
            else:
                logger.error(f'error sending {update_type} event for ad {ad_id}')
                continue

            if update_type == 'inactivate':
                self.active_ads.ads[ad_id].status = AdStatus.INACTIVE
                self.active_ads.ad_events[ad_id] = event
            else:
                del self.active_ads.ads[ad_id]
                del self.active_ads.ad_events[ad_id]

        return

    async def adjust_fixed_cost(self) -> int:
        try:
            conf_target = self.options.get('dynamic_fixed_cost_conf_target', 2)
            fee_multiplier = self.options.get('dynamic_fixed_cost_vb_multiplier', 320)
            chain_fees = await self.ln_backend.estimate_chain_fee(conf_target=conf_target)
            return round(chain_fees.sat_per_vb * fee_multiplier)
        except Exception as e:
            logger.error(f'could not fetch adjusted fix cost: {e}')
            fallback = round(self.options.get('fixed_cost_sats', 1000))
            logger.error(f'using fallback value of {fallback} sats')
            return fallback

    async def adjust_ad_max_capacity(
            self,
            ad: Ad,
            channel_max_bucket: int = CustomAdSettings().channel_max_bucket,
            sum_utxos_as_max_capacity: bool = CustomAdSettings().sum_utxos_as_max_capacity) -> float:
        """
        set the max capacity for ads as a function of the utxo set.
        Ad.max_channel_balance_sat is the default, but user may want to adjust
        to the sum of utxo set, or may need to adjust if sum of utxo set is
        less than `Ad.max_channel_balance_sat` but greater than
        `Ad.min_channel_balance_sat`

        1) return None if sum of confirmed utxos is less than min
        or 2) set the max channel size as the sum of confirmed utxos (only if
        `sum_utxos_as_max_capacity` is True). this takes into account reserve
        and cost of spending all utxos,
        or 3) modify the existing max if the sum of confirmed utxos is less than
        current max (rounded down to `channel_max_bucket`)
        or 4) return the original max capacity
        """
        try:
            utxos = await self.ln_backend.get_utxo_set()
            reserve = await self.ln_backend.get_reserve_amount()
            chain_fees = await self.ln_backend.estimate_chain_fee()
            # get cost of spending all utxos as buffer
            all_utxos_spend_cost = spend_all_cost(
                inputs=utxos.utxos,
                chain_fee_sat_vb=chain_fees.sat_per_vb,
                num_outputs=2)
            available_funds = round(utxos.spendable_amount \
                - reserve.required_reserve \
                - all_utxos_spend_cost)

            if available_funds < ad.min_channel_balance_sat:
                return None
            if sum_utxos_as_max_capacity:
                return available_funds
            if available_funds < ad.max_channel_balance_sat:
                new_max_capacity = available_funds - (available_funds % channel_max_bucket)
                return new_max_capacity

            return ad.max_channel_balance_sat
        except Exception as e:
            logger.error(f'could not get adjusted max capacity, returning None to inactivate ad: {e}')
            return None

    async def reload(self):
        """Reload the ad_handler with new AdSettings from .env file."""
        try:
            logger.info("Hot reloading ad changes...")

            new_ad_settings = AdSettings()
            new_value_prop = CustomAdSettings()

            # Check if different from current
            current_options = self.options
            new_options = new_ad_settings.model_dump() | new_value_prop.model_dump()

            if current_options == new_options:
                logger.info("No AdSettings changes detected")
                return

            logger.info("AdSettings changed, reloading...")

            # Create new AdHandler with updated AdSettings
            updated_kwargs = self._init_kwargs.copy()
            updated_kwargs.update(new_options)

            # update the ad_handler field and republish the ad
            new_ad_handler = AdHandler(
                nostr_client=self.nostr_client,
                ln_backend=self.ln_backend,
                **updated_kwargs,
            )
            await new_ad_handler.publish_ad(content=new_options['value_prop'])

            # check to make sure we published the new events and so we can
            # modify the live objects
            if hasattr(new_ad_handler.active_ads, 'ads') and new_ad_handler.active_ads.ads:
                # update the order handler with the latest ad handler
                return new_ad_handler
            else:
                logger.error('error in hot loading new fields, keeping previous')
                logger.error(f'settings that prevented hot loading: {new_options}')

        except Exception as e:
            logger.error(f"Error during ad hot reload: {e}")


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
        # need sum of confirmed utxos, less reserve amount, less chain fees
        # needed if all utxos needed to be spent, to be greater than
        # order total capacity
        utxos = await self.ln_backend.get_utxo_set()
        buyer_msg = "LSP could not successfully fill order at this moment, please try again later"
        if utxos.error_message:
            logger.error("could not fetch utxo set to fulfill order")
            return OrderErrorResponse(
                code=OrderErrorCode.invalid_params,
                error_message=buyer_msg
            )
        reserve = await self.ln_backend.get_reserve_amount()
        chain_fees = await self.ln_backend.estimate_chain_fee()
        # assume P2WPKH, cost to send all utxos to 2 outputs is tx header (10.5vB)
        # + 2 outputs (2*31 vB) + num_utxos * 68vB
        all_utxos_spend_cost = (10.5 + 2 * 31 + 68 * utxos.num_utxos) * chain_fees.sat_per_vb
        can_utxo_set_fill_order = round(utxos.spendable_amount \
            - reserve.required_reserve \
            - all_utxos_spend_cost) \
                < order.total_capacity
        if can_utxo_set_fill_order:
            logger.error("order total capacity greater than available utxo set")
            return OrderErrorResponse(
                code=OrderErrorCode.invalid_params,
                error_message=buyer_msg
            )

        # try connecting to pubkey uri to make sure we can open channel
        # before taking any payments
        peer_connection = await self.ln_backend.connect_peer(
            pubkey_uri=order.target_pubkey_uri
        )
        if not peer_connection.connected:
            logger.error("failed to connect to peer, cancelling order")
            logger.error(f'reason: {peer_connection.error_message}')
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
            await self.nostr_client.send_private_msg(
                client_pubkey,
                f"Channel status update",
                rumor_extra_tags=update.model_dump_tags(),
            )
            logger.info(f'Channel state is now {state}')
            if state == ChannelState.PENDING:
                # channel pending implies change in utxo set so publish a new
                # ad if needed
                await self.ad_handler.publish_ad()
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
                # update the ad now that we have some newly confirmed utxos
                await self.ad_handler.publish_ad()
                return

            if state == ChannelState.UNKNOWN:
                # cancel the invoice to issue refund
                logger.info(f'Channel could not be opened, issuing refund')
                refund = await self.ln_backend.cancel_hodl_invoice(preimage.base64_hash)
                if refund.error_message:
                    logger.error(f'got error when cancelling invoice: {refund}')
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

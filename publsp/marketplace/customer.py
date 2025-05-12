import asyncio
import click
import contextlib
from binascii import hexlify
from nostr_sdk import UnsignedEvent
from typing import Union

from publsp.blip51.info import Ad
from publsp.blip51.order import (
    Order,
    OrderErrorResponse,
    OrderResponse,
    ValidatedOrderResponse,
)
from publsp.ln.invdecoder import lndecode
from publsp.ln.requesthandlers import ChannelOpenResponse
from publsp.marketplace.base import AdEventData, MarketplaceAgent
from publsp.nostr.client import NostrClient
from publsp.nostr.kinds import PublspKind
from publsp.nostr.nip17 import RumorHandler

import logging

# init_logger(LogLevel.INFO)  # nostr_sdk logging
logger = logging.getLogger(name=__name__)


class CustomerHandler(MarketplaceAgent):
    """
    Find and evaluate LSP ads
    Communicate with an LSP to order a channel open
    """
    def __init__(
            self,
            nostr_client: NostrClient,
            **kwargs):
        self.nostr_client = nostr_client
        self.active_ads: AdEventData = None
        self.kind = PublspKind
        self.options = {
            key: value
            for key, value in kwargs.items()
            if key in list(Order.model_fields.keys())
        }

    def summarise_channel_prices(self, capacity: int = 5000000) -> None:
        """
        after running self.get_ad_info we can summarise the cost of
        opening a channel of a given capacity
        """
        yearly_mined_blocks = int(24*60/10*365)  # ~52560 blocks per year mined
        table = (
            f'{"": <64}'
            f'{"total cost (sats)": >19}'
            f'{"sats/block": >12}'
            f'{"annualized rate (%)": >21}\n'
            f'{"-" * 116}\n'
        )
        for ad in self.active_ads.ads.values():
            ad_nostr_pubkey = self.active_ads.get_nostr_pubkey(ad.d)
            warning = ''
            if capacity < ad.min_channel_balance_sat \
                    or capacity > ad.max_channel_balance_sat:
                warning = '**lsp will refuse request of this capacity, ' +\
                    'verify lsp limits**'
            total_lease_cost = int(
                ad.fixed_cost_sats +
                ad.variable_cost_ppm*1e-6*capacity
            )
            sats_per_block = round(
                total_lease_cost/ad.max_channel_expiry_blocks,
                3
            )
            annual_rate = round(
                (total_lease_cost / capacity)
                * (yearly_mined_blocks / ad.max_channel_expiry_blocks)
                * 100,
                2
            )
            table += (
                f'{warning: <64}\n'
                f'{"ad id: " + str(ad.d): <64}\n'
                f'nostr key: \n'
                f'{ad_nostr_pubkey: <64}'
                f'{total_lease_cost: >18}'
                f'{sats_per_block: >12}'
                f'{annual_rate: >21}\n'
                f'ln node key: \n'
                f'{ad.lsp_pubkey: <65}\n\n'
                f'{"-" * 116}\n'
            )

        return table

    def build_order(self, ad_id: str) -> Order:
        return Order(**self.options, d=ad_id)


class OrderResponseHandler:
    _LISTENER_CONFIG = [
        ("order_responses", "handle_order_response", "_order_task"),
        ("channel_open_responses", "handle_chan_open_response", "_chan_task"),
    ]

    def __init__(
            self,
            customer_handler: CustomerHandler,
            rumor_handler: RumorHandler,
            **kwargs):
        self.customer_handler = customer_handler
        self.rumor_handler = rumor_handler
        self.selected_ad: Ad = None  # populated after order request sent
        self.cli_opts = kwargs

    async def _listener(self, iterator, handler):
        """
        Pull off items from an async‐iterator and dispatch to handler.
        Swallows CancelledError so we can cleanly cancel tasks.
        """
        try:
            async for rumor, payload in iterator:
                handler(rumor, payload)
        except asyncio.CancelledError:
            pass

    def is_order_resp_valid(
            self,
            order_resp: OrderResponse) -> ValidatedOrderResponse:
        """
        1. decode the bolt11 invoice in the order response
        2. check invoice destination pubkey against ad lsp pubkey
        3. check order response total cost against bolt11 amount
        4. check expected total fee against order response
        5. check order response total cost against expected total cost
        6. (unnecessary) check expected total cost against bolt11 amount
        """
        # 1.
        logger.debug('validating order response')
        decoded_payreq = lndecode(order_resp.payment.bolt11.invoice)
        receiver_pubkey = hexlify(decoded_payreq.pubkey.serialize()).decode('utf-8')
        invoice_order_total_sat = int(float(decoded_payreq.amount)*1e8)
        requested_capacity = self.cli_opts.get('lsp_balance_sat') \
            + self.cli_opts.get('client_balance_sat')
        expected_fee_total = int(
            self.selected_ad.fixed_cost_sats +
            self.selected_ad.variable_cost_ppm*1e-6*requested_capacity
        )
        expected_total_cost = expected_fee_total \
            + self.cli_opts.get('client_balance_sat')
        # 2.
        if self.selected_ad.lsp_pubkey != receiver_pubkey:
            err = f'invoice does not originate from LSP, got {receiver_pubkey}'
            logger.error(err)
            return ValidatedOrderResponse(is_valid=False, error_message=err)
        # 3.
        if order_resp.payment.bolt11.order_total_sat != invoice_order_total_sat:
            err = (
                'order response order total of '
                f'{order_resp.payment.bolt11.order_total_sat} '
                'not consistent with the decoded bolt11 invoice amount of '
                '{invoice_order_total_sat}, something went wrong with the LSP')
            logger.error(err)
            return ValidatedOrderResponse(is_valid=False, error_message=err)
        # 4.
        if expected_fee_total != order_resp.payment.bolt11.fee_total_sat:
            err = (
                f'expected a fee total of {expected_fee_total} '
                'but got {order_resp.payment.bolt11.fee_total_sat} '
                'in the order response')
            logger.error(err)
            return ValidatedOrderResponse(is_valid=False, error_message=err)
        # 5.
        if expected_total_cost != order_resp.payment.bolt11.order_total_sat:
            err = (
                f'expected a total cost of {expected_total_cost} '
                'but got {order_resp.payment.bolt11.total_cost_sat} in the '
                'order response')
            logger.error(err)
            return ValidatedOrderResponse(is_valid=False, error_message=err)
        # 6.
        if expected_total_cost != invoice_order_total_sat:
            err = (
                f'expected a total cost of {expected_total_cost} '
                'but got {order_resp.payment.bolt11.total_cost_sat} in the '
                'bolt11 invoice')
            logger.error(err)
            return ValidatedOrderResponse(is_valid=False, error_message=err)

        return ValidatedOrderResponse(is_valid=True)

    def handle_order_response(
            self,
            rumor: UnsignedEvent,
            order_resp: Union[OrderResponse, OrderErrorResponse]):
        logger.debug(f'handling order response')
        if isinstance(order_resp, OrderErrorResponse):
            click.echo(
                '\n\nThe LSP had a problem processing the order request: '
                f'error code: {order_resp.code}\n'
                f'error message: {order_resp.error_message}\n\n'
            )
            return
        resp_validated = self.is_order_resp_valid(order_resp)
        if resp_validated.is_valid:
            click.echo(
                '\n\nOrder response validated, matches expectation for '
                'total fee, total cost and LSP node destination\n'
                f'Order ID: {order_resp.order_id}\n'
                f'Invoice amount: {order_resp.payment.bolt11.order_total_sat}\n'
                f'Please pay the following BOLT11 invoice:\n{order_resp.payment.bolt11.invoice}\n\n'
            )
        else:
            click.echo(
                'Something went wrong when validating the order response '
                f'from the LSP: {resp_validated.error_message}'
            )

    def handle_chan_open_response(
            self,
            rumor: UnsignedEvent,
            chan_open_resp: ChannelOpenResponse):
        logger.debug(f'handling channel open response')
        click.echo(
            '\n\nReceived channel open notification:\n'
            f'Channel status: {chan_open_resp.channel_state.value}\n'
            f'Transaction ID: {chan_open_resp.txid_hex}\n'
            f'Output index: {chan_open_resp.output_index}\n\n'
        )

    def start(self):
        """
        Walk our config table, and for each entry spin up
        a `self._listener(rumor_handler.foo(), self.handle_xyz)`.
        """
        for attr_iter, handler_name, task_attr in self._LISTENER_CONFIG:
            # if the task is missing or done, create it
            task = getattr(self, task_attr, None)
            if task is None or task.done():
                iterator = getattr(self.rumor_handler, attr_iter)()
                handler  = getattr(self, handler_name)
                new_task = asyncio.create_task(self._listener(iterator, handler))
                setattr(self, task_attr, new_task)

    async def stop(self):
        """
        Cancel and await each of our listener‐tasks.
        """
        for _, _, task_attr in self._LISTENER_CONFIG:
            task = getattr(self, task_attr, None)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

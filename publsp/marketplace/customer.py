import asyncio
import click
import contextlib
from binascii import hexlify
from nostr_sdk import UnsignedEvent
from typing import Union, Optional, Any

from publsp.blip51.info import Ad
from publsp.blip51.order import (
    Order,
    OrderErrorCode,
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
from publsp.settings import Interface, PublspSettings

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
            output_interface: Interface = PublspSettings().interface,
            response_queue_manager = None,  # New parameter for response queue
            **kwargs):
        self.customer_handler = customer_handler
        self.rumor_handler = rumor_handler
        self.selected_ad: Ad = None  # populated after order request sent
        self.opts = kwargs
        self.output_interface = output_interface

        # Initialize or use provided queue manager
        if response_queue_manager is None:
            from publsp.marketplace.response_manager import ResponseQueueManager
            self.response_queue_manager = ResponseQueueManager()
        else:
            self.response_queue_manager = response_queue_manager

        # Register response types
        self.response_queue_manager.register_response_type("order")
        self.response_queue_manager.register_response_type("channel_open")

    async def _listener(self, iterator, handler):
        """
        Pull off items from an async‐iterator and dispatch to handler.
        Swallows CancelledError so we can cleanly cancel tasks.
        """
        logger.info(f"OrderResponseHandler._listener started for handler: {handler.__name__}")
        try:
            async for rumor, payload in iterator:
                logger.info(f"OrderResponseHandler._listener received message - rumor: {rumor}, payload type: {type(payload)}")
                logger.info(f"OrderResponseHandler._listener payload content: {payload}")
                # Only pass the payload to the handler (rumor is not needed anymore)
                result = handler(payload)
                logger.info(f"OrderResponseHandler._listener handler returned: {result}")
        except asyncio.CancelledError:
            logger.info(f"OrderResponseHandler._listener cancelled for handler: {handler.__name__}")
            pass
        except Exception as e:
            logger.error(f"OrderResponseHandler._listener error: {e}", exc_info=True)

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
        requested_capacity = self.opts.get('lsp_balance_sat') \
            + self.opts.get('client_balance_sat')
        expected_fee_total = int(
            self.selected_ad.fixed_cost_sats +
            self.selected_ad.variable_cost_ppm*1e-6*requested_capacity
        )
        expected_total_cost = expected_fee_total \
            + self.opts.get('client_balance_sat')
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
                f'{invoice_order_total_sat}, something went wrong with the LSP')
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

    def _process_order_response(
            self,
            order_resp: Union[OrderResponse, OrderErrorResponse]
        ) -> Union[OrderResponse, OrderErrorResponse]:
        """
        Process the order response and return structured result.

        This core method handles the logic of response processing without
        managing the output format.

        Args:
            order_resp: The order response from the LSP

        Returns:
            tuple: (response_type, response_obj, validation_info)
                - response_type: "error", "success", or "validation_error"
                - response_obj: OrderResponse or OrderErrorResponse object
                - validation_info: ValidatedOrderResponse if applicable or None
        """
        logger.debug('processing order response')

        # Handle error response
        if isinstance(order_resp, OrderErrorResponse):
            return order_resp

        # Handle success case with validation
        resp_validated = self.is_order_resp_valid(order_resp)
        if resp_validated.is_valid:
            logger.debug(f'order response validated')
            return order_resp
        logger.debug(f'order response has an error')
        return OrderErrorResponse(
            code=OrderErrorCode.option_mismatch,
            error_message=resp_validated.error_message
        )

    def _format_order_response(
            self,
            response_obj: Union[OrderResponse, OrderErrorResponse]) -> str:
        if response_obj.error_message:
            return (
                '\n\nThe LSP had a problem processing the order request: '
                f'error code: {response_obj.code}\n'
                f'error message: {response_obj.error_message}\n\n'
            )
        return (
            '\n\nOrder response validated, matches expectation for '
            'total fee, total cost and LSP node destination\n'
            f'Order ID: {response_obj.order_id}\n'
            f'Invoice amount: {response_obj.payment.bolt11.order_total_sat}\n'
            f'Please pay the following BOLT11 invoice:\n{response_obj.payment.bolt11.invoice}\n\n'
        )

    def handle_order_response(
            self,
            order_resp: Union[OrderResponse, OrderErrorResponse]):
        """
        Handle an order response (CLI mode) or return response data (API mode).

        Args:
            order_resp: The order response object

        Returns:
            None in CLI mode, response tuple in API mode
        """
        logger.info(f"OrderResponseHandler.handle_order_response called with: {type(order_resp)}")
        logger.info(f"Response content: {order_resp}")

        # Process the response
        result = self._process_order_response(order_resp)
        logger.info(f"Processed response: {type(result)}")

        # Store the response in the queue manager
        response_type = "order"
        logger.info(f"Storing response of type '{response_type}' in queue manager")
        self.response_queue_manager.store_response(response_type, result)
        logger.info(f"Response stored successfully")

        # Handle output based on mode
        if self.output_interface == Interface.CLI:
            message = self._format_order_response(result)
            click.echo(message)
            return

        return result

    def _process_chan_open_response(
            self,
            chan_open_resp: ChannelOpenResponse) -> ChannelOpenResponse:
        """
        Process a channel open response.

        Args:
            chan_open_resp: The channel open response

        Returns:
            The processed channel open response
        """
        logger.debug('processing channel open response')
        return chan_open_resp

    def _format_chan_open_response(
            self,
            chan_open_resp: ChannelOpenResponse) -> str:
        return (
            '\n\nReceived channel open notification:\n'
            f'Channel status: {chan_open_resp.channel_state.value}\n'
            f'Transaction ID: {chan_open_resp.txid_hex}\n'
            f'Output index: {chan_open_resp.output_index}\n\n'
        )

    def handle_chan_open_response(
            self,
            chan_open_resp: ChannelOpenResponse):
        """
        Handle a channel open response (CLI mode) or return response (API mode).

        Args:
            chan_open_resp: The channel open response

        Returns:
            None in CLI mode, ChannelOpenResponse in API mode
        """
        # No additional processing needed for channel open responses
        response = chan_open_resp

        # Store the response in the queue manager
        self.response_queue_manager.store_response("channel_open", response)

        # Handle output based on interface
        if self.output_interface == Interface.CLI:
            message = self._format_chan_open_response(response)
            click.echo(message)
            return

        return response

    def start(self):
        """
        Walk our config table, and for each entry spin up
        a `self._listener(rumor_handler.foo(), self.handle_xyz)`.
        """
        logger.info("OrderResponseHandler.start() called")
        for attr_iter, handler_name, task_attr in self._LISTENER_CONFIG:
            logger.info(f"Setting up listener for {attr_iter} -> {handler_name} -> {task_attr}")
            # if the task is missing or done, create it
            task = getattr(self, task_attr, None)
            if task is None or task.done():
                iterator = getattr(self.rumor_handler, attr_iter)()
                handler = getattr(self, handler_name)
                logger.info(f"Creating task for {handler_name} with iterator {attr_iter}")
                new_task = asyncio.create_task(self._listener(iterator, handler))
                setattr(self, task_attr, new_task)
                logger.info(f"Task {task_attr} created successfully")
            else:
                logger.info(f"Task {task_attr} already exists and is running")
        logger.info("OrderResponseHandler.start() completed")

    async def stop(self):
        """
        Cancel and await each of our listener-tasks.
        """
        for _, _, task_attr in self._LISTENER_CONFIG:
            task = getattr(self, task_attr, None)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

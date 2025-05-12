import asyncio
import logging

from contextlib import suppress

from nostr_sdk import (
    Event,
    Filter,
    HandleNotification,
    Kind, KindStandard,
    SubscribeOutput,
    Timestamp,
    UnsignedEvent,
    UnwrappedGift,
)
from typing import AsyncIterator, Union

from publsp.blip51.order import Order, OrderResponse, OrderErrorResponse
from publsp.nostr.client import NostrClient
from publsp.ln.requesthandlers import ChannelOpenResponse

logger = logging.getLogger(name=__name__)


class RumorHandler:
    """
    Collects incoming NIP‑17 rumors (gift‑wrapped DMs) into an asyncio.Queue,
    and exposes them via an async‐iterator or async getters.
    """
    def __init__(self) -> None:
        # our buffer of incoming rumors
        self._queue: asyncio.Queue[UnsignedEvent] = asyncio.Queue()

    def on_new_rumor(self, rumor: UnsignedEvent) -> None:
        """
        Called by Nip17NotificationHandler.handle() whenever a new DM arrives.
        """
        # put_nowait so we don't block the NIP‑17 handler
        self._queue.put_nowait(rumor)

    async def get_rumor(self) -> UnsignedEvent:
        """
        Pull one rumor off the queue.
        """
        return await self._queue.get()

    def __aiter__(self) -> AsyncIterator[UnsignedEvent]:
        """
        Allows:
            async for rumor in rumor_handler:
                ...
        """
        return self._rumors()

    async def _rumors(self) -> AsyncIterator[UnsignedEvent]:
        while True:
            rumor = await self._queue.get()
            logger.debug(f'got rumor: {rumor}')
            yield rumor

    async def order_requests(self) -> AsyncIterator[Order]:
        """
        Filters the raw rumors to only yield valid Order requests.
        """
        async for rumor in self:
            tags = rumor.tags().to_vec()
            tag_keys = [t.as_vec()[0] for t in tags]
            order_req_fields = list(Order.model_fields.keys())
            if set(order_req_fields).issubset(tag_keys):
                # build and yield the Order
                order_req = Order.model_from_tags(tags=tags)
                logger.debug(f'rumor is order request: {order_req}')
                yield rumor, order_req

    async def order_responses(self) -> AsyncIterator[Union[OrderResponse, OrderErrorResponse]]:
        """
        Filters the raw rumors to only yield valid Order response.
        """
        async for rumor in self:
            # your existing tag‐based test
            tags = rumor.tags().to_vec()
            tag_keys = [t.as_vec()[0] for t in tags]
            order_resp_fields = list(OrderResponse.model_fields.keys())
            if set(order_resp_fields).issubset(tag_keys):
                # build and yield the Order
                logger.debug('got order response')
                order_resp = OrderResponse.model_from_tags(tags=tags)
                yield rumor, order_resp
            elif set(['error_message', 'code']).issubset(tag_keys):
                logger.debug('got order error response')
                order_err_resp = OrderErrorResponse.model_from_tags(tags=tags)
                yield rumor, order_err_resp

    async def channel_open_responses(self) -> AsyncIterator[Order]:
        """
        Filters the raw rumors to only yield valid channel open responses
        """
        async for rumor in self:
            tags = rumor.tags().to_vec()
            tag_keys = [t.as_vec()[0] for t in tags]
            chan_resp_fields = list(ChannelOpenResponse.model_fields.keys())
            if set(chan_resp_fields).issubset(tag_keys):
                logger.debug('got channel open response')
                chan_open_resp = ChannelOpenResponse.model_from_tags(tags=tags)
                logger.debug(f'rumor is channel open response: {chan_open_resp}')
                yield rumor, chan_open_resp


class Nip17NotificationHandler(HandleNotification):
    def __init__(self, nostr_client: NostrClient, rumor_handler: RumorHandler):
        self.nostr_client = nostr_client
        self.rumor_handler = rumor_handler
        self._ts = Timestamp.now()

    async def handle(self, relay_url, subscription_id, event: Event):
        if event.kind().as_std() == KindStandard.GIFT_WRAP:
            logger.debug("Received a NIP-59 event, decrypting...")
            try:
                # Extract rumor
                unwrapped_gift = await UnwrappedGift\
                    .from_gift_wrap(self.nostr_client.signer, event)
                rumor: UnsignedEvent = unwrapped_gift.rumor()

                # Check timestamp of rumor
                if rumor.created_at().as_secs() >= self._ts.as_secs():
                    rumor_kind = rumor.kind().as_std()
                    if rumor_kind == KindStandard.PRIVATE_DIRECT_MESSAGE:
                        self.rumor_handler.on_new_rumor(rumor)
                    else:
                        logger.debug(f"msg: {rumor.as_json()}")
            except Exception as e:
                logger.error(f"Error during content NIP59 decryption: {e}")

    async def handle_msg(self, relay_url, msg):
        pass


class Nip17Listener:
    """
    Subscribes to GIFT_WRAP DMs for our key,
    unwraps them, and pushes into RumorHandler’s queue.
    """
    def __init__(
            self,
            nostr_client: NostrClient,
            rumor_handler: RumorHandler):
        self.nostr_client = nostr_client
        self.handler = Nip17NotificationHandler(nostr_client, rumor_handler)
        self.filter = (
            Filter()
            .kind(Kind.from_std(KindStandard.GIFT_WRAP))
            .pubkey(nostr_client.key_handler.keys.public_key())
            .limit(0)
        )
        self._sub: Union[SubscribeOutput, None] = None
        self._task: Union[asyncio.Task, None] = None

    async def _run(self):
        # 1) subscribe
        self._sub = await self.nostr_client.subscribe(self.filter)
        # 2) hand off INCOMING events to our notification adapter
        await self.nostr_client.handle_notifications(self.handler)

    def start(self):
        """Begin relaying gift‑wrapped DMs into RumorHandler.queue."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        """Tear down the subscription and cancel the background task."""
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

        if self._sub:
            await self.nostr_client.unsubscribe(self._sub.id)

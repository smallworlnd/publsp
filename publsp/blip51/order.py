"""
https://github.com/lightning/blips/blob/master/blip-0051.md#2-lsps1create_order
"""
import base64
import uuid
from datetime import datetime, timezone
from enum import IntEnum, Enum
from pydantic import BaseModel, Field, field_serializer
from typing import Optional

from publsp.blip51.channel import Channel
from publsp.blip51.info import Ad
from publsp.blip51.mixins import NostrTagsMixin, ErrorMessageMixin
from publsp.blip51.payment import Payment
from publsp.settings import OrderSettings


class OrderState(Enum):
    CREATED = "CREATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    def __str__(self):
        return self.name


class OrderErrorCode(IntEnum):
    connection_error = 0
    invalid_params = -32602
    client_rejected = 1
    option_mismatch = 100


class ValidatedOrder(BaseModel, ErrorMessageMixin):
    is_valid: bool
    error_code: Optional[OrderErrorCode] = None


class ValidatedOrderResponse(BaseModel, ErrorMessageMixin):
    is_valid: bool


class Order(BaseModel, NostrTagsMixin):
    """
    self request
    https://github.com/lightning/blips/blob/master/blip-0051.md#2-lsps1create_self

    'd' to specify the offer id (plan is for lsps to be able to offer
    multiple depending on whatever criteria they choose)
    'target_pubkey_uri' for customer to specify where the channel should be
    opened
    """
    d: str  # corresponds to Ad.d
    target_pubkey_uri: str = OrderSettings().target_pubkey_uri
    lsp_balance_sat: int = OrderSettings().lsp_balance_sat
    client_balance_sat: int = OrderSettings().client_balance_sat
    required_channel_confirmations: int = OrderSettings().required_channel_confirmations
    funding_confirms_within_blocks: int = OrderSettings().funding_confirms_within_blocks
    channel_expiry_blocks: int = OrderSettings().channel_expiry_blocks
    token: Optional[str] = Field(default='')
    refund_onchain_address: Optional[str] = Field(default=None)
    announce_channel: bool = OrderSettings().announce_channel

    @field_serializer('lsp_balance_sat', 'client_balance_sat')
    def coerce_to_str(self, sat_amt: int, _info):
        return str(sat_amt)

    @property
    def total_capacity(self) -> int:
        return self.lsp_balance_sat + self.client_balance_sat

    @property
    def pubkey(self) -> str:
        uri_components = self.target_pubkey_uri.split('@')
        return uri_components[0]

    @property
    def pubkey_base64(self) -> str:
        pubkey_bytes = bytes.fromhex(self.pubkey)
        return base64.b64encode(pubkey_bytes).decode()

    def validate_order(self, ad: Ad) -> ValidatedOrder:
        if not self.lsp_balance_sat >= ad.min_initial_lsp_balance_sat:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="lsp_balance_sat < min_initial_lsp_balance_sat")
        if not self.lsp_balance_sat <= ad.max_initial_lsp_balance_sat:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="lsp_balance_sat > max_initial_lsp_balance_sat")
        if not self.client_balance_sat >= ad.min_initial_client_balance_sat:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="client_balance_sat < min_initial_client_balance_sat")
        if not self.client_balance_sat <= ad.max_initial_client_balance_sat:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="client_balance_sat > max_initial_client_balance_sat")
        if not self.client_balance_sat + self.lsp_balance_sat >= ad.min_channel_balance_sat:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="client_balance_sat + lsp_balance_sat < min_channel_balance_sat")
        if not self.client_balance_sat + self.lsp_balance_sat <= ad.max_channel_balance_sat:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="client_balance_sat + lsp_balance_sat > max_channel_balance_sat")
        if not self.required_channel_confirmations >= ad.min_required_channel_confirmations:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="required_channel_confirmations < min_required_channel_confirmations")
        if not self.funding_confirms_within_blocks >= ad.min_funding_confirms_within_blocks:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="funding_confirms_within_blocks < min_funding_confirms_within_blocks")
        if not self.channel_expiry_blocks <= ad.max_channel_expiry_blocks:
            return ValidatedOrder(
                is_valid=False,
                error_code=OrderErrorCode.option_mismatch,
                error_message="channel_expiry_blocks > max_channel_expiry_blocks")

        return ValidatedOrder(is_valid=True)


class OrderResponse(BaseModel, NostrTagsMixin):
    """
    order response
    https://github.com/lightning/blips/blob/master/blip-0051.md#2-lsps1create_order
    """
    order_id: str = Field(default=str(uuid.uuid4()))
    lsp_balance_sat: int
    client_balance_sat: int
    required_channel_confirmations: int
    funding_confirms_within_blocks: int
    channel_expiry_blocks: int
    token: str = Field(default='')
    created_at: datetime = Field(default=datetime.now(timezone.utc))
    announce_channel: bool
    order_state: OrderState
    payment: Payment
    channel: Optional[Channel] = Field(default=None)
    error_message: Optional[str] = Field(default='')

    @field_serializer('lsp_balance_sat', 'client_balance_sat')
    def coerce_to_str(self, sat_amt: int, _info):
        return str(sat_amt)

    @classmethod
    def from_order(cls, order: Order, payment: Payment):
        customer_order_data = {
            field: getattr(order, field)
            for field in cls.__fields__
            if field in order.model_dump()
        }
        instance = cls(
            order_state=OrderState.CREATED,
            payment=payment,
            **customer_order_data
        )
        return instance


class OrderErrorResponse(BaseModel, NostrTagsMixin, ErrorMessageMixin):
    code: OrderErrorCode

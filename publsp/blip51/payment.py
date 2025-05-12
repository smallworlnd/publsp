"""
https://github.com/lightning/blips/blob/master/blip-0051.md#3-payment
"""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_serializer
from typing import Optional

from publsp.blip51.mixins import NostrTagsMixin


class HodlInvoiceState(str, Enum):
    """blip51 invoice states"""
    EXPECT_PAYMENT = 'EXPECT_PAYMENT'
    HOLD = 'HOLD'
    PAID = 'PAID'
    REFUNDED = 'REFUNDED'
    UNKNOWN = 'UNKNOWN'

    @classmethod
    def from_lnd(cls, lnd_invoice_state: str):
        mapping = {
            "OPEN": cls.EXPECT_PAYMENT,
            "SETTLED": cls.PAID,
            "CANCELED": cls.REFUNDED,
            "ACCEPTED": cls.HOLD
        }
        return mapping.get(lnd_invoice_state, cls.UNKNOWN)

    def __str__(self):
        return self.name


class Bolt11(BaseModel):
    """part of order response"""
    state: HodlInvoiceState
    expires_at: datetime
    fee_total_sat: int
    order_total_sat: int
    invoice: str

    @field_serializer('fee_total_sat', 'order_total_sat')
    def coerce_to_str(self, sat_amt: int, _info):
        return str(sat_amt)


class Onchain(BaseModel):
    """part of order response"""
    state: str
    expires_at: datetime
    fee_total_sat: int
    order_total_sat: int
    address: str
    min_fee_for_0conf: int
    min_onchain_payment_confirmations: int
    refund_onchain_address: str

    @field_serializer('fee_total_sat', 'order_total_sat')
    def coerce_to_str(self, sat_amt: int, _info):
        return str(sat_amt)


class Payment(BaseModel, NostrTagsMixin):
    """part of order response"""
    bolt11: Bolt11
    onchain: Optional[Onchain] = Field(default=None)

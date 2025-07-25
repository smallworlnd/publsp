import base64
import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pydantic import BaseModel, model_validator, Field
from typing import List, Optional

from publsp.blip51.mixins import ErrorMessageMixin, NostrTagsMixin
from publsp.blip51.payment import HodlInvoiceState


class NodeStatusResponse(BaseModel, ErrorMessageMixin):
    healthy: bool
    synced_to_chain: Optional[bool] = None
    synced_to_graph: Optional[bool] = None


class MacaroonPermissionsResponse(BaseModel, ErrorMessageMixin):
    valid_perms: Optional[List[str]] = None
    invalid_perms: Optional[List[str]] = None


class ConnectPeerResponse(BaseModel, ErrorMessageMixin):
    connected: bool


class ChannelState(str, Enum):
    PENDING = 'PENDING'
    OPEN = 'OPEN'
    CLOSED = 'CLOSED'
    UNKNOWN = 'UNKNOWN'


class ChannelOpenResponse(BaseModel, NostrTagsMixin, ErrorMessageMixin):
    channel_state: ChannelState
    txid_bytes: Optional[str] = None
    txid_hex: Optional[str] = Field(default=None)
    output_index: Optional[int] = None

    @model_validator(mode="after")
    def compute_txid_hex(self):
        if self.txid_bytes:
            raw = base64.b64decode(self.txid_bytes)
            object.__setattr__(self, "txid_hex", raw[::-1].hex())
        return self


class HodlInvoiceResponse(BaseModel, ErrorMessageMixin):
    """on invoice creation"""
    created: bool
    inv_hash: Optional[str] = None
    payment_request: Optional[str] = None
    expiry: Optional[int] = None


class CancelInvoiceResponse(BaseModel, ErrorMessageMixin, NostrTagsMixin):
    cancelled: bool


class GetNodeIdResponse(BaseModel, ErrorMessageMixin):
    pubkey: str
    alias: str


class GetNodePropertyResponse(BaseModel, ErrorMessageMixin):
    total_capacity: Optional[int] = Field(default=None)
    num_channels: Optional[int] = Field(default=None)
    median_outbound_ppm: Optional[int] = Field(default=None)
    median_inbound_ppm: Optional[int] = Field(default=None)


class GetNodeSummaryResponse(GetNodeIdResponse, GetNodePropertyResponse):
    def model_dump_str(self, *args, **kwargs):
        d = super().model_dump(*args, **kwargs)
        return {k: str(v) for k, v in d.items() if v is not None}


class WalletReserveResponse(BaseModel, ErrorMessageMixin):
    required_reserve: Optional[int] = Field(default=None)


class EstimateChainFeeResponse(BaseModel, ErrorMessageMixin):
    sat_per_kw: Optional[int] = Field(default=None)
    min_relay_fee_sat_per_kw: Optional[int] = Field(default=None)

    @property
    def sat_per_vb(self) -> int:
        if self.sat_per_kw:
            return self.sat_per_kw / 1000 / 4
        return 0


class GetBestBlockResponse(BaseModel, ErrorMessageMixin):
    block_hash: Optional[str] = Field(default=None)
    block_height: Optional[int] = Field(default=None)


class GetUtxosResponse(BaseModel, ErrorMessageMixin):
    utxos: Optional[List["Utxo"]] = Field(default=None)

    @property
    def spendable_amount(self) -> int:
        return sum([
            utxo.amount_sat
            for utxo in self.utxos
            if utxo.confirmations >= 3
        ])

    @property
    def num_utxos(self) -> int:
        return len(self.utxos)


class SignMessageResponse(BaseModel, ErrorMessageMixin):
    signature: Optional[str] = Field(default=None)


@dataclass
class Preimage:
    hex: Optional[str] = field(default=None)
    hex_hash: Optional[str] = field(default=None)
    base64: Optional[str] = field(default=None)
    base64_hash: Optional[str] = field(default=None)

    @classmethod
    def generate(cls):
        preimage = hashlib.sha256(uuid.uuid4().bytes)
        preimage_hash = hashlib.sha256(preimage.digest())
        base64_preimage = base64.urlsafe_b64encode(preimage.digest())
        base64_preimage_hash = base64.urlsafe_b64encode(preimage_hash.digest())

        return cls(
            hex=preimage.hexdigest(),
            hex_hash=preimage_hash.hexdigest(),
            base64=base64_preimage.decode(),
            base64_hash=base64_preimage_hash.decode()
        )


class PaymentStatus(BaseModel, ErrorMessageMixin):
    result: HodlInvoiceState

    @property
    def expect_payment(self) -> bool:
        return self.result == HodlInvoiceState.EXPECT_PAYMENT

    @property
    def hold(self) -> bool:
        return self.result == HodlInvoiceState.HOLD

    @property
    def paid(self) -> bool:
        return self.result == HodlInvoiceState.PAID

    @property
    def refunded(self) -> bool:
        return self.result == HodlInvoiceState.REFUNDED

    @property
    def unknown(self) -> bool:
        return self.result == HodlInvoiceState.UNKNOWN

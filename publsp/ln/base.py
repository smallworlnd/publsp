from abc import ABC, abstractmethod
from typing import AsyncIterator, Coroutine, Optional
from pydantic import BaseModel, Field

from publsp.blip51.order import Order
from publsp.ln.requesthandlers import (
    ChannelOpenResponse,
    ConnectPeerResponse,
    GetNodeIdResponse,
    GetNodePropertyResponse,
    HodlInvoiceResponse,
    PaymentStatus,
    NodeStatusResponse,
)


class UtxoOutpoint(BaseModel):
    txid_bytes: Optional[str] = Field(default=None)
    txid_str: Optional[str] = Field(default=None)
    output_index: Optional[int] = Field(default=None)


class Utxo(BaseModel):
    address_type: Optional[str] = Field(default=None)
    address: Optional[str] = Field(default=None)
    amount_sat: Optional[int] = Field(default=None)
    pk_script: Optional[str] = Field(default=None)
    outpoint: Optional[UtxoOutpoint] = Field(default=None)
    confirmations: Optional[int] = Field(default=None)

    @property
    def spend_cost_vb(self) -> float:
        if self.address_type == 'WITNESS_PUBKEY_HASH':
            return 68
        if self.address_type == 'NESTED_PUBKEY_HASH':
            return 68
        if self.address_type == 'TAPROOT_PUBKEY':
            return 57.5
        return 0


class NodeBase(ABC):
    @abstractmethod
    def __init__(
            self,
            rest_host: str,
            permissions_file_path: str,
            cert_file_path: str):
        pass

    @abstractmethod
    def check_node_connection(self) -> Coroutine[None, None, NodeStatusResponse]:
        pass

    @abstractmethod
    def get_node_id(self) -> Coroutine[None, None, GetNodeIdResponse]:
        pass

    @abstractmethod
    def get_node_properties(self) -> Coroutine[None, None, GetNodePropertyResponse]:
        pass

    @abstractmethod
    def create_hodl_invoice(
            self,
            hash: str,
            amt: int) -> Coroutine[None, None, HodlInvoiceResponse]:
        pass

    @abstractmethod
    def subscribe_to_hodl_invoice(self) -> AsyncIterator[PaymentStatus]:
        pass

    @abstractmethod
    def settle_hodl_invoice(
            self,
            preimage: str) -> Coroutine[None, None, PaymentStatus]:
        pass

    @abstractmethod
    def connect_peer(
            self,
            pubkey_uri: str) -> Coroutine[None, None, ConnectPeerResponse]:
        pass

    @abstractmethod
    def open_channel(self, order: Order) -> Coroutine[None, None, ChannelOpenResponse]:
        pass

    @abstractmethod
    def close_rest_client(self) -> Coroutine[None, None, None]:
        pass

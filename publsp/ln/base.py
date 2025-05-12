from abc import ABC, abstractmethod
from typing import AsyncIterator, Coroutine

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

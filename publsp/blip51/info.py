"""
rough implementation of
https://github.com/lightning/blips/blob/master/blip-0051.md#1-lsps1get_info
but adapted for nostr
"""
from pydantic import BaseModel, Field
from typing import Optional, List

from publsp.settings import AdSettings
from publsp.blip51.mixins import NostrTagsMixin


class Ad(BaseModel, NostrTagsMixin):
    d: Optional[str] = Field(default=None)  # unique offer id
    lsp_pubkey: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=AdSettings().status)
    min_required_channel_confirmations: int = AdSettings().min_required_channel_confirmations
    min_funding_confirms_within_blocks: int = AdSettings().min_funding_confirms_within_blocks
    supports_zero_channel_reserve: bool = AdSettings().supports_zero_channel_reserve
    max_channel_expiry_blocks: int = AdSettings().max_channel_expiry_blocks
    min_initial_client_balance_sat: int = AdSettings().min_initial_client_balance_sat
    max_initial_client_balance_sat: int = AdSettings().max_initial_client_balance_sat
    min_initial_lsp_balance_sat: int = AdSettings().min_initial_lsp_balance_sat
    max_initial_lsp_balance_sat: int = AdSettings().max_initial_lsp_balance_sat
    min_channel_balance_sat: int = AdSettings().min_channel_balance_sat
    max_channel_balance_sat: int = AdSettings().max_channel_balance_sat
    fixed_cost_sats: int = AdSettings().fixed_cost_sats
    variable_cost_ppm: int = AdSettings().variable_cost_ppm
    max_promised_fee_rate: int = AdSettings().max_promised_fee_rate
    max_promised_base_fee: int = AdSettings().max_promised_base_fee


class AdList(BaseModel):
    ads: List[Ad]


class CostEstimate(BaseModel):
    d: str
    lsp_pubkey: str
    total_cost_sats: int
    sats_per_block: float
    annualized_rate_percent: float
    min_channel_balance_sat: int
    max_channel_balance_sat: int


class CostEstimateList(BaseModel):
    estimates: List[CostEstimate]

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel

from publsp.api.session import UserSession
from publsp.api.utils import get_user_session
from publsp.blip51.info import Ad, CostEstimate, CostEstimateList


class AdMetaInfo(Ad):
    nostr_pubkey: Optional[str] = None
    value_prop: Optional[str] = None
    lsp_alias: Optional[str] = None
    total_capacity: Optional[int] = None
    num_channels: Optional[int] = None
    median_outbound_ppm: Optional[int] = None
    median_inbound_ppm: Optional[int] = None


class AdList(BaseModel):
    ads: List[AdMetaInfo]


router = APIRouter(prefix="/ads", tags=["LSP Ads"])


@router.get("/list", response_model=AdList)
async def list_ads(
    refresh: bool = Query(False, description="Refresh ad list from nostr"),
    session: UserSession = Depends(get_user_session)
):
    """Get all available LSP ads"""
    if not session.initialized:
        await session.initialize()

    # Refresh ad info if requested or if no ads available
    if refresh or not session.customer_handler.active_ads:
        await session.customer_handler.get_ad_info()

    # Get ads
    ads_data = session.customer_handler.active_ads
    if not (ads_data and ads_data.ads):
        return AdList(ads=[])

    # Convert to enhanced response format
    enhanced_ads = []
    for ad_id, ad in ads_data.ads.items():
        # Get nostr pubkey for this ad
        nostr_pubkey = ads_data.get_nostr_pubkey(ad_id=ad_id)

        # Parse event content to get additional info
        event_content = ads_data.parse_event_content(ad_id=ad_id)
        node_info = event_content.get('node_stats', {})
        value_prop = event_content.get('lsp_message')

        enhanced_ad = AdMetaInfo(
            # Core Ad fields
            d=ad_id,
            lsp_pubkey=ad.lsp_pubkey,
            fixed_cost_sats=ad.fixed_cost_sats,
            variable_cost_ppm=ad.variable_cost_ppm,
            min_channel_balance_sat=ad.min_channel_balance_sat,
            max_channel_balance_sat=ad.max_channel_balance_sat,
            min_initial_lsp_balance_sat=ad.min_initial_lsp_balance_sat,
            max_initial_lsp_balance_sat=ad.max_initial_lsp_balance_sat,
            min_initial_client_balance_sat=ad.min_initial_client_balance_sat,
            max_initial_client_balance_sat=ad.max_initial_client_balance_sat,
            max_channel_expiry_blocks=ad.max_channel_expiry_blocks,
            supports_zero_channel_reserve=ad.supports_zero_channel_reserve,
            min_required_channel_confirmations=ad.min_required_channel_confirmations,
            min_funding_confirms_within_blocks=ad.min_funding_confirms_within_blocks,
            max_promised_fee_rate=ad.max_promised_fee_rate,
            max_promised_base_fee=ad.max_promised_base_fee,

            # Additional event content fields
            nostr_pubkey=nostr_pubkey,
            value_prop=value_prop,
            lsp_alias=node_info.get("alias"),
            total_capacity=node_info.get("total_capacity"),
            num_channels=node_info.get("num_channels"),
            median_outbound_ppm=node_info.get("median_outbound_ppm"),
            median_inbound_ppm=node_info.get("median_inbound_ppm")
        )
        enhanced_ads.append(enhanced_ad)

    return AdList(ads=enhanced_ads)


@router.get("/cost-breakdown", response_model=CostEstimateList)
async def estimate_costs_all_ads(
    capacity: int = Query(..., description="Total channel capacity in sats"),
    session: UserSession = Depends(get_user_session)
):
    """Estimate the cost for a channel with the given capacity across all
    available ads"""
    if not session.initialized:
        await session.initialize()

    # Ensure ad info is available
    if not session.customer_handler.active_ads:
        await session.customer_handler.get_ad_info()

    # Get all ads
    ads_data = session.customer_handler.active_ads
    if not (ads_data and ads_data.ads):
        return CostEstimateList(estimates=[])

    estimates = []
    yearly_mined_blocks = int(24*60/10*365)  # ~52560 blocks per year mined

    # Calculate costs for each ad
    for ad_id, ad in ads_data.ads.items():
        # Skip ads where capacity is outside the valid range
        if capacity < ad.min_channel_balance_sat \
                or capacity > ad.max_channel_balance_sat:
            continue

        total_lease_cost = int(ad.fixed_cost_sats + ad.variable_cost_ppm*1e-6*capacity)
        sats_per_block = round(total_lease_cost/ad.max_channel_expiry_blocks, 3)
        annual_rate = round(
            (total_lease_cost / capacity) * (yearly_mined_blocks / ad.max_channel_expiry_blocks) * 100,
            2
        )

        estimates.append(CostEstimate(
            d=ad_id,
            lsp_pubkey=ad.lsp_pubkey,
            total_cost_sats=total_lease_cost,
            sats_per_block=sats_per_block,
            annualized_rate_percent=annual_rate,
            min_channel_balance_sat=ad.min_channel_balance_sat,
            max_channel_balance_sat=ad.max_channel_balance_sat
        ))

    # Sort estimates by total cost (cheapest first)
    estimates.sort(key=lambda x: x.total_cost_sats)

    return CostEstimateList(estimates=estimates)


@router.get("/cost-breakdown/{ad_id}", response_model=CostEstimate)
async def estimate_cost(
    ad_id: str,
    capacity: int = Query(..., description="Total channel capacity in sats"),
    session: UserSession = Depends(get_user_session)
):
    """Estimate the cost of a channel with the given capacity"""
    if not session.initialized:
        await session.initialize()

    # Ensure ad info is available
    if not session.customer_handler.active_ads:
        await session.customer_handler.get_ad_info()

    # Get the ad
    ads = session.customer_handler.active_ads
    if not (ads and ads.ads and ad_id in ads.ads):
        raise HTTPException(status_code=404, detail=f"Ad ID {ad_id} not found")

    ad = ads.ads[ad_id]

    # Check capacity constraints
    if capacity < ad.min_channel_balance_sat \
            or capacity > ad.max_channel_balance_sat:
        raise HTTPException(
            status_code=400,
            detail=f"Capacity {capacity} outside valid range "
            f"({ad.min_channel_balance_sat}-{ad.max_channel_balance_sat})"
        )

    # Calculate costs
    yearly_mined_blocks = int(24*60/10*365)  # ~52560 blocks per year mined
    total_lease_cost = int(ad.fixed_cost_sats + ad.variable_cost_ppm*1e-6*capacity)
    sats_per_block = round(total_lease_cost/ad.max_channel_expiry_blocks, 3)
    annual_rate = round(
        (total_lease_cost / capacity) * (yearly_mined_blocks / ad.max_channel_expiry_blocks) * 100,
        2
    )

    return CostEstimate(
        d=ad_id,
        lsp_pubkey=ad.lsp_pubkey,
        total_cost_sats=total_lease_cost,
        sats_per_block=sats_per_block,
        annualized_rate_percent=annual_rate,
        min_channel_balance_sat=ad.min_channel_balance_sat,
        max_channel_balance_sat=ad.max_channel_balance_sat
    )


@router.get("/list/{ad_id}", response_model=AdMetaInfo)
async def get_ad_by_id(
        ad_id: str,
        session: UserSession = Depends(get_user_session)):
    """Get a specific LSP ad by its ID with enhanced information"""
    if not session.initialized:
        await session.initialize()

    # Ensure ad info is available
    if not session.customer_handler.active_ads:
        await session.customer_handler.get_ad_info()

    # Get the ad
    ads_data = session.customer_handler.active_ads
    if not (ads_data and ads_data.ads and ad_id in ads_data.ads):
        raise HTTPException(status_code=404, detail=f"Ad ID {ad_id} not found")

    ad = ads_data.ads[ad_id]

    # Get additional information
    nostr_pubkey = ads_data.get_nostr_pubkey(ad_id=ad_id)
    event_content = ads_data.parse_event_content(ad_id=ad_id)
    node_info = event_content.get('node_stats', {})
    value_prop = event_content.get('lsp_message')

    return AdMetaInfo(
        # Core Ad fields
        d=ad_id,
        lsp_pubkey=ad.lsp_pubkey,
        fixed_cost_sats=ad.fixed_cost_sats,
        variable_cost_ppm=ad.variable_cost_ppm,
        min_channel_balance_sat=ad.min_channel_balance_sat,
        max_channel_balance_sat=ad.max_channel_balance_sat,
        min_initial_lsp_balance_sat=ad.min_initial_lsp_balance_sat,
        max_initial_lsp_balance_sat=ad.max_initial_lsp_balance_sat,
        min_initial_client_balance_sat=ad.min_initial_client_balance_sat,
        max_initial_client_balance_sat=ad.max_initial_client_balance_sat,
        max_channel_expiry_blocks=ad.max_channel_expiry_blocks,
        supports_zero_channel_reserve=ad.supports_zero_channel_reserve,
        min_required_channel_confirmations=ad.min_required_channel_confirmations,
        min_funding_confirms_within_blocks=ad.min_funding_confirms_within_blocks,
        max_promised_fee_rate=ad.max_promised_fee_rate,
        max_promised_base_fee=ad.max_promised_base_fee,

        # Additional event content fields
        nostr_pubkey=nostr_pubkey,
        value_prop=value_prop,
        lsp_alias=node_info.get("alias"),
        total_capacity=node_info.get("total_capacity"),
        num_channels=node_info.get("num_channels"),
        median_outbound_ppm=node_info.get("median_outbound_ppm"),
        median_inbound_ppm=node_info.get("median_inbound_ppm")
    )

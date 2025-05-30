from fastapi import APIRouter, Depends, HTTPException
from typing import Union

from publsp.api.session import UserSession
from publsp.api.utils import get_user_session
from publsp.blip51.order import Order, OrderResponse, OrderErrorResponse

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("/create", response_model=Union[OrderResponse, OrderErrorResponse])
async def create_order(
    order: Order,  # Use the existing Order model directly
    session: UserSession = Depends(get_user_session)
):
    """Create a new order with an LSP"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Order request received: {order.model_dump()}")

    if not session.initialized:
        await session.initialize()
        logger.info("Session initialized")

    # Get ad information
    ads = session.customer_handler.active_ads
    if not (ads and ads.ads):
        logger.info("No ads found, refreshing ad info")
        await session.customer_handler.get_ad_info()
        ads = session.customer_handler.active_ads
        if not (ads and ads.ads):
            logger.error("No ads available after refresh")
            raise HTTPException(status_code=404, detail="No ads available")

    # Log ad IDs for debugging
    ad_ids = list(ads.ads.keys())
    logger.info(f"Available ad IDs: {ad_ids}")

    # Check if the requested ad ID exists
    if order.d not in ads.ads:
        logger.error(f"Ad ID {order.d} not found in available ads")
        raise HTTPException(status_code=404, detail=f"Ad {order.d} not found")

    # Get the ad
    ad = ads.ads[order.d]
    logger.info(f"Found ad: {ad.d}, LSP pubkey: {ad.lsp_pubkey}")

    # Register the selected ad with the handler
    session.order_response_handler.selected_ad = ad
    logger.info("Selected ad registered with handler")

    # Update the handler with the current order parameters for validation
    session.order_response_handler.opts.update({
        'lsp_balance_sat': order.lsp_balance_sat,
        'client_balance_sat': order.client_balance_sat
    })
    logger.info(
        "Updated handler opts with order parameters: "
        f"lsp_balance_sat={order.lsp_balance_sat}, "
        f"client_balance_sat={order.client_balance_sat}")

    # Validate the channel capacity
    total_capacity = order.lsp_balance_sat + order.client_balance_sat
    logger.info(
        f"Total capacity: {total_capacity}, "
        f"min: {ad.min_channel_balance_sat}, "
        f"max: {ad.max_channel_balance_sat}")

    if total_capacity < ad.min_channel_balance_sat:
        logger.error(f"Capacity too low: {total_capacity} < {ad.min_channel_balance_sat}")
        raise HTTPException(
            status_code=400,
            detail=f"Total channel capacity ({total_capacity}) is below minimum ({ad.min_channel_balance_sat})"
        )
    if total_capacity > ad.max_channel_balance_sat:
        logger.error(f"Capacity too high: {total_capacity} > {ad.max_channel_balance_sat}")
        raise HTTPException(
            status_code=400,
            detail=f"Total channel capacity ({total_capacity}) is above maximum ({ad.max_channel_balance_sat})"
        )

    try:
        # Get the peer's public key
        peer_pk = ads.get_nostr_pubkey(ad_id=order.d, as_PublicKey=True)
        logger.info(f"Peer public key: {peer_pk}")

        # Send the order request
        logger.info("Sending order request via nostr")
        order_tags = order.model_dump_tags()
        logger.info(f"Order tags: {order_tags}")
        await session.nostr_client.send_private_msg(
            peer_pk,
            "order request",
            rumor_extra_tags=order_tags,
        )
        logger.info("Order request sent, waiting for LSP response...")

        # Wait for the response
        response = await session.response_queue_manager.wait_for_next_response(
            "order",
            timeout=30.0
        )
        logger.info(f"Response received: {response}")

        # Handle timeout
        if not response:
            logger.error("Timeout waiting for LSP response")
            raise HTTPException(
                status_code=408,
                detail="No response received from LSP within timeout"
            )

        # Return the actual response
        return response
    except Exception as e:
        logger.exception(f"Error processing order: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing order: {str(e)}"
        )


@router.get("/status", response_model=Union[OrderResponse, OrderErrorResponse])
async def get_latest_order(session: UserSession = Depends(get_user_session)):
    """Get the latest order response for this session"""
    if not session.initialized:
        raise HTTPException(status_code=400, detail="Session not initialized")

    response = session.response_queue_manager.get_latest_response("order")
    if not response:
        raise HTTPException(status_code=404, detail="No order responses received in this session")

    # Return the response directly
    return response

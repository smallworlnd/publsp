from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import asyncio
import logging
from typing import AsyncGenerator

from publsp.api.session import UserSession
from publsp.api.utils import get_user_session
from publsp.ln.requesthandlers import ChannelOpenResponse, ChannelState
from publsp.settings import ApiSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["Channels"])


@router.get("/status", response_model=ChannelOpenResponse)
async def get_latest_channel(session: UserSession = Depends(get_user_session)):
    """Get the latest channel open response for this session"""
    if not session.initialized:
        raise HTTPException(status_code=400, detail="Session not initialized")

    response = session.response_queue_manager.get_latest_response("channel_open")
    if not response:
        raise HTTPException(status_code=404, detail="No channel open responses received in this session")

    return response


async def generate_channel_events(
        session: UserSession,
        max_wait_time: int) -> AsyncGenerator[str, None]:
    """Standalone generator function that yields channel open response
    events"""
    logger.info(f"Starting channel status stream for session {session.session_id}")

    # Track states we've seen to avoid duplicates
    seen_states = set()

    # Maximum time to wait for responses
    max_wait_time_seconds = max_wait_time * 60
    start_time = asyncio.get_event_loop().time()

    while True:
        current_time = asyncio.get_event_loop().time()
        elapsed_time = current_time - start_time

        # Check if we've exceeded the maximum wait time
        if elapsed_time > max_wait_time_seconds:
            logger.info(f"Stream timeout reached after {elapsed_time:.1f}s")
            yield f'{{"error_message": "Stream timeout after {max_wait_time_seconds} seconds"}}\n'
            break

        try:
            logger.info(f"Waiting for channel_open response...")

            # Wait for the next channel response with a shorter timeout for streaming
            response = await session.response_queue_manager.wait_for_next_response(
                "channel_open",
                timeout=10.0  # Shorter timeout for more responsive heartbeats
            )

            if response:
                logger.info(f"Received channel response: {response}")

                # Create a unique identifier for this state
                state_key = (response.channel_state.value, response.txid_hex, response.output_index)

                # Only send if we haven't seen this exact state before
                if state_key not in seen_states:
                    seen_states.add(state_key)

                    logger.info(f"Sending channel_update event: {response}")

                    yield response.model_dump_json() + '\n'

                    if response.channel_state == ChannelState.OPEN:
                        logger.info(f"Channel reached final state: {response.channel_state}")
                        break
                else:
                    logger.info(f"Skipping duplicate state: {state_key}")
                    yield f'{{"event": "duplicate", "message": "Duplicate state skipped", "state": "{response.channel_state.value}"}}\n'
            else:
                # Send heartbeat to keep connection alive
                logger.debug(f"No response received, sending heartbeat at {elapsed_time:.1f}s")
                yield f'{{"timestamp": {current_time}, "elapsed_time": {elapsed_time:.1f}, "waiting_for_response": true}}\n'

        except asyncio.TimeoutError:
            # Send heartbeat on timeout to keep connection alive
            logger.debug(f"Timeout waiting for response, sending heartbeat at {elapsed_time:.1f}s")
            yield f'{{"timestamp": {current_time}, "elapsed_time": {elapsed_time:.1f}, "waiting_for_response": true}}\n'
        except Exception as e:
            logger.error(f"Error in channel status stream: {e}", exc_info=True)
            yield f'{{"error_message": "{str(e)}", "elapsed_time": {elapsed_time:.1f}}}\n'
            break

    logger.info(f"Channel status stream ended after {elapsed_time:.1f}s")


@router.get("/listen-status")
async def stream_channel_status(
        max_wait_time: int = ApiSettings().max_listen_minutes,
        session: UserSession = Depends(get_user_session)):
    """Stream channel open responses as Server-Sent Events"""
    if not session.initialized:
        raise HTTPException(status_code=400, detail="Session not initialized")

    return StreamingResponse(
        generate_channel_events(session, max_wait_time),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

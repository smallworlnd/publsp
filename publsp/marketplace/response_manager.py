import asyncio
from typing import Dict, Optional, Union, Any, List
import logging

logger = logging.getLogger(__name__)

class ResponseQueueManager:
    """
    Manages response queues for different types of responses
    
    This class provides a way to:
    1. Store responses as they arrive from handlers
    2. Retrieve the latest response of a given type
    3. Wait for the next response of a given type
    """
    
    def __init__(self):
        # Latest response of each type
        self.latest_responses: Dict[str, Any] = {}
        
        # Queues for new responses of each type
        self.response_queues: Dict[str, List[asyncio.Queue]] = {}
        
        # Events to notify about new responses
        self.response_events: Dict[str, asyncio.Event] = {}
    
    def register_response_type(self, response_type: str) -> None:
        """Register a new response type to track"""
        if response_type not in self.response_events:
            self.response_events[response_type] = asyncio.Event()
            self.response_queues[response_type] = []
    
    def create_response_waiter(self, response_type: str) -> asyncio.Queue:
        """Create a queue that will receive the next response of this type"""
        if response_type not in self.response_queues:
            self.register_response_type(response_type)
            
        queue = asyncio.Queue(maxsize=1)
        self.response_queues[response_type].append(queue)
        return queue
    
    def store_response(self, response_type: str, response: Any) -> None:
        """
        Store a response and notify waiters
        
        Args:
            response_type: Type of response (e.g., "order", "channel_open")
            response: The response object to store
        """
        logger.info(f"ResponseQueueManager: Storing {response_type} response")
        logger.info(f"ResponseQueueManager: Response content: {response}")
        logger.info(f"ResponseQueueManager: Current queues waiting: {len(self.response_queues.get(response_type, []))}")
        
        # Update the latest response of this type
        self.latest_responses[response_type] = response
        logger.info(f"ResponseQueueManager: Updated latest response for {response_type}")
        
        # Notify all queues waiting for this response type
        queues_to_remove = []
        if response_type in self.response_queues:
            logger.info(f"ResponseQueueManager: Found {len(self.response_queues[response_type])} waiting queues")
            for i, queue in enumerate(self.response_queues[response_type]):
                try:
                    # If queue is full, skip it (the reader is too slow)
                    if queue.full():
                        logger.warning(f"ResponseQueueManager: Queue {i} is full, skipping")
                        queues_to_remove.append(queue)
                        continue
                        
                    queue.put_nowait(response)
                    logger.info(f"ResponseQueueManager: Successfully put response in queue {i}")
                    # Queue served its purpose, mark for removal
                    queues_to_remove.append(queue)
                except Exception as e:
                    logger.error(f"ResponseQueueManager: Error putting response in queue {i}: {e}")
                    queues_to_remove.append(queue)
            
            # Remove queues that have been served
            for queue in queues_to_remove:
                if queue in self.response_queues[response_type]:
                    self.response_queues[response_type].remove(queue)
                    logger.info(f"ResponseQueueManager: Removed served queue")
        else:
            logger.warning(f"ResponseQueueManager: No queues registered for response type {response_type}")
        
        # Set the event to notify waiters
        if response_type in self.response_events:
            self.response_events[response_type].set()
            logger.info(f"ResponseQueueManager: Event set for {response_type}")
            # Immediately clear it so we can wait for the next one
            self.response_events[response_type].clear()
        else:
            logger.warning(f"ResponseQueueManager: No event registered for response type {response_type}")
        
        logger.info(f"ResponseQueueManager: Finished storing {response_type} response")
    
    def get_latest_response(self, response_type: str) -> Optional[Any]:
        """Get the most recent response of a given type"""
        return self.latest_responses.get(response_type)
    
    async def wait_for_next_response(
        self, 
        response_type: str, 
        timeout: Optional[float] = 30.0
    ) -> Optional[Any]:
        """
        Wait for the next response of a given type
        
        Args:
            response_type: Type of response to wait for
            timeout: Timeout in seconds (None for no timeout)
            
        Returns:
            The response object, or None on timeout
        """
        logger.info(f"ResponseQueueManager: Waiting for {response_type} response with timeout {timeout}s")
        
        # Create a queue to receive the response
        queue = self.create_response_waiter(response_type)
        logger.info(f"ResponseQueueManager: Created waiter queue for {response_type}")
        
        try:
            # Wait for the response with timeout
            if timeout is not None:
                logger.info(f"ResponseQueueManager: Starting wait with timeout")
                result = await asyncio.wait_for(queue.get(), timeout)
                logger.info(f"ResponseQueueManager: Received response: {result}")
                return result
            else:
                logger.info(f"ResponseQueueManager: Starting wait without timeout")
                result = await queue.get()
                logger.info(f"ResponseQueueManager: Received response: {result}")
                return result
        except asyncio.TimeoutError:
            logger.error(f"ResponseQueueManager: Timeout waiting for {response_type} response")
            # Remove the queue on timeout
            if response_type in self.response_queues and queue in self.response_queues[response_type]:
                self.response_queues[response_type].remove(queue)
                logger.info(f"ResponseQueueManager: Removed timed-out queue")
            return None
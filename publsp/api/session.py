import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

from publsp.marketplace.customer import CustomerHandler, OrderResponseHandler
from publsp.nostr.client import NostrClient
from publsp.nostr.nip17 import RumorHandler, Nip17Listener
from publsp.marketplace.response_manager import ResponseQueueManager
from publsp.settings import ApiSettings, Interface

logger = logging.getLogger(__name__)


class UserSession:
    """
    Represents a user session with its own set of components

    Each user gets their own unique nostr keys and component instances
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.session_id = str(uuid.uuid4())
        self.created_at = datetime.now()
        self.last_accessed = datetime.now()

        # Components will be initialized later
        self.nostr_client = None
        self.rumor_handler = None
        self.customer_handler = None
        self.order_response_handler = None
        self.response_queue_manager = ResponseQueueManager()
        self.nip17_listener = None

        # Track initialization state
        self.initialized = False
        self.initialization_lock = asyncio.Lock()

    async def initialize(self, **kwargs):
        """Initialize components for this session"""
        # Use a lock to prevent multiple initializations
        async with self.initialization_lock:
            if self.initialized:
                return

            # Create components with unique nostr keys for this session
            # The reuse_keys=False ensures we get fresh keys for each session
            self.nostr_client = NostrClient(
                client_for="customer",
                reuse_keys=False,
                write_keys=False,
                ask_encrypt=False
            )
            self.rumor_handler = RumorHandler()
            self.customer_handler = CustomerHandler(
                nostr_client=self.nostr_client,
                **kwargs
            )
            self.order_response_handler = OrderResponseHandler(
                customer_handler=self.customer_handler,
                rumor_handler=self.rumor_handler,
                response_queue_manager=self.response_queue_manager,
                output_interface=Interface.API,
                **kwargs
            )

            # Connect to nostr relays and start listeners
            await self.nostr_client.connect_relays()
            await self.customer_handler.get_ad_info()

            # Start the listeners
            self.nip17_listener = Nip17Listener(
                nostr_client=self.nostr_client,
                rumor_handler=self.rumor_handler,
            )
            self.nip17_listener.start()
            self.order_response_handler.start()

            self.initialized = True
            npub = self.nostr_client.get_npub()
            self.user_id = npub
            logger.info(
                f'Session {self.session_id} for user {self.user_id} '
                f'initialized with nostr pubkey: {npub}')

    async def cleanup(self):
        """Clean up all resources for this session"""
        if not self.initialized:
            return

        logger.info(
            f"Cleaning up session {self.session_id} "
            f"for user {self.user_id}")

        # Stop listeners
        if self.nip17_listener:
            await self.nip17_listener.stop()

        if self.order_response_handler:
            await self.order_response_handler.stop()

        # Disconnect from relays
        if self.nostr_client:
            await self.nostr_client.disconnect_relays()

        self.initialized = False

    def update_last_accessed(self):
        """Update the last accessed timestamp"""
        self.last_accessed = datetime.now()

    def is_expired(
            self,
            max_idle_minutes: int = ApiSettings().max_idle_minutes) -> bool:
        """Check if this session has expired due to inactivity"""
        idle_delta = datetime.now() - self.last_accessed
        return idle_delta > timedelta(minutes=max_idle_minutes)


class SessionManager:
    """Manages user sessions"""

    def __init__(self):
        self.sessions: Dict[str, UserSession] = {}
        self.user_sessions: Dict[str, Dict[str, UserSession]] = {}
        self.pubkey_sessions: Dict[str, UserSession] = {}
        self.maintenance_task = None

    async def get_or_create_session(
            self,
            user_id: str,
            **kwargs) -> UserSession:
        """Get an existing session for a user or create a new one"""
        # Create user dict if it doesn't exist
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {}

        # Create a new session if the user has no active sessions
        if not self.user_sessions[user_id]:
            session = UserSession(user_id)
            self.sessions[session.session_id] = session
            self.user_sessions[user_id][session.session_id] = session
            await session.initialize(**kwargs)

            # Map the nostr pubkey to the session for easy lookup
            if session.nostr_client:
                npub = session.nostr_client.get_npub()
                self.pubkey_sessions[npub] = session

            return session

        # Get the first active session for the user
        session_id = next(iter(self.user_sessions[user_id].keys()))
        session = self.user_sessions[user_id][session_id]
        session.update_last_accessed()

        # Make sure the session is initialized
        if not session.initialized:
            await session.initialize(**kwargs)
            # Map the nostr pubkey to the session
            if session.nostr_client:
                npub = session.nostr_client.get_npub()
                self.pubkey_sessions[npub] = session

        return session

    def get_session(self, session_id: str) -> Optional[UserSession]:
        """Get a session by its ID"""
        session = self.sessions.get(session_id)
        if session:
            session.update_last_accessed()
        return session

    async def create_new_session(self, user_id: str, **kwargs) -> UserSession:
        """Create a new session regardless of existing sessions"""
        # Create a new session
        session = UserSession(user_id)
        self.sessions[session.session_id] = session

        # Make sure user dict exists
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {}

        # Add to user sessions
        self.user_sessions[user_id][session.session_id] = session

        # Initialize the session
        await session.initialize(**kwargs)

        # Map the nostr pubkey to the session for easy lookup
        if session.nostr_client:
            npub = session.nostr_client.get_npub()
            self.pubkey_sessions[npub] = session

        return session

    async def cleanup_session(self, session_id: str) -> bool:
        """Clean up and remove a specific session"""
        session = self.sessions.get(session_id)
        if not session:
            return False

        # Remove from pubkey mapping if initialized
        if session.initialized and session.nostr_client:
            npub = session.nostr_client.get_npub()
            if npub in self.pubkey_sessions:
                del self.pubkey_sessions[npub]

        await session.cleanup()

        # Remove from both dictionaries
        del self.sessions[session_id]
        if session.user_id in self.user_sessions:
            if session_id in self.user_sessions[session.user_id]:
                del self.user_sessions[session.user_id][session_id]

            # Clean up empty user entries
            if not self.user_sessions[session.user_id]:
                del self.user_sessions[session.user_id]

        return True

    async def start_maintenance(
            self,
            interval_minutes: int = ApiSettings().interval_minutes,
            max_idle_minutes: int = ApiSettings().max_idle_minutes):
        """Start a background task to clean up expired sessions"""
        async def maintenance_loop():
            while True:
                try:
                    await self.cleanup_expired_sessions(max_idle_minutes)
                except Exception as e:
                    logger.error(f"Error in session maintenance: {e}")

                await asyncio.sleep(interval_minutes * 60)

        self.maintenance_task = asyncio.create_task(maintenance_loop())

    async def cleanup_expired_sessions(
            self,
            max_idle_minutes: int = ApiSettings().max_idle_minutes) -> int:
        """Clean up all expired sessions"""
        expired_sessions = [
            session_id for session_id, session in self.sessions.items()
            if session.is_expired(max_idle_minutes)
        ]

        count = 0
        for session_id in expired_sessions:
            if await self.cleanup_session(session_id):
                count += 1

        if count > 0:
            logger.info(f"Cleaned up {count} expired sessions")

        return count

    async def stop_maintenance(self):
        """Stop the maintenance task"""
        if self.maintenance_task:
            self.maintenance_task.cancel()
            try:
                await self.maintenance_task
            except asyncio.CancelledError:
                pass
            self.maintenance_task = None

    async def shutdown(self):
        """Clean up all sessions and stop maintenance"""
        await self.stop_maintenance()

        # Make a copy of the keys to avoid modifying during iteration
        session_ids = list(self.sessions.keys())
        for session_id in session_ids:
            await self.cleanup_session(session_id)


# Create a global session manager instance
session_manager = SessionManager()

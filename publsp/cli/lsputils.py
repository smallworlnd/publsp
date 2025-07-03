import asyncio
from typing import Optional, Union

from publsp.ln.lnd import LndBackend
from publsp.marketplace.lsp import AdHandler
from publsp.settings import AdStatus

import logging
logger = logging.getLogger(name=__name__)


class HealthChecker:
    """
    Periodically checks the Lightning Node connection and updates nostr events
    depending on outcome
    """
    def __init__(
            self,
            ad_handler: AdHandler,
            ln_backend: Union[LndBackend],
            health_check_time: int = 60):
        self.ln_backend = ln_backend
        self.ad_handler = ad_handler
        self.health_check_time = health_check_time
        self._health_check_task: Optional[asyncio.Task] = None
        self._running = False

    async def _check_node_health(self):
        """
        Periodically checks the Lightning Node connection and updates nostr
        events depending on outcome

        Unhealthy (out of sync with graph or chain), or lost connection to ln
        backend then send event to inactivate ad and wait till node becomes
        healthy again or back online, then send new event to 'activate' ad
        again
        """
        while self._running:
            logger.debug("running ln node health check...")
            try:
                connection_status = await self.ln_backend.check_node_connection()

                if connection_status.healthy:
                    logger.debug(f"ln node is healthy: {connection_status}")
                    if not self.ad_handler.active_ads:
                        # if no active ads it's likely at startup so skip the
                        # check and wait the health check time
                        await asyncio.sleep(self.health_check_time)
                    for ad in self.ad_handler.active_ads.ads.values():
                        if ad.status != AdStatus.ACTIVE:
                            logger.info("republishing ads")
                            await self.ad_handler.publish_ad()
                else:
                    logger.error(f"ln node connection NOT healthy: {connection_status}")
                    ad_statuses = [ad.status for ad in self.ad_handler.active_ads.ads.values()]
                    if AdStatus.ACTIVE in ad_statuses:
                        logger.warning('deactivating ad until ln node becomes healthy again')
                        await self.ad_handler.inactivate_ads()
                logger.debug(f'checking again in {self.health_check_time}s')
            except Exception as e:
                logger.error(f"Error during Lightning Node health check: {e}")
                try:
                    ad_statuses = [ad.status for ad in self.ad_handler.active_ads.ads.values()]
                    # if any ads are active, then send an updated ad event to
                    # inactivate them
                    if AdStatus.ACTIVE in ad_statuses:
                        logger.warning('Deactivating ads until node becomes healthy')
                        await self.ad_handler.inactivate_ads()
                except Exception as err:
                    logger.error(f'could not update ad events with inactivate: {err}')
                logger.info(f'checking again in {self.health_check_time}s')
                await asyncio.sleep(self.health_check_time)

            await asyncio.sleep(self.health_check_time)

    async def start(self):
        """
        Starts the periodic health check task.
        """
        if not self._running:
            self._running = True
            self._health_check_task = asyncio.create_task(self._check_node_health())
            logger.info("HealthChecker started.")

    async def stop(self):
        """
        Stops the periodic health check task.
        """
        if self._running and self._health_check_task:
            logger.info("Stopping HealthChecker...")
            self._running = False
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                logger.info("HealthChecker task cancelled successfully.")
            except Exception as e:
                logger.error(f"Error while stopping HealthChecker task: {e}")
            self._health_check_task = None

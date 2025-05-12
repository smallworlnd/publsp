import pytest
from publsp.settings import AdStatus


@pytest.mark.asyncio
async def test_publish_and_update_ad(ad_handler):
    await ad_handler.publish_ad()
    ad_id = '29cff27c-ec05-b50b-fc6c-0a2ca3063d6e'
    assert ad_handler.active_ads.ads[ad_id].status == AdStatus.ACTIVE
    await ad_handler.update_ad_events()
    assert ad_handler.active_ads.ads[ad_id].status == AdStatus.INACTIVE

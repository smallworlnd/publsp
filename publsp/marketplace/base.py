import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from nostr_sdk import (
    Event, Events,
    Filter,
    PublicKey,
)
from typing import List, Dict, Tuple, Union

from publsp.blip51.info import Ad
from publsp.nostr.kinds import PublspKind
from publsp.nostr.client import NostrClient

logger = logging.getLogger(name=__name__)


@dataclass
class AdEventData:
    """ad_id is the uuid of each ad"""
    ads: Dict[str, Ad]
    ad_events: Dict[str, Event]

    def get_nostr_pubkey(
            self,
            ad_id: str,
            as_PublicKey: bool = False) -> Union[str, PublicKey]:
        if as_PublicKey:
            return self.ad_events[ad_id].author()
        else:
            return self.ad_events[ad_id].author().to_hex()

    def parse_event_content(self, ad_id: str) -> Dict[str, str]:
        return json.loads(self.ad_events[ad_id].content())

    def get_event_id(self, ad_id: str) -> str:
        return self.ad_events[ad_id].id().to_hex()

    def __str__(self):
        indent = 38
        formatted_output = str()
        for ad_id, ad in self.ads.items():
            nostr_pubkey = self.get_nostr_pubkey(ad_id=ad_id)
            event_content = self.parse_event_content(ad_id=ad_id)
            node_info = event_content.get('node_stats')
            value_prop = event_content.get('lsp_message')
            formatted_output += (
                f'\n{"d ID": <{indent}}{ad_id}\n'
                f'{"Value proposition": <{indent}}{value_prop}\n'
                f'{"Nostr pubkey": <{indent}}{nostr_pubkey}\n'
                f'{"LSP pubkey": <{indent}}{ad.lsp_pubkey}\n'
                f'{"LSP alias": <{indent}}{node_info.get("alias")}\n'
                f'{"LSP total capacity (sats)": <{indent}}{node_info.get("total_capacity")}\n'
                f'{"LSP number of channels": <{indent}}{node_info.get("num_channels")}\n'
                f'{"LSP median outbound fee rate (ppm)": <{indent}}{node_info.get("median_outbound_ppm")}\n'
                f'{"LSP median inbound fee rate (ppm)": <{indent}}{node_info.get("median_inbound_ppm")}\n'
                f'{"Min required channel confirmations": <{indent}}'
                f'{ad.min_required_channel_confirmations}\n'
                f'{"Min funding confirms within blocks": <{indent}}'
                f'{ad.min_funding_confirms_within_blocks}\n'
                f'{"Supports zero channel reserve": <{indent}}'
                f'{ad.supports_zero_channel_reserve}\n'
                f'{"Max channel expiry in blocks": <{indent}}'
                f'{ad.max_channel_expiry_blocks}\n'
                f'{"Min initial client balance (sats)": <{indent}}'
                f'{ad.min_initial_client_balance_sat}\n'
                f'{"Max initial client balance (sats)": <{indent}}'
                f'{ad.max_initial_client_balance_sat}\n'
                f'{"Min initial LSP balance (sats)": <{indent}}'
                f'{ad.min_initial_lsp_balance_sat}\n'
                f'{"Max initial LSP balance (sats)": <{indent}}'
                f'{ad.max_initial_lsp_balance_sat}\n'
                f'{"Min channel capacity (sats)": <{indent}}'
                f'{ad.min_channel_balance_sat}\n'
                f'{"Max channel capacity (sats)": <{indent}}'
                f'{ad.max_channel_balance_sat}\n'
                f'{"Fixed opening cost (sats)": <{indent}}{ad.fixed_cost_sats}\n'
                f'{"Variable opening cost (ppm)": <{indent}}'
                f'{ad.variable_cost_ppm}\n'
                f'{"Max promised fee rate (ppm)": <{indent}}'
                f'{ad.max_promised_fee_rate}\n'
                f'{"Max promised base fee (sat)": <{indent}}'
                f'{ad.max_promised_base_fee}\n'
            )
        return formatted_output


class MarketplaceAgent(ABC):
    kind: PublspKind

    @abstractmethod
    def __init__(
            self,
            nostr_client: NostrClient,
            **kwargs):
        pass

    async def get_ad_events(self, self_ads: bool = False) -> Events:
        ads_filter = Filter()\
            .kind(self.kind.AD.as_kind_obj)
        if self_ads:
            ads_filter = ads_filter.\
                authors([self.nostr_client.key_handler.keys.public_key()])
        events = await self.nostr_client\
            .fetch_events(ads_filter, timedelta(seconds=10))

        return events

    def filter_ad_events(self, events: Events) -> [Event]:
        """
        # 1. keep events whose tags match an Ad object
        # 2. filter out ads that have 'status' inactive
        # 3. then keep only the latest unique ad ids per lsp
        * implementation of this logic is clumsy since we're mostly dealing
        with non-standard tags not cmopatible with Event filters that strictly
        require TagKind objects
        """
        required_keys = set(Ad.model_fields.keys())

        evs_and_tags: List[tuple[Event, Dict[str,str]]] = []
        for ev in events.to_vec():
            # build a dict of this event's tags
            tag_pairs = [tag.as_vec() for tag in ev.tags().to_vec()]
            tags = {k: v for k, v in tag_pairs}

            # step 1: does it have every required tag?
            if not required_keys.issubset(tags):
                continue

            # step 2: drop inactive
            if not tags.get("lsp_pubkey") or tags.get("status", "").lower() == "inactive":
                continue

            evs_and_tags.append((ev, tags))

        # 3) group by lsp_pubkey and pick the newest per group
        latest_by_pair: Dict[Tuple[str,str], Event] = {}
        for ev, tags in evs_and_tags:
            pair = (tags["lsp_pubkey"], tags["d"])
            prev = latest_by_pair.get(pair)
            # if no existing, or this one is newer, replace it
            if prev is None or ev.created_at().as_secs() > prev.created_at().as_secs():
                latest_by_pair[pair] = ev

        # return just the Events
        return list(latest_by_pair.values())

    def parse_filtered_ads(self, ad_events: [Event]) -> AdEventData:
        ads = {}
        events = {}
        for ad_event in ad_events:
            ad_tags = ad_event.tags().to_vec()
            lsp_ad = Ad.model_from_tags(tags=ad_tags)
            ads[lsp_ad.d] = lsp_ad
            events[lsp_ad.d] = ad_event

        return AdEventData(ads=ads, ad_events=events)

    async def get_ad_info(self, self_ads: bool = False) -> None:
        """
        wrap the getting of events of a given kind, filtering those events per
        required tags, and then building Ad dataclass objects from the tags
        i.e., wrap steps 1-3
        """
        # 1. get all events
        ads = await self.get_ad_events(self_ads=self_ads)
        # 2. filter events
        active_ad_events = self.filter_ad_events(events=ads)
        # 3. create dataclass objects from filtered event tags
        self.active_ads = self.parse_filtered_ads(ad_events=active_ad_events)

import ipaddress
import os
import re
import socket
from enum import Enum
from pathlib import Path
from pydantic import (
    Field,
    FilePath,
    field_validator,
    field_serializer,
    HttpUrl,
    model_validator,
    StringConstraints,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource
from typing import List, Optional
from typing_extensions import Annotated

VERSION = '0.4.20'
AD_ID_REGEX = r'(?:[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})?'
ONION_RE = re.compile(r"^(?:[a-z2-7]{16}|[a-z2-7]{56})\.onion$", re.IGNORECASE)
PUBKEY_RE = re.compile(r"^[0-9A-Fa-f]{66}$")


class Environment(str, Enum):
    PROD = 'production'
    DEV = 'development'


class Interface(str, Enum):
    CLI = "cli"
    API = "api"


class LnImplementation(str, Enum):
    LND = 'lnd'
    CLN = 'cln'  # not yet supported
    ECLAIR = 'eclair'  # not yet supported
    LDK = 'ldk'  # not yet supported

    @classmethod
    def supported(cls) -> list["LnImplementation"]:
        # only LND is implemented today
        return [cls.LND]

    @classmethod
    def choices(cls) -> list[str]:
        # strings for click.Choice
        return [impl.value for impl in cls.supported()]


class AdStatus(str, Enum):
    ACTIVE = 'active'
    INACTIVE = 'inactive'


class LogLevel(str, Enum):
    DEBUG = 'DEBUG'
    INFO = 'INFO'
    WARNING = 'WARNING'
    ERROR = 'ERROR'
    CRITICAL = 'CRITICAL'
    # NOTSET = 'NOTSET'


class PublspSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )
    log_level: LogLevel = LogLevel.INFO
    interface: Interface = Interface.CLI

    # Field to expose which env file is actually being used
    env_file: str = Field(default=".env", description="Path to the env file being used")

    def __init__(self, **kwargs):
        # Determine which env file will be used using the same logic as settings_customise_sources
        self._determined_env_file = self._determine_env_file()
        # Set the env_file field to the determined value
        kwargs.setdefault('env_file', self._determined_env_file)
        super().__init__(**kwargs)

    @classmethod
    def _determine_env_file(cls) -> str:
        """Determine which env file to use based on ENVIRONMENT setting in base .env"""
        base_path = Path(".env")
        if base_path.is_file():
            base_vars = DotEnvSettingsSource._static_read_env_file(
                base_path,
                encoding="utf-8",
                case_sensitive=False,
                ignore_empty=False,
                parse_none_str=None,
            )
        else:
            base_vars = {}

        # Choose .env.dev or .env using the same logic as settings_customise_sources
        env = base_vars.get("environment", Environment.PROD.value)
        return ".env.dev" if env.upper() == Environment.DEV.name else ".env"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings
    ):
        # 1) peek at your base .env for the ENVIRONMENT key
        base_path = Path(".env")
        if base_path.is_file():
            base_vars = DotEnvSettingsSource._static_read_env_file(
                base_path,
                encoding="utf-8",
                case_sensitive=False,
                ignore_empty=False,
                parse_none_str=None,
            )
        else:
            base_vars = {}

        # 2) choose .env.dev or .env
        env = base_vars.get("environment", Environment.PROD.value)
        chosen = ".env.dev" if env.upper() == Environment.DEV.name else ".env"

        # 3) build a new DotEnvSettingsSource pointing at that file
        custom_dotenv = DotEnvSettingsSource(
            settings_cls=cls,
            env_file=chosen,
            env_file_encoding="utf-8",
        )
        def filtered_dotenv() -> dict[str, any]:
            data = custom_dotenv()
            # remove any k where v is the empty-string
            return {k: v for k, v in data.items() if v != ""}

        return (
            init_settings,
            filtered_dotenv,
            env_settings,
            file_secret_settings,
        )


class EnvironmentSettings(PublspSettings):
    environment: Environment = Environment.PROD

    @field_validator('environment', mode='before')
    def validate_env(cls, value):
        # 1) If it's already an Environment member, return as-is
        if isinstance(value, Environment):
            return value

        # 2) Otherwise expect a string like "development" or "PROD"
        if isinstance(value, str):
            try:
                return Environment[value.upper()]
            except KeyError:
                raise ValueError(f"Invalid env: {value}")

        # 3) Reject any other types
        raise ValueError(f"Environment must be a str or Environment enum, got {value!r}")


class LndPermissions(BaseSettings):
    """
    methods have overlap in permissions, but best to maintain an explicit list
    of URIs for clarity
    """
    methods: List[str] = [
        'uri:/chainrpc.ChainKit/GetBestBlock',
        'uri:/invoicesrpc.Invoices/AddHoldInvoice',
        'uri:/invoicesrpc.Invoices/CancelInvoice',
        'uri:/invoicesrpc.Invoices/SettleInvoice',
        'uri:/invoicesrpc.Invoices/SubscribeSingleInvoice',
        'uri:/lnrpc.Lightning/CheckMacaroonPermissions',
        'uri:/lnrpc.Lightning/ConnectPeer',
        'uri:/lnrpc.Lightning/GetInfo',
        'uri:/lnrpc.Lightning/GetNodeInfo',
        'uri:/lnrpc.Lightning/ListPermissions',
        'uri:/lnrpc.Lightning/OpenChannel',
        'uri:/lnrpc.Lightning/SignMessage',
        'uri:/walletrpc.WalletKit/EstimateFee',
        'uri:/walletrpc.WalletKit/ListUnspent',
        'uri:/walletrpc.WalletKit/RequiredReserve',
    ]


class LnBackendSettings(BaseSettings):
    node: Optional[LnImplementation] = Field(default=None)
    rest_host: Optional[HttpUrl] = Field(default=None)
    permissions_file_path: Optional[FilePath] = Field(default=None)
    cert_file_path: Optional[FilePath] = Field(default=None)
    health_check_time: Optional[int] = Field(default=300)  # in seconds

    @field_validator('health_check_time', mode='after')
    def validate_health_check_time(cls, v: Optional[int]) -> Optional[int]:
        min_time_check = 30
        if not v:
            return v
        if v < min_time_check:
            raise ValueError(f'you should set a value for HEALTH_CHECK_TIME greater than {min_time_check} seconds, got {v}')
        return v

    @field_validator("node", mode="after")
    def validate_supported_impl(cls, v: Optional[LnImplementation]) -> Optional[LnImplementation]:
        if not v:
            return v
        if v not in LnImplementation.supported():
            raise ValueError(f'{v.name} not yet supported')
        return v

    @field_validator("permissions_file_path", "cert_file_path", mode="before")
    def _expand_user_path(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            return os.path.expanduser(v)
        return v

    @field_validator("rest_host", mode="after")
    def check_rest_host(cls, v: Optional[HttpUrl]) -> Optional[HttpUrl]:
        if v is None:
            return None

        host, port = v.host, v.port
        try:
            socket.create_connection((host, port), timeout=5).close()
        except OSError as e:
            raise ValueError(f"could not connect to {host}:{port}: {e.strerror or e}")
        return v

    @field_serializer("rest_host", mode="plain")
    def _ser_rest_host(self, v: Optional[HttpUrl], info) -> Optional[str]:
        return None if v is None else v.unicode_string()

    @field_serializer("permissions_file_path", 'cert_file_path', mode="plain")
    def _ser_path(self, v: Optional[Path], info) -> Optional[str]:
        return None if v is None else v.as_posix()


class AdSettings(PublspSettings):
    status: AdStatus = Field(default=AdStatus.ACTIVE)
    min_required_channel_confirmations: int = Field(default=0)
    min_funding_confirms_within_blocks: int = Field(default=2)
    supports_zero_channel_reserve: bool = Field(default=False)
    supports_private_channels: bool = Field(default=True)
    max_channel_expiry_blocks: int = Field(default=12960)
    min_initial_client_balance_sat: int = Field(default=0)
    max_initial_client_balance_sat: int = Field(default=0)
    min_initial_lsp_balance_sat: int = Field(default=0)
    max_initial_lsp_balance_sat: int = Field(default=10000000)
    min_channel_balance_sat: int = Field(default=1000000)
    max_channel_balance_sat: int = Field(default=10000000)
    fixed_cost_sats: int = Field(default=75000)
    variable_cost_ppm: int = Field(default=10000)
    max_promised_fee_rate: int = Field(default=2500)
    max_promised_base_fee: int = Field(default=1)

    @field_validator('min_funding_confirms_within_blocks')
    def validate_greater_than_one(v: Optional[int]) -> Optional[int]:
        if v > 1:
            return v
        else:
            raise ValueError(
                'min_funding_confirms_within_blocks must be 2 (the fastest) '
                'or greater, according to the API')

    @model_validator(mode='after')
    def validate_channel_balance_limits(self):
        if self.min_channel_balance_sat > self.max_channel_balance_sat:
            raise ValueError(f'min channel balance has to be smaller than max')
        return self

    @model_validator(mode='after')
    def validate_client_balance_limits(self):
        if self.min_initial_client_balance_sat > self.max_initial_client_balance_sat:
            raise ValueError(f'min initial client balance has to be smaller than max')
        return self

    @model_validator(mode='after')
    def validate_lsp_balance_limits(self):
        if self.min_initial_lsp_balance_sat > self.max_initial_lsp_balance_sat:
            raise ValueError(f'min initial client balance has to be smaller than max')
        return self

    @field_validator(
        'min_funding_confirms_within_blocks',
        'max_channel_expiry_blocks',
        'max_initial_client_balance_sat',
        'max_initial_lsp_balance_sat',
        'min_channel_balance_sat',
        'max_channel_balance_sat')
    def validate_greater_than_zero(v: Optional[int]) -> Optional[int]:
        if v > 0:
            return v
        else:
            raise ValueError(f'{v} must be greater than 0')

    @field_validator(
        'min_required_channel_confirmations',
        'min_initial_client_balance_sat',
        'min_initial_lsp_balance_sat',
        'fixed_cost_sats',
        'variable_cost_ppm',
        'max_promised_fee_rate',
        'max_promised_base_fee'
    )
    def validate_greater_equal_to_zero(v: Optional[int]) -> Optional[int]:
        if v >= 0:
            return v
        else:
            raise ValueError(f'{v} must be greater than or equal to 0')


class CustomAdSettings(PublspSettings):
    value_prop: Optional[str] = Field(default="No frills liquidity offer over Nostr using publsp!")
    sum_utxos_as_max_capacity: Optional[bool] = Field(default=False)
    channel_max_bucket: Optional[int] = Field(default=5000000)
    dynamic_fixed_cost: Optional[bool] = Field(default=False)
    dynamic_fixed_cost_conf_target: Optional[int] = Field(default=2)
    dynamic_fixed_cost_vb_multiplier: Optional[int] = Field(default=15000)

    @field_validator('dynamic_fixed_cost_conf_target', mode='after')
    def validate_min_dynamic_fixed_cost_conf_target(v: Optional[int]) -> Optional[int]:
        if not v:
            return
        if v < 2:
            raise ValueError('dynamic_fixed_cost_conf_target must be greater than or equal to 2')
        return v


class OrderSettings(PublspSettings):
    ad_id: Optional[Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            pattern=AD_ID_REGEX,
        ),
    ]] = Field(default=None)
    target_pubkey_uri: Optional[str] = Field(default=None)
    lsp_balance_sat: int = Field(default=5000000)
    client_balance_sat: int = Field(default=0)
    required_channel_confirmations: int = Field(default=0)
    funding_confirms_within_blocks: int = Field(default=6)
    channel_expiry_blocks: int = Field(default=4320)
    token: Optional[str] = Field(default='')
    refund_onchain_address: Optional[str] = Field(default='')
    announce_channel: bool = Field(default=True)

    @field_validator('lsp_balance_sat', 'channel_expiry_blocks')
    def validate_greater_than_zero(cls, v: Optional[int]) -> Optional[int]:
        if v > 0:
            return v
        else:
            raise ValueError(f'{v} must be greater than 0')

    @field_validator(
        'client_balance_sat',
        'required_channel_confirmations',
        'funding_confirms_within_blocks')
    def validate_greater_equal_to_zero(cls, v: Optional[int]) -> Optional[int]:
        if v >= 0:
            return v
        else:
            raise ValueError(f'{v} must be greater than or equal to 0')

    @field_validator('target_pubkey_uri', mode='before')
    def validate_pubkey_uri(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v

        try:
            pubkey, hostport = v.split("@", 1)
        except ValueError:
            raise ValueError("must be in form <66-hex-pubkey>@<host>:<port>")

        # 1) pubkey
        if not PUBKEY_RE.fullmatch(pubkey):
            raise ValueError("pubkey must be exactly 66 hex characters")

        # 2) split host / port by last colon (so IPv6 works)
        idx = hostport.rfind(":")
        if idx == -1:
            raise ValueError("missing port (expected host:port)")

        host = hostport[:idx]
        port_str = hostport[idx + 1:]

        # 3) port
        if not port_str.isdigit():
            raise ValueError("port must be an integer")
        port = int(port_str)
        if not (1 <= port <= 65_535):
            raise ValueError("port must be 1–65535")

        # 4) host: IPv4 / IPv6 / .onion
        if ONION_RE.fullmatch(host):
            return v

        try:
            # will succeed for both IPv4 and IPv6
            ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(
                "host must be a valid IPv4, IPv6, or 16/56-char .onion address"
            )

        return v


class NostrSettings(PublspSettings):
    nostr_keys_path: Optional[str] = Field(default='output/nostr-keys.json')
    nostr_keys_path_dev: Optional[str] = Field(default='output/nostr-keys.json.dev')
    reuse_keys: Optional[bool] = Field(default=False)
    write_keys: Optional[bool] = Field(default=False)
    encrypt_keys: Optional[bool] = Field(default=False)
    nostr_relays: List[str] = Field(
        default=[
            'wss://relay.damus.io',
            'wss://nostr.mom',
            'wss://nostr.bitcoiner.social',
        ]
    )
    dev_relays: List[str] = Field(
        default=[
            'ws://localhost:10547',
        ]
    )

    @model_validator(mode='after')
    def ensure_output_directory_exists(self):
        """Ensure the output directory exists for nostr keys files"""
        paths_to_check = [self.nostr_keys_path, self.nostr_keys_path_dev]

        for path in paths_to_check:
            if path:
                directory = Path(path).parent
                directory.mkdir(parents=True, exist_ok=True)

        return self


class ApiSettings(PublspSettings):
    interval_minutes: int = 10
    max_idle_minutes: int = 120
    max_listen_minutes: int = 100


class LspSettings(
        EnvironmentSettings,
        LnBackendSettings,
        AdSettings,
        CustomAdSettings,
        NostrSettings,
        PublspSettings,
        ):
    version: str = Field(default=VERSION)
    daemon: bool = Field(default=False)
    lease_history_file_path: str = Field(default='output/lease-history.json')
    include_node_sig: bool = Field(default=False)

    @model_validator(mode='after')
    def ensure_output_directory_exists(self):
        """Ensure the output directory exists for nostr keys files"""
        if self.lease_history_file_path:
            directory = Path(self.lease_history_file_path).parent
            directory.mkdir(parents=True, exist_ok=True)

        return self

class CustomerSettings(
        EnvironmentSettings,
        OrderSettings,
        NostrSettings,
        PublspSettings,
        ):
    version: str = Field(default=VERSION)


class Settings(
        EnvironmentSettings,
        LnBackendSettings,
        AdSettings,
        CustomAdSettings,
        OrderSettings,
        NostrSettings,
        PublspSettings,
        ):
    version: str = Field(default=VERSION)

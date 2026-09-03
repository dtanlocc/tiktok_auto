from __future__ import annotations

from dataclasses import dataclass

from invisible_browser_studio.adapters.outbound.brokers import (
    InMemoryEventBroker,
    LatestFrameBroker,
)
from invisible_browser_studio.adapters.outbound.in_memory_batch_repository import (
    InMemoryBatchRepository,
)
from invisible_browser_studio.adapters.outbound.in_memory_repository import (
    InMemorySessionRepository,
)
from invisible_browser_studio.adapters.outbound.invisible_runtime import (
    InvisiblePlaywrightRuntime,
)
from invisible_browser_studio.adapters.outbound.microsoft_graph_otp import (
    MicrosoftGraphOtpReader,
    SimulatedOtpReader,
)
from invisible_browser_studio.adapters.outbound.proxy_rotation import HttpProxyRotator
from invisible_browser_studio.adapters.outbound.simulated_runtime import (
    SimulatedBrowserRuntime,
)
from invisible_browser_studio.adapters.outbound.sqlite_batch_repository import (
    SqliteBatchRepository,
)
from invisible_browser_studio.adapters.outbound.tiktok_signup_driver import (
    SimulatedSignupDriver,
    TikTokSignupDriver,
)
from invisible_browser_studio.application.batch_services import AutomationBatchService
from invisible_browser_studio.application.scheduler import FairPriorityScheduler
from invisible_browser_studio.application.services import BrowserSessionService
from invisible_browser_studio.application.signup_services import SignupTestService

from invisible_browser_studio.adapters.outbound.credential_cipher import (
    FernetCredentialCipher,
)
from invisible_browser_studio.adapters.outbound.sqlite_account_repository import (
    SqliteAccountRepository,
)
from invisible_browser_studio.application.account_services import AccountService
from invisible_browser_studio.application.ports import (
    AccountRepository,
    BatchRepository,
    BrowserRuntime,
    OtpReader,
    SignupAutomationDriver,
)

from .config import Settings


@dataclass(slots=True)
class Container:
    settings: Settings
    repository: InMemorySessionRepository
    batch_repository: BatchRepository
    account_repository: AccountRepository
    events: InMemoryEventBroker
    frames: LatestFrameBroker
    scheduler: FairPriorityScheduler
    runtime: BrowserRuntime
    sessions: BrowserSessionService
    batches: AutomationBatchService
    accounts: AccountService
    signup_tests: SignupTestService

    @classmethod
    def build(cls, settings: Settings) -> Container:
        repository = InMemorySessionRepository()
        batch_repository: BatchRepository = (
            SqliteBatchRepository(settings.database_path)
            if settings.database_path is not None
            else InMemoryBatchRepository()
        )
        account_database_path = (
            settings.database_path
            if settings.database_path is not None
            else settings.upload_root.parent / "control-plane.sqlite3"
        )
        credentials_key_path = (
            settings.credentials_key_path
            if settings.credentials_key_path is not None
            else account_database_path.parent / "credentials.key"
        )
        credential_cipher = FernetCredentialCipher(
            key_path=credentials_key_path,
            configured_key=settings.credentials_key,
        )
        account_repository: AccountRepository = SqliteAccountRepository(
            account_database_path,
            credential_cipher,
        )
        events = InMemoryEventBroker()
        frames = LatestFrameBroker()
        scheduler = FairPriorityScheduler(
            workers=settings.scheduler_workers,
            max_queued=max(settings.max_queued_jobs, settings.scheduler_workers),
        )
        runtime: BrowserRuntime
        signup_driver: SignupAutomationDriver
        otp_reader: OtpReader
        if settings.runtime == "invisible":
            invisible_runtime = InvisiblePlaywrightRuntime(
                frames,
                screenshot_interval_seconds=settings.stream_interval_seconds,
                jpeg_quality=settings.stream_jpeg_quality,
                stream_max_width=settings.stream_max_width,
                extension_paths=settings.extension_paths,
                extensions_required=settings.extensions_required,
                omocaptcha_api_key=settings.omocaptcha_api_key,
                omocaptcha_extension_uuid=settings.omocaptcha_extension_uuid,
            )
            runtime = invisible_runtime
            signup_driver = TikTokSignupDriver(invisible_runtime)
            otp_reader = MicrosoftGraphOtpReader()
        else:
            runtime = SimulatedBrowserRuntime(
                frames, frame_interval_seconds=settings.stream_interval_seconds
            )
            signup_driver = SimulatedSignupDriver()
            otp_reader = SimulatedOtpReader()
        sessions = BrowserSessionService(
            repository=repository,
            runtime=runtime,
            scheduler=scheduler,
            events=events,
            max_sessions=settings.max_sessions,
        )
        rotator = HttpProxyRotator(
            timeout_seconds=settings.rotation_timeout_seconds,
            max_attempts=settings.rotation_max_attempts,
            settle_seconds=settings.rotation_settle_seconds,
        )
        batches = AutomationBatchService(
            repository=batch_repository,
            sessions=sessions,
            rotator=rotator,
            events=events,
            max_jobs=settings.batch_max_jobs,
            max_concurrency=settings.batch_max_concurrency,
        )
        accounts = AccountService(account_repository)
        signup_tests = SignupTestService(
            sessions=sessions,
            driver=signup_driver,
            otp_reader=otp_reader,
            events=events,
        )
        return cls(
            settings,
            repository,
            batch_repository,
            account_repository,
            events,
            frames,
            scheduler,
            runtime,
            sessions,
            batches,
            accounts,
            signup_tests,
        )

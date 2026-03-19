"""Windows Service support for Print Agent."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.config import AgentConfig

SERVICE_STOP_TIMEOUT_SECONDS = 30


def _load_agent_config(config_path: str) -> AgentConfig:
    from src.config import AgentConfig

    return AgentConfig.from_file(config_path)


def _setup_agent_logger(config: AgentConfig):
    from src.utils import setup_logging

    return setup_logging(
        "agent",
        config.LOG_DIR,
        instance_id=config.INSTANCE_ID,
        force_event_log=True,
        event_source=config.SERVICE_NAME,
        posthog_enabled=config.POSTHOG_ENABLED,
        posthog_config={
            "api_key": config.POSTHOG_PROJECT_API_KEY,
            "host": config.POSTHOG_HOST,
            "organisation_id": config.ORGANISATION_ID,
        } if config.POSTHOG_ENABLED else None,
    )


def _resolve_python_executable() -> str:
    python_executable = sys.executable
    if Path(python_executable).name.lower() == "pythonservice.exe":
        candidate = Path(python_executable).with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return python_executable

PYWIN32_IMPORT_ERROR: Optional[Exception] = None
try:
    import pywintypes  # type: ignore
    import servicemanager  # type: ignore
    import win32event  # type: ignore
    import win32service  # type: ignore
    import win32serviceutil  # type: ignore

    PYWIN32_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - platform-dependent
    PYWIN32_AVAILABLE = False
    PYWIN32_IMPORT_ERROR = exc

def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows Service commands are only available on Windows.")


def _require_pywin32() -> None:
    if not PYWIN32_AVAILABLE:
        raise RuntimeError(
            "pywin32 is required for Windows Service support. "
            "Install dependencies and try again."
        ) from PYWIN32_IMPORT_ERROR


if PYWIN32_AVAILABLE:

    class PrintAgentWindowsService(win32serviceutil.ServiceFramework):
        """Windows SCM host for Print Agent."""

        _svc_name_ = "PrintAgentSync"
        _svc_display_name_ = "PrintAgentSync"
        _svc_description_ = "Print Agent Sync Service"

        @classmethod
        def configure_service_metadata(cls, service_name: str) -> None:
            cls._svc_name_ = service_name
            cls._svc_display_name_ = service_name
            cls._svc_description_ = f"{service_name} - Print Agent Sync Service"

        def __init__(self, args):
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.logger = None
            self.child_process: Optional[subprocess.Popen] = None

        def _get_config_path(self) -> str:
            return win32serviceutil.GetServiceCustomOption(
                self._svc_name_,
                "ConfigPath",
                "config.json",
            )

        def _get_project_root(self) -> Path:
            return Path(__file__).resolve().parent

        def _build_child_env(self) -> dict:
            project_root = self._get_project_root()
            src_dir = project_root / "src"
            env = os.environ.copy()
            python_path_entries = [str(project_root), str(src_dir)]
            existing_python_path = env.get("PYTHONPATH")
            if existing_python_path:
                python_path_entries.append(existing_python_path)
            env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
            return env

        def _build_child_command(self) -> list[str]:
            return [
                _resolve_python_executable(),
                str(self._get_project_root() / "main.py"),
                "service",
                "--config",
                self._get_config_path(),
            ]

        def _start_child_process(self) -> subprocess.Popen:
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
                else 0
            )
            return subprocess.Popen(
                self._build_child_command(),
                cwd=str(self._get_project_root()),
                env=self._build_child_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
            )

        def _stop_child_process(self) -> None:
            if self.child_process is None or self.child_process.poll() is not None:
                return

            try:
                self.child_process.send_signal(signal.CTRL_BREAK_EVENT)
                self.child_process.wait(timeout=SERVICE_STOP_TIMEOUT_SECONDS)
                return
            except Exception:
                pass

            try:
                self.child_process.terminate()
                self.child_process.wait(timeout=10)
            except Exception:
                self.child_process.kill()
                self.child_process.wait(timeout=10)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self) -> None:
            config = _load_agent_config(self._get_config_path())
            self.logger = _setup_agent_logger(config)
            servicemanager.LogInfoMsg(f"{config.SERVICE_NAME} starting")

            try:
                self.child_process = self._start_child_process()
                self.ReportServiceStatus(win32service.SERVICE_RUNNING)

                while True:
                    if self.child_process.poll() is not None:
                        break

                    wait_result = win32event.WaitForSingleObject(self.stop_event, 1000)
                    if wait_result == win32event.WAIT_OBJECT_0:
                        self._stop_child_process()
                        break
            finally:
                servicemanager.LogInfoMsg(f"{config.SERVICE_NAME} stopped")

else:

    class PrintAgentWindowsService:  # pragma: no cover - platform-dependent
        """Fallback placeholder when pywin32 is unavailable."""

        @classmethod
        def configure_service_metadata(cls, service_name: str) -> None:
            _ = service_name


def install_service(config: AgentConfig, config_path: str) -> str:
    """Install the agent as a Windows Service."""
    _require_windows()
    _require_pywin32()

    service_name = config.SERVICE_NAME
    module_dir = os.path.dirname(os.path.abspath(__file__))
    service_class_path = (
        os.path.splitext(os.path.abspath(__file__))[0]
        + "."
        + PrintAgentWindowsService.__name__
    )
    abs_config_path = os.path.abspath(config_path)

    PrintAgentWindowsService.configure_service_metadata(service_name)
    win32serviceutil.InstallService(
        service_class_path,
        service_name,
        service_name,
        description=f"{service_name} - Print Agent Sync Service",
        startType=win32service.SERVICE_DEMAND_START,
    )
    win32serviceutil.SetServiceCustomOption(
        service_name,
        "ConfigPath",
        abs_config_path,
    )
    win32serviceutil.SetServiceCustomOption(
        service_name,
        "PythonPath",
        module_dir,
    )
    return service_name


def start_service(service_name: str) -> None:
    """Start an installed Windows Service."""
    _require_windows()
    _require_pywin32()
    try:
        win32serviceutil.StartService(service_name)
    except pywintypes.error as e:
        if len(e.args) > 0 and e.args[0] == 1060:
            raise RuntimeError(
                f"Service '{service_name}' is not installed. "
                "Run 'python main.py windows-install --config <path>' first."
            ) from e
        raise RuntimeError(f"Failed to start Windows service '{service_name}': {e}") from e


def stop_service(service_name: str) -> None:
    """Stop a running Windows Service."""
    _require_windows()
    _require_pywin32()
    try:
        win32serviceutil.StopService(service_name)
    except pywintypes.error as e:
        if len(e.args) > 0 and e.args[0] == 1060:
            raise RuntimeError(
                f"Service '{service_name}' is not installed."
            ) from e
        raise RuntimeError(f"Failed to stop Windows service '{service_name}': {e}") from e


def remove_service(service_name: str) -> None:
    """Remove an installed Windows Service."""
    _require_windows()
    _require_pywin32()
    try:
        win32serviceutil.RemoveService(service_name)
    except pywintypes.error as e:
        if len(e.args) > 0 and e.args[0] == 1060:
            raise RuntimeError(
                f"Service '{service_name}' is not installed."
            ) from e
        raise RuntimeError(f"Failed to remove Windows service '{service_name}': {e}") from e


def run_service_host(config: AgentConfig) -> None:
    """Run the sync loop with service-mode logging policy."""
    _require_windows()
    _require_pywin32()
    from src.sync_service import SyncService

    logger = _setup_agent_logger(config)
    SyncService(config, logger).run()

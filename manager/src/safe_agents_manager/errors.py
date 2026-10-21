"""Error types. Each error carries a stable ``code`` that the API maps to an HTTP status."""

from __future__ import annotations


class ManagerError(Exception):
    code = "manager_error"
    http_status = 500


class ArchiveError(ManagerError):
    code = "invalid_archive"
    http_status = 422


class ManifestError(ManagerError):
    code = "invalid_manifest"
    http_status = 422


class InstallError(ManagerError):
    code = "install_failed"
    http_status = 500


class AgentNotFoundError(ManagerError):
    code = "agent_not_found"
    http_status = 404


class AgentAlreadyInstalledError(ManagerError):
    code = "agent_already_installed"
    http_status = 409


class AgentRunningError(ManagerError):
    code = "agent_running"
    http_status = 409


class MissingSecretError(ManagerError):
    code = "missing_secret"
    http_status = 412


class AgentStartError(ManagerError):
    code = "agent_start_failed"
    http_status = 502


class NoFreePortError(ManagerError):
    code = "no_free_port"
    http_status = 503


class RegistryError(ManagerError):
    code = "invalid_registry"
    http_status = 502


class RegistryUnavailableError(ManagerError):
    code = "registry_unavailable"
    http_status = 503


class DownloadError(ManagerError):
    code = "download_failed"
    http_status = 502


class UnsafeUrlError(ManagerError):
    code = "unsafe_url"
    http_status = 422


class SecretStoreError(ManagerError):
    code = "secret_store_unavailable"
    http_status = 503


class InvalidApiKeyError(ManagerError):
    code = "invalid_api_key"
    http_status = 422


class UpstreamError(ManagerError):
    code = "upstream_error"
    http_status = 502


class InvalidRequestError(ManagerError):
    code = "invalid_request"
    http_status = 422

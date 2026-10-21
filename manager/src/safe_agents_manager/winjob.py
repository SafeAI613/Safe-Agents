"""Windows: put agent servers in a Job Object that is killed when the manager exits.

If the manager crashes or is killed, Windows closes its handle to the job and terminates
every process in it, so no agent server is left running in the background. On other
platforms this is a no-op; there the manager stops agents when its stdin closes.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

log = logging.getLogger(__name__)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

_job_handle: int | None = None
_job_lock = threading.Lock()

if sys.platform == "win32":

    def _job() -> int:
        global _job_handle
        with _job_lock:
            if _job_handle is None:
                handle = _kernel32.CreateJobObjectW(None, None)
                if not handle:
                    raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
                info = _ExtendedLimits()
                info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not _kernel32.SetInformationJobObject(
                    handle,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                ):
                    raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
                # Kept open for the manager's lifetime on purpose: closing it kills the agents.
                _job_handle = int(handle)
            return _job_handle

    def assign_to_kill_on_close_job(process: subprocess.Popen[bytes]) -> None:
        try:
            handle = int(process._handle)  # type: ignore[attr-defined]
            if not _kernel32.AssignProcessToJobObject(_job(), handle):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        except OSError as exc:
            # Not fatal: graceful shutdown still stops the agent; only crash cleanup is lost.
            log.warning("could not attach agent process to the job object: %s", exc)

else:

    def assign_to_kill_on_close_job(process: subprocess.Popen[bytes]) -> None:
        return None

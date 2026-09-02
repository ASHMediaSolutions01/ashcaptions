"""Tie ffmpeg's lifetime to ours with a Windows Job Object.

``JobWorker.stop()`` asks a running job to stop and the engine kills its
ffmpeg child in a ``finally`` -- that covers Quit. It does not cover a hard
crash, ``taskkill /F``, or a machine that logs off: Python dies, its
``finally`` never runs, and an ffmpeg encoding an hour-long file keeps
going for another hour, pinning the CPU and writing into an output folder
the next launch will overwrite.

A Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` fixes that at the
OS level: this process joins a job it created at startup, every child it
spawns inherits membership, and when the last handle to the job closes --
which happens when this process dies, however it dies -- Windows
terminates every process still in it. ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` is
set alongside so the one child that must outlive us, the update helper,
can opt out with ``CREATE_BREAKAWAY_FROM_JOB``.

Everything here is best-effort: any failure is logged and the app starts
anyway. Being in a job is a safety net, never a precondition.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("ash_captions.app.jobobject")

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
# subprocess creationflags value for a child that must outlive this process.
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_ERROR_ACCESS_DENIED = 5

# Handle to the job this process created and joined. Never closed on
# purpose: closing the last handle is exactly what kills the members.
_job_handle: int | None = None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    k32.SetInformationJobObject.restype = ctypes.c_int
    k32.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
    k32.AssignProcessToJobObject.restype = ctypes.c_int
    k32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.IsProcessInJob.restype = ctypes.c_int
    k32.IsProcessInJob.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    return k32


def in_job() -> bool | None:
    """Whether this process is already inside some job (``None`` if the
    question can't be asked -- not Windows, or the call failed)."""
    if sys.platform != "win32":
        return None
    try:
        k32 = _kernel32()
        result = ctypes.c_int(0)
        if not k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(result)):
            return None
        return bool(result.value)
    except Exception:  # noqa: BLE001 - diagnostic only
        return None


def assign_current_process() -> bool:
    """Create a kill-on-close job and put this process in it. Returns True
    on success; False (with a log line) on any failure. Idempotent."""
    global _job_handle
    if sys.platform != "win32":
        return False
    if _job_handle is not None:
        return True
    try:
        k32 = _kernel32()
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        if not k32.SetInformationJobObject(
            handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
        ):
            error = ctypes.get_last_error()
            k32.CloseHandle(handle)
            raise ctypes.WinError(error)
        if not k32.AssignProcessToJobObject(handle, k32.GetCurrentProcess()):
            error = ctypes.get_last_error()
            k32.CloseHandle(handle)
            if error == _ERROR_ACCESS_DENIED:
                # Already in a job whose limits forbid nesting (pre-Win8
                # semantics, or a restrictive launcher). Not fatal.
                log.warning(
                    "Not joining a kill-on-close job: this process is already in a job "
                    "that does not allow nesting. ffmpeg will not be cleaned up after a hard crash."
                )
                return False
            raise ctypes.WinError(error)
        _job_handle = handle
        log.info("Joined a kill-on-close job object; child processes die with this one.")
        return True
    except Exception:  # noqa: BLE001 - a safety net that fails must never stop the app
        log.exception("Could not set up the kill-on-close job object; continuing without it")
        return False


def is_assigned() -> bool:
    return _job_handle is not None

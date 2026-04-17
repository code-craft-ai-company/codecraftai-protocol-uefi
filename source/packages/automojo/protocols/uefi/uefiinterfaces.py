"""
    .. module:: uefiinterfaces
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: :class:`typing.Protocol` based structural-typing contracts
               that shape the rest of the ``automojo-uefi`` stack.  Each
               protocol is decorated with :func:`typing.runtime_checkable`
               so concrete classes may opt in structurally and tests may
               assert compliance via :func:`isinstance`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import uuid

from typing import Iterator, Optional, Protocol, Tuple, runtime_checkable

from automojo.protocols.uefi.uefiaspects import UefiCmdAspects


# ---------------------------------------------------------------------------
# IUefiTransport
# ---------------------------------------------------------------------------

@runtime_checkable
class IUefiTransport(Protocol):
    """
        Lifecycle and byte-level I/O contract for a UEFI transport.

        Implementations MUST treat ``recv(timeout=0)`` as a non-blocking
        poll: return whatever bytes are already buffered and raise
        :class:`TimeoutError` if none are available.  A concrete class
        that provides the properties and methods listed below satisfies
        this protocol structurally — no inheritance is required.
    """

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` when the transport has an open, usable
                      session to the target.
        """
        ...

    def connect(self, aspects: UefiCmdAspects) -> None:
        """
            Establish the underlying connection to the target.

            :param aspects: The measured-interop behavior package that
                            carries connection timeouts, retry
                            intervals, and allowed-exception sets.

            :raises: UefiTransportError
        """
        ...

    def disconnect(self) -> None:
        """
            Tear down the connection.  Shall not raise on a
            double-disconnect.
        """
        ...

    def send(self, data: bytes) -> int:
        """
            Send ``data`` toward the target.

            :param data: The bytes to send.

            :returns: The number of bytes actually sent.
            :raises: UefiTransportError
        """
        ...

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Receive up to ``max_bytes`` from the target.

            :param max_bytes: Upper bound on the number of returned
                              bytes.
            :param timeout: Seconds to wait; ``0`` means non-blocking
                            poll.

            :returns: The bytes read; may be shorter than ``max_bytes``.
            :raises: TimeoutError, UefiTransportError
        """
        ...


# ---------------------------------------------------------------------------
# IUefiShellRunner
# ---------------------------------------------------------------------------

@runtime_checkable
class IUefiShellRunner(Protocol):
    """
        Protocol for objects that execute UEFI Shell commands.

        A concrete class implementing :meth:`run_cmd` satisfies this
        protocol structurally.  Mock runners used in tests and real
        runners used against hardware are interchangeable at call
        sites that accept :class:`IUefiShellRunner`.
    """

    def run_cmd(self, command: str, aspects: UefiCmdAspects) -> Tuple[int, str, str]:
        """
            Execute ``command`` against the UEFI Shell and return the
            status, stdout, and stderr as a three-tuple.

            :param command: The shell command string.
            :param aspects: The measured-interop behavior package.  A
                            ``None`` sentinel is NOT accepted at this
                            layer; higher layers may merge an instance
                            default before calling.

            :returns: A tuple ``(status, stdout, stderr)`` where
                      ``status`` is the integer exit code, ``stdout``
                      is the captured standard-output text, and
                      ``stderr`` is the captured standard-error text.
            :raises: UefiShellError, UefiTransportError
        """
        ...


# ---------------------------------------------------------------------------
# IUefiVariableProvider
# ---------------------------------------------------------------------------

@runtime_checkable
class IUefiVariableProvider(Protocol):
    """
        Protocol for objects that read, write, and enumerate UEFI
        variables.  Backend implementations for the UEFI Shell,
        Redfish, and SSH-to-host transports are interchangeable at
        call sites that accept :class:`IUefiVariableProvider`.
    """

    def get_variable(self, name: str, vendor_guid: uuid.UUID) -> Tuple[int, bytes]:
        """
            Read a single UEFI variable.

            :param name: UEFI variable name.
            :param vendor_guid: Vendor GUID namespace of the variable.

            :returns: A tuple ``(attributes, data)`` where
                      ``attributes`` is the UEFI attribute-flag
                      bitfield and ``data`` is the raw variable
                      payload.
            :raises: UefiVariableError
        """
        ...

    def set_variable(self, name: str, vendor_guid: uuid.UUID, attributes: int, data: bytes) -> None:
        """
            Write a UEFI variable.

            :param name: UEFI variable name.
            :param vendor_guid: Vendor GUID namespace of the variable.
            :param attributes: UEFI attribute-flag bitfield.
            :param data: The raw variable payload to write.

            :raises: UefiVariableError
        """
        ...

    def enumerate_variables(self) -> Iterator[Tuple[str, uuid.UUID]]:
        """
            Stream the set of variable ``(name, vendor_guid)`` pairs
            lazily; implementations SHALL NOT build the full list
            eagerly.

            :returns: An iterator of ``(name, vendor_guid)`` tuples.
            :raises: UefiVariableError
        """
        ...


# ---------------------------------------------------------------------------
# IUefiFileProvider
# ---------------------------------------------------------------------------

@runtime_checkable
class IUefiFileProvider(Protocol):
    """
        Protocol for ESP (EFI System Partition) file operations.

        Paths follow the UEFI convention (``FS0:\\path``) in UEFI
        Shell transports and the POSIX convention
        (``/boot/efi/...``) in SSH-to-host transports.  Concrete
        classes choose their path convention based on the
        underlying transport.
    """

    def file_push(self, local: str, remote: str) -> None:
        """
            Upload ``local`` to ``remote`` on the target.

            :param local: Path on the test host.
            :param remote: Path on the target.

            :raises: UefiShellError, UefiTransportError
        """
        ...

    def file_pull(self, remote: str, local: str) -> None:
        """
            Download ``remote`` from the target to ``local``.

            :param remote: Path on the target.
            :param local: Path on the test host.

            :raises: UefiShellError, UefiTransportError
        """
        ...

    def file_exists(self, path: str) -> bool:
        """
            Test whether ``path`` exists on the target and is a file.

            :param path: Path on the target.

            :returns: ``True`` when ``path`` exists and is a file.
            :raises: UefiShellError, UefiTransportError
        """
        ...

    def directory_exists(self, path: str) -> bool:
        """
            Test whether ``path`` exists on the target and is a
            directory.

            :param path: Path on the target.

            :returns: ``True`` when ``path`` exists and is a
                      directory.
            :raises: UefiShellError, UefiTransportError
        """
        ...


# ---------------------------------------------------------------------------
# IUefiSystemContext
# ---------------------------------------------------------------------------

@runtime_checkable
class IUefiSystemContext(Protocol):
    """
        Protocol describing the UEFI-specific system-level metadata
        surface.  Properties may return ``None`` when the underlying
        transport cannot yet report the requested datum; consumers
        SHALL treat ``None`` as "not-yet-known" rather than "not
        applicable".
    """

    @property
    def host(self) -> str:
        """
            :returns: A stable identifier for the target host — for
                      example a DNS name, an IP address, or a BMC
                      URI.  Used as a correlation key in log output
                      and in structured diagnostics.
        """
        ...

    @property
    def firmware_vendor(self) -> Optional[str]:
        """
            :returns: The firmware-vendor string reported by the
                      target, or ``None`` when not yet known.
        """
        ...

    @property
    def firmware_revision(self) -> Optional[int]:
        """
            :returns: The firmware-revision integer (UEFI spec
                      encoding), or ``None`` when not yet known.
        """
        ...

    @property
    def secure_boot_state(self) -> Optional[str]:
        """
            :returns: A short human-readable summary of the Secure
                      Boot state (for example ``"enabled"``,
                      ``"setup"``, ``"audit"``), or ``None`` when
                      not yet known.
        """
        ...

    def verify_connectivity(self) -> bool:
        """
            Probe the target to determine whether the system context
            is reachable.

            :returns: ``True`` when the target responded to the
                      probe; ``False`` otherwise.
            :raises: UefiTransportError
        """
        ...


__all__ = [
    "IUefiFileProvider",
    "IUefiShellRunner",
    "IUefiSystemContext",
    "IUefiTransport",
    "IUefiVariableProvider",
]

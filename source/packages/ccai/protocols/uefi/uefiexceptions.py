"""
    .. module:: uefiexceptions
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: UEFI-specific exception hierarchy.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import uuid

from typing import Any, Dict, List, Optional

from ccai.errors.exceptions import ProtocolError


SHELL_ERROR_OUTPUT_PREVIEW_LINES = 40


def _preview(text: Optional[str]) -> str:
    """
        Trim ``text`` to a bounded number of lines and append an ellipsis line
        when truncation occurs.  Used when rendering captured stdout / stderr
        into an exception's ``__str__``.

        :param text: The full captured stream contents or ``None``.

        :returns: A trimmed string suitable for inclusion in exception text.
    """
    if text is None:
        rtnval = ""
    else:
        lines = text.splitlines()
        line_count = len(lines)
        if line_count <= SHELL_ERROR_OUTPUT_PREVIEW_LINES:
            rtnval = os.linesep.join(lines)
        else:
            kept = lines[:SHELL_ERROR_OUTPUT_PREVIEW_LINES]
            truncated = line_count - SHELL_ERROR_OUTPUT_PREVIEW_LINES
            ellipsis = "... ({} more lines)".format(truncated)
            rtnval = os.linesep.join(kept) + os.linesep + ellipsis

    return rtnval


class UefiError(RuntimeError):
    """
        Root exception for every UEFI-specific error raised by this library.

        :param message: A short human-readable summary of the error.
        :param details: Optional list of context lines rendered after the
                        summary, joined with :data:`os.linesep`.
    """

    def __init__(self, message: str, details: Optional[List[str]] = None) -> None:
        """
            Initialize the base UEFI exception.
        """
        RuntimeError.__init__(self, message)

        if details is None:
            resolved_details: List[str] = []
        elif isinstance(details, list):
            resolved_details = list(details)
        else:
            err_msg = "UefiError 'details' must be a list of str or None; got type={}".format(type(details).__name__)
            raise TypeError(err_msg) from None

        self.details = resolved_details
        return

    def __str__(self) -> str:
        """
            Render the message followed by the details lines.
        """
        if len(self.args) > 0:
            message = self.args[0]
        else:
            message = ""

        if len(self.details) == 0:
            rtnval = message
        else:
            rtnval = message + os.linesep + os.linesep.join(self.details)

        return rtnval

    def as_dict(self) -> Dict[str, Any]:
        """
            Serialize the exception to a JSON-compatible dictionary.

            :returns: Dict with ``type``, ``message``, ``details`` keys.
        """
        if len(self.args) > 0:
            message = self.args[0]
        else:
            message = ""

        rtnval: Dict[str, Any] = {
            "type": type(self).__name__,
            "message": message,
            "details": list(self.details),
        }
        return rtnval


class UefiShellError(UefiError):
    """
        Raised when a UEFI Shell command fails or returns an unexpected
        status code.

        :param message: Short human-readable summary.
        :param details: Optional list of extra context lines.
        :param command: The command string that was issued.
        :param status: The integer exit status returned by the shell.
        :param stdout: Captured standard-output text.
        :param stderr: Captured standard-error text.
    """

    def __init__(self, message: str, details: Optional[List[str]] = None,
                 command: Optional[str] = None,
                 status: Optional[int] = None,
                 stdout: Optional[str] = None,
                 stderr: Optional[str] = None) -> None:
        """
            Initialize the shell error with shell-specific context fields.
        """
        UefiError.__init__(self, message, details=details)

        if status is not None and (not isinstance(status, int) or isinstance(status, bool)):
            err_msg = "UefiShellError 'status' must be int or None; got type={}".format(type(status).__name__)
            raise TypeError(err_msg) from None

        if stdout is not None and not isinstance(stdout, str):
            err_msg = "UefiShellError 'stdout' must be str or None; got type={}".format(type(stdout).__name__)
            raise TypeError(err_msg) from None

        if stderr is not None and not isinstance(stderr, str):
            err_msg = "UefiShellError 'stderr' must be str or None; got type={}".format(type(stderr).__name__)
            raise TypeError(err_msg) from None

        self.command = command
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        return

    def __str__(self) -> str:
        """
            Render the shell-specific context below the base message.
        """
        base = UefiError.__str__(self)
        lines: List[str] = [base]

        if self.command is not None:
            lines.append("COMMAND: {}".format(self.command))
        if self.status is not None:
            lines.append("STATUS: {}".format(self.status))
        if self.stdout is not None:
            lines.append("STDOUT:")
            lines.append(_preview(self.stdout))
        if self.stderr is not None:
            lines.append("STDERR:")
            lines.append(_preview(self.stderr))

        rtnval = os.linesep.join(lines)
        return rtnval

    def as_dict(self) -> Dict[str, Any]:
        """
            Serialize the shell error including its typed context fields.
        """
        rtnval = UefiError.as_dict(self)
        rtnval["command"] = self.command
        rtnval["status"] = self.status
        rtnval["stdout"] = self.stdout
        rtnval["stderr"] = self.stderr
        return rtnval


class UefiVariableError(UefiError):
    """
        Raised when a UEFI variable-services operation fails.

        :param message: Short human-readable summary.
        :param details: Optional list of extra context lines.
        :param name: UEFI variable name.
        :param vendor_guid: Vendor GUID namespace of the variable.
        :param attributes: Attribute flag bitfield.
        :param efi_status: Raw EFI_STATUS numeric value, if known.
    """

    def __init__(self, message: str, details: Optional[List[str]] = None,
                 name: Optional[str] = None,
                 vendor_guid: Optional[uuid.UUID] = None,
                 attributes: Optional[int] = None,
                 efi_status: Optional[int] = None) -> None:
        """
            Initialize the variable error with variable-specific context fields.
        """
        UefiError.__init__(self, message, details=details)

        if vendor_guid is not None and not isinstance(vendor_guid, uuid.UUID):
            err_msg = "UefiVariableError 'vendor_guid' must be uuid.UUID or None; got type={}".format(type(vendor_guid).__name__)
            raise TypeError(err_msg) from None

        if attributes is not None and (not isinstance(attributes, int) or isinstance(attributes, bool)):
            err_msg = "UefiVariableError 'attributes' must be int or None; got type={}".format(type(attributes).__name__)
            raise TypeError(err_msg) from None

        if efi_status is not None and (not isinstance(efi_status, int) or isinstance(efi_status, bool)):
            err_msg = "UefiVariableError 'efi_status' must be int or None; got type={}".format(type(efi_status).__name__)
            raise TypeError(err_msg) from None

        self.name = name
        self.vendor_guid = vendor_guid
        self.attributes = attributes
        self.efi_status = efi_status
        return

    def __str__(self) -> str:
        """
            Render variable context below the base message.
        """
        base = UefiError.__str__(self)
        lines: List[str] = [base]

        if self.name is not None:
            lines.append("NAME: {}".format(self.name))
        if self.vendor_guid is not None:
            lines.append("VENDOR GUID: {}".format(self.vendor_guid))
        if self.attributes is not None:
            lines.append("ATTRIBUTES: 0x{:08X}".format(self.attributes))
        if self.efi_status is not None:
            lines.append("EFI STATUS: 0x{:X}".format(self.efi_status))

        rtnval = os.linesep.join(lines)
        return rtnval

    def as_dict(self) -> Dict[str, Any]:
        """
            Serialize the variable error including its typed context fields.
        """
        rtnval = UefiError.as_dict(self)
        rtnval["name"] = self.name
        if self.vendor_guid is not None:
            rtnval["vendor_guid"] = str(self.vendor_guid)
        else:
            rtnval["vendor_guid"] = None
        rtnval["attributes"] = self.attributes
        rtnval["efi_status"] = self.efi_status
        return rtnval


class UefiCapsuleError(UefiError):
    """
        Raised when a UEFI capsule build, delivery, or report-read fails.
    """


class UefiSecureBootError(UefiError):
    """
        Raised when a Secure Boot state read or key-enrollment write fails.
    """


class UefiTransportError(UefiError):
    """
        Raised when a transport-level fault is observed during connect,
        read, write, or framing.
    """


class UefiProtocolError(UefiError, ProtocolError):
    """
        Raised when the remote side violates the expected protocol framing.

        Multi-inherits the shared :class:`ProtocolError` type so that
        cross-library handlers that catch :class:`ProtocolError` also catch
        UEFI-specific protocol faults.
    """


__all__ = [
    "UefiError",
    "UefiShellError",
    "UefiVariableError",
    "UefiCapsuleError",
    "UefiSecureBootError",
    "UefiTransportError",
    "UefiProtocolError",
    "SHELL_ERROR_OUTPUT_PREVIEW_LINES",
]

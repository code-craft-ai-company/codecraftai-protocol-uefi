"""
    .. module:: uefiguids
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the registry of well-known UEFI GUIDs.
               Every GUID is exposed as a :class:`uuid.UUID` module-level
               constant.  The :data:`WELL_KNOWN_GUIDS` read-only mapping
               enumerates the full set for introspection and drift-detection
               tests.  This module has zero third-party dependencies.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import uuid

from types import MappingProxyType
from typing import Final, Mapping


# ---------------------------------------------------------------------------
# Well-known UEFI GUID constants
#
# Values are drawn from the UEFI Specification and published platform
# reference documents.  Each constant is a :class:`uuid.UUID`; callers that
# need the mixed-endian 16-byte representation should feed the constant to
# :func:`automojo.protocols.uefi.ueficonst.guid_to_bytes`.
# ---------------------------------------------------------------------------

EFI_GLOBAL_VARIABLE_GUID: Final[uuid.UUID] = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
EFI_IMAGE_SECURITY_DATABASE_GUID: Final[uuid.UUID] = uuid.UUID("d719b2cb-3d3a-4596-a3bc-dad00e67656f")
EFI_SYSTEM_RESOURCE_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("b122a263-3661-4f68-9929-78f8b0d62180")
EFI_MEMORY_ATTRIBUTES_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("dcfa911d-26eb-469f-a220-38b7dc461220")
EFI_CAPSULE_REPORT_GUID: Final[uuid.UUID] = uuid.UUID("39b68c46-f7fb-441b-b6ec-16b0f69821f3")
EFI_CONFORMANCE_PROFILE_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("36122546-f7e7-4c8f-bd9b-eb8525b50c0b")
EFI_ACPI_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("8868e871-e4f1-11d3-bc22-0080c73c8881")
SMBIOS_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("eb9d2d31-2d88-11d3-9a16-0090273fc14d")
SMBIOS3_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("f2fd1544-9794-4a2c-992e-e5bbcf20e394")
EFI_RT_PROPERTIES_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("eb66918a-7eef-402a-842e-931d21c38ae9")

EFI_DEVICE_PATH_PROTOCOL_GUID: Final[uuid.UUID] = uuid.UUID("09576e91-6d3f-11d2-8e39-00a0c969723b")
EFI_LOADED_IMAGE_PROTOCOL_GUID: Final[uuid.UUID] = uuid.UUID("5b1b31a1-9562-11d2-8e3f-00a0c969723b")
EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID: Final[uuid.UUID] = uuid.UUID("86c77a67-0b97-4633-a187-49104d0685c7")


# ---------------------------------------------------------------------------
# Aggregate registry (read-only proxy)
# ---------------------------------------------------------------------------

WELL_KNOWN_GUIDS: Final[Mapping[str, uuid.UUID]] = MappingProxyType({
    "EFI_GLOBAL_VARIABLE_GUID": EFI_GLOBAL_VARIABLE_GUID,
    "EFI_IMAGE_SECURITY_DATABASE_GUID": EFI_IMAGE_SECURITY_DATABASE_GUID,
    "EFI_SYSTEM_RESOURCE_TABLE_GUID": EFI_SYSTEM_RESOURCE_TABLE_GUID,
    "EFI_MEMORY_ATTRIBUTES_TABLE_GUID": EFI_MEMORY_ATTRIBUTES_TABLE_GUID,
    "EFI_CAPSULE_REPORT_GUID": EFI_CAPSULE_REPORT_GUID,
    "EFI_CONFORMANCE_PROFILE_TABLE_GUID": EFI_CONFORMANCE_PROFILE_TABLE_GUID,
    "EFI_ACPI_TABLE_GUID": EFI_ACPI_TABLE_GUID,
    "SMBIOS_TABLE_GUID": SMBIOS_TABLE_GUID,
    "SMBIOS3_TABLE_GUID": SMBIOS3_TABLE_GUID,
    "EFI_RT_PROPERTIES_TABLE_GUID": EFI_RT_PROPERTIES_TABLE_GUID,
    "EFI_DEVICE_PATH_PROTOCOL_GUID": EFI_DEVICE_PATH_PROTOCOL_GUID,
    "EFI_LOADED_IMAGE_PROTOCOL_GUID": EFI_LOADED_IMAGE_PROTOCOL_GUID,
    "EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID": EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID,
})


# ---------------------------------------------------------------------------
# __all__ - public surface
# ---------------------------------------------------------------------------

__all__ = [
    "EFI_ACPI_TABLE_GUID",
    "EFI_CAPSULE_REPORT_GUID",
    "EFI_CONFORMANCE_PROFILE_TABLE_GUID",
    "EFI_DEVICE_PATH_PROTOCOL_GUID",
    "EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID",
    "EFI_GLOBAL_VARIABLE_GUID",
    "EFI_IMAGE_SECURITY_DATABASE_GUID",
    "EFI_LOADED_IMAGE_PROTOCOL_GUID",
    "EFI_MEMORY_ATTRIBUTES_TABLE_GUID",
    "EFI_RT_PROPERTIES_TABLE_GUID",
    "EFI_SYSTEM_RESOURCE_TABLE_GUID",
    "SMBIOS3_TABLE_GUID",
    "SMBIOS_TABLE_GUID",
    "WELL_KNOWN_GUIDS",
]

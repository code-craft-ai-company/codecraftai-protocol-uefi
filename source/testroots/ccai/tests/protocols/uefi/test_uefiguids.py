"""
    .. module:: test_uefiguids
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the well-known UEFI GUID registry module.
               Covers per-constant canonical-string identity and a
               drift-detection snapshot of the full registry.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import types
import unittest
import uuid

from ccai.protocols.uefi import uefiguids
from ccai.protocols.uefi.uefiguids import (
    EFI_ACPI_TABLE_GUID,
    EFI_CAPSULE_REPORT_GUID,
    EFI_CONFORMANCE_PROFILE_TABLE_GUID,
    EFI_DEVICE_PATH_PROTOCOL_GUID,
    EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID,
    EFI_GLOBAL_VARIABLE_GUID,
    EFI_IMAGE_SECURITY_DATABASE_GUID,
    EFI_LOADED_IMAGE_PROTOCOL_GUID,
    EFI_MEMORY_ATTRIBUTES_TABLE_GUID,
    EFI_RT_PROPERTIES_TABLE_GUID,
    EFI_SYSTEM_RESOURCE_TABLE_GUID,
    SMBIOS3_TABLE_GUID,
    SMBIOS_TABLE_GUID,
    WELL_KNOWN_GUIDS,
)


# ---------------------------------------------------------------------------
# Frozen expected-value snapshot (drift detector)
#
# Any edit that changes a GUID value must update this table in the same
# commit.  The test :meth:`GuidRegistrySnapshotTestCase.test_snapshot_matches`
# asserts the live module matches this table exactly.
# ---------------------------------------------------------------------------

EXPECTED_GUID_STRINGS = types.MappingProxyType({
    "EFI_GLOBAL_VARIABLE_GUID": "8be4df61-93ca-11d2-aa0d-00e098032b8c",
    "EFI_IMAGE_SECURITY_DATABASE_GUID": "d719b2cb-3d3a-4596-a3bc-dad00e67656f",
    "EFI_SYSTEM_RESOURCE_TABLE_GUID": "b122a263-3661-4f68-9929-78f8b0d62180",
    "EFI_MEMORY_ATTRIBUTES_TABLE_GUID": "dcfa911d-26eb-469f-a220-38b7dc461220",
    "EFI_CAPSULE_REPORT_GUID": "39b68c46-f7fb-441b-b6ec-16b0f69821f3",
    "EFI_CONFORMANCE_PROFILE_TABLE_GUID": "36122546-f7e7-4c8f-bd9b-eb8525b50c0b",
    "EFI_ACPI_TABLE_GUID": "8868e871-e4f1-11d3-bc22-0080c73c8881",
    "SMBIOS_TABLE_GUID": "eb9d2d31-2d88-11d3-9a16-0090273fc14d",
    "SMBIOS3_TABLE_GUID": "f2fd1544-9794-4a2c-992e-e5bbcf20e394",
    "EFI_RT_PROPERTIES_TABLE_GUID": "eb66918a-7eef-402a-842e-931d21c38ae9",
    "EFI_DEVICE_PATH_PROTOCOL_GUID": "09576e91-6d3f-11d2-8e39-00a0c969723b",
    "EFI_LOADED_IMAGE_PROTOCOL_GUID": "5b1b31a1-9562-11d2-8e3f-00a0c969723b",
    "EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID": "86c77a67-0b97-4633-a187-49104d0685c7",
})


class IndividualGuidTestCase(unittest.TestCase):

    def test_efi_global_variable_guid(self) -> None:
        self.assertIsInstance(EFI_GLOBAL_VARIABLE_GUID, uuid.UUID)
        self.assertEqual(str(EFI_GLOBAL_VARIABLE_GUID), "8be4df61-93ca-11d2-aa0d-00e098032b8c")
        return

    def test_efi_image_security_database_guid(self) -> None:
        self.assertIsInstance(EFI_IMAGE_SECURITY_DATABASE_GUID, uuid.UUID)
        self.assertEqual(str(EFI_IMAGE_SECURITY_DATABASE_GUID), "d719b2cb-3d3a-4596-a3bc-dad00e67656f")
        return

    def test_efi_system_resource_table_guid(self) -> None:
        self.assertIsInstance(EFI_SYSTEM_RESOURCE_TABLE_GUID, uuid.UUID)
        self.assertEqual(str(EFI_SYSTEM_RESOURCE_TABLE_GUID), "b122a263-3661-4f68-9929-78f8b0d62180")
        return

    def test_efi_memory_attributes_table_guid(self) -> None:
        self.assertIsInstance(EFI_MEMORY_ATTRIBUTES_TABLE_GUID, uuid.UUID)
        self.assertEqual(str(EFI_MEMORY_ATTRIBUTES_TABLE_GUID), "dcfa911d-26eb-469f-a220-38b7dc461220")
        return

    def test_efi_capsule_report_guid(self) -> None:
        self.assertIsInstance(EFI_CAPSULE_REPORT_GUID, uuid.UUID)
        self.assertEqual(str(EFI_CAPSULE_REPORT_GUID), "39b68c46-f7fb-441b-b6ec-16b0f69821f3")
        return

    def test_efi_conformance_profile_table_guid(self) -> None:
        self.assertIsInstance(EFI_CONFORMANCE_PROFILE_TABLE_GUID, uuid.UUID)
        self.assertEqual(str(EFI_CONFORMANCE_PROFILE_TABLE_GUID), "36122546-f7e7-4c8f-bd9b-eb8525b50c0b")
        return

    def test_efi_acpi_table_guid(self) -> None:
        self.assertIsInstance(EFI_ACPI_TABLE_GUID, uuid.UUID)
        self.assertEqual(str(EFI_ACPI_TABLE_GUID), "8868e871-e4f1-11d3-bc22-0080c73c8881")
        return

    def test_smbios_table_guid(self) -> None:
        self.assertIsInstance(SMBIOS_TABLE_GUID, uuid.UUID)
        self.assertEqual(str(SMBIOS_TABLE_GUID), "eb9d2d31-2d88-11d3-9a16-0090273fc14d")
        return

    def test_smbios3_table_guid(self) -> None:
        self.assertIsInstance(SMBIOS3_TABLE_GUID, uuid.UUID)
        self.assertEqual(str(SMBIOS3_TABLE_GUID), "f2fd1544-9794-4a2c-992e-e5bbcf20e394")
        return

    def test_efi_rt_properties_table_guid(self) -> None:
        self.assertIsInstance(EFI_RT_PROPERTIES_TABLE_GUID, uuid.UUID)
        self.assertEqual(str(EFI_RT_PROPERTIES_TABLE_GUID), "eb66918a-7eef-402a-842e-931d21c38ae9")
        return

    def test_efi_device_path_protocol_guid(self) -> None:
        self.assertIsInstance(EFI_DEVICE_PATH_PROTOCOL_GUID, uuid.UUID)
        self.assertEqual(str(EFI_DEVICE_PATH_PROTOCOL_GUID), "09576e91-6d3f-11d2-8e39-00a0c969723b")
        return

    def test_efi_loaded_image_protocol_guid(self) -> None:
        self.assertIsInstance(EFI_LOADED_IMAGE_PROTOCOL_GUID, uuid.UUID)
        self.assertEqual(str(EFI_LOADED_IMAGE_PROTOCOL_GUID), "5b1b31a1-9562-11d2-8e3f-00a0c969723b")
        return

    def test_efi_firmware_management_protocol_guid(self) -> None:
        self.assertIsInstance(EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID, uuid.UUID)
        self.assertEqual(str(EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID), "86c77a67-0b97-4633-a187-49104d0685c7")
        return


class WellKnownGuidsMappingTestCase(unittest.TestCase):

    def test_is_mapping_proxy(self) -> None:
        self.assertIsInstance(WELL_KNOWN_GUIDS, types.MappingProxyType)
        return

    def test_all_values_are_uuid(self) -> None:
        for name, value in WELL_KNOWN_GUIDS.items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(value, uuid.UUID)
        return

    def test_mapping_is_readonly(self) -> None:
        with self.assertRaises(TypeError):
            WELL_KNOWN_GUIDS["NEW"] = uuid.UUID("00000000-0000-0000-0000-000000000000")  # type: ignore[index]
        return

    def test_mapping_contains_required_names(self) -> None:
        required_names = {
            "EFI_GLOBAL_VARIABLE_GUID",
            "EFI_IMAGE_SECURITY_DATABASE_GUID",
            "EFI_SYSTEM_RESOURCE_TABLE_GUID",
            "EFI_MEMORY_ATTRIBUTES_TABLE_GUID",
            "EFI_CAPSULE_REPORT_GUID",
            "EFI_CONFORMANCE_PROFILE_TABLE_GUID",
            "EFI_ACPI_TABLE_GUID",
            "SMBIOS_TABLE_GUID",
            "SMBIOS3_TABLE_GUID",
            "EFI_RT_PROPERTIES_TABLE_GUID",
        }
        missing = required_names.difference(WELL_KNOWN_GUIDS.keys())
        self.assertEqual(len(missing), 0, msg="Missing required names: {}".format(missing))
        return


class GuidRegistrySnapshotTestCase(unittest.TestCase):

    def test_snapshot_matches(self) -> None:
        observed = {name: str(value) for name, value in WELL_KNOWN_GUIDS.items()}
        expected = dict(EXPECTED_GUID_STRINGS)

        # Report a helpful diff on mismatch rather than a bare assertEqual dump.
        missing_from_module = set(expected.keys()).difference(observed.keys())
        extra_in_module = set(observed.keys()).difference(expected.keys())
        value_mismatches = []
        for name in sorted(set(expected.keys()).intersection(observed.keys())):
            if observed[name] != expected[name]:
                value_mismatches.append((name, expected[name], observed[name]))

        problems = []
        if len(missing_from_module) > 0:
            problems.append("missing from module: {}".format(sorted(missing_from_module)))
        if len(extra_in_module) > 0:
            problems.append("unexpected in module: {}".format(sorted(extra_in_module)))
        for name, exp, obs in value_mismatches:
            problems.append("mismatch {}: expected={} observed={}".format(name, exp, obs))

        self.assertEqual(len(problems), 0, msg="; ".join(problems))
        return

    def test_module_all_lists_every_guid(self) -> None:
        module_all = set(uefiguids.__all__)
        registry_keys = set(WELL_KNOWN_GUIDS.keys())
        missing = registry_keys.difference(module_all)
        self.assertEqual(len(missing), 0, msg="Names in registry but not __all__: {}".format(missing))
        return


if __name__ == "__main__":
    unittest.main()

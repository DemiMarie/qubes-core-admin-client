# -*- encoding: utf8 -*-
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2015-2016  Wojtek Porczyk <woju@invisiblethingslab.com>
# Copyright (C) 2016       Bahtiar `kalkin-` Gadimov <bahtiar@gadimov.de>
# Copyright (C) 2017 Marek Marczykowski-Górecki
#                               <marmarek@invisiblethingslab.com>
# Copyright (C) 2024 Piotr Bartman-Szwarc <prbartman@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License along
# with this program; if not, see <http://www.gnu.org/licenses/>.

"""API for various types of devices.

Main concept is that some domain main
expose (potentially multiple) devices, which can be attached to other domains.
Devices can be of different classes (like 'pci', 'usb', etc.). Each device
class is implemented by an extension.

Devices are identified by pair of (backend domain, `port_id`), where `port_id`
is :py:class:`str`.
"""
import itertools
from typing import Iterable

import qubesadmin.exc
from qubesadmin.device_protocol import (
    Port,
    DeviceInfo,
    UnknownDevice,
    DeviceAssignment,
    VirtualDevice,
    AssignmentMode, DeviceInterface,
)


DEVICE_DENY_LIST = "/etc/qubes/device-deny.list"


class DeviceCollection:
    """Bag for devices.

    Used as default value for :py:meth:`DeviceManager.__missing__` factory.

    :param vm: VM for which we manage devices
    :param class_: device class

    """

    def __init__(self, vm, class_):
        self._vm = vm
        self._class = class_
        self._dev_cache = {}

    def attach(self, assignment: DeviceAssignment) -> None:
        """
        Attach (add) device to domain.

        :param DeviceAssignment assignment: device object
        """
        if assignment.devclass == "pci":
            raise qubesadmin.exc.QubesValueError(
                "PCI devices cannot be attached manually, "
                "did you mean `qvm-pci assign --required ...`"
            )
        self._add(assignment, "attach")

    def detach(self, assignment: DeviceAssignment) -> None:
        """
        Detach (remove) device from domain.

        :param DeviceAssignment assignment: device to detach
            (obtained from :py:meth:`assignments`)
        """
        self._remove(assignment, "detach")

    def assign(self, assignment: DeviceAssignment) -> None:
        """
        Assign device to domain (add to :file:`qubes.xml`).

        :param DeviceAssignment assignment: device object
        """
        if (
            assignment.devclass not in ("pci", "testclass", "block")
            and assignment.required
        ):
            raise qubesadmin.exc.QubesValueError(
                "Only pci and block devices can be assigned as required."
            )
        if assignment.devclass == "pci" and not assignment.required:
            raise qubesadmin.exc.QubesValueError(
                "PCI devices cannot be assigned as not required."
            )
        if (
            assignment.devclass
            not in ("testclass", "usb", "block", "mic", "pci")
            and assignment.attach_automatically
        ):
            raise qubesadmin.exc.QubesValueError(
                f"{assignment.devclass} devices cannot be assigned "
                "to be automatically attached."
            )

        self._add(assignment, "assign")

    def unassign(self, assignment: DeviceAssignment) -> None:
        """
        Unassign device from domain (remove from :file:`qubes.xml`).

        :param DeviceAssignment assignment: device to unassign
            (obtained from :py:meth:`assignments`)
        """
        self._remove(assignment, "unassign")

    def _add(self, assignment: DeviceAssignment, action: str) -> None:
        """
        Helper for attaching/assigning device.
        """
        if not assignment.frontend_domain:
            assignment.frontend_domain = self._vm
        if assignment.frontend_domain != self._vm:
            raise qubesadmin.exc.QubesValueError(
                f"Trying to {action} device belonging to other domain:"
                f" {assignment.frontend_domain}"
            )
        if assignment.devclass != self._class:
            raise qubesadmin.exc.QubesValueError(
                f"Device class does not match to expected: "
                f"{assignment.devclass=}!={self._class=}"
            )

        self._vm.qubesd_call(
            None,
            f"admin.vm.device.{self._class}.{action.capitalize()}",
            repr(assignment),
            assignment.serialize(),
        )

    def _remove(self, assignment: DeviceAssignment, action: str) -> None:
        """
        Helper for detaching/unassigning device.
        """
        if (
            assignment.frontend_domain
            and assignment.frontend_domain != self._vm
        ):
            raise qubesadmin.exc.QubesValueError(
                f"Trying to {action} device belonging to other domain:"
                f" {assignment.frontend_domain}"
            )
        if assignment.devclass != self._class:
            raise qubesadmin.exc.QubesValueError(
                f"Device class does not match to expected: "
                f"{assignment.devclass=}!={self._class=}"
            )

        self._vm.qubesd_call(
            None,
            f"admin.vm.device.{self._class}.{action.capitalize()}",
            repr(assignment),
        )

    def get_dedicated_devices(self) -> Iterable[DeviceAssignment]:
        """
        List devices which are attached or assigned to this vm.
        """
        dedicated = set(
            itertools.chain(
                self.get_attached_devices(), self.get_assigned_devices()
            )
        )
        yield from dedicated

    def get_attached_devices(self) -> Iterable[DeviceAssignment]:
        """
        List devices which are attached to this vm.
        """
        assignments_str = self._vm.qubesd_call(
            None, "admin.vm.device.{}.Attached".format(self._class)
        ).decode()
        for assignment_str in assignments_str.splitlines():
            head, _, untrusted_rest = assignment_str.partition(" ")
            device = VirtualDevice.from_qarg(
                head, self._class, self._vm.app.domains, blind=True
            )

            yield DeviceAssignment.deserialize(
                untrusted_rest.encode("ascii"), expected_device=device
            )

    def get_assigned_devices(
        self, required_only: bool = False
    ) -> Iterable[DeviceAssignment]:
        """
        Devices assigned to this vm (included in :file:`qubes.xml`).

        Safe to access before libvirt bootstrap.
        """
        assignments_str = self._vm.qubesd_call(
            None, "admin.vm.device.{}.Assigned".format(self._class)
        ).decode()
        for assignment_str in assignments_str.splitlines():
            head, _, untrusted_rest = assignment_str.partition(" ")
            device = VirtualDevice.from_qarg(
                head, self._class, self._vm.app.domains, blind=True
            )

            assignment = DeviceAssignment.deserialize(
                untrusted_rest.encode("ascii"), expected_device=device
            )
            if not required_only or assignment.required:
                yield assignment

    def get_exposed_devices(self) -> Iterable[DeviceInfo]:
        """
        List devices exposed by this vm.
        """
        devices: bytes = self._vm.qubesd_call(
            None, "admin.vm.device.{}.Available".format(self._class)
        )
        for dev_serialized in devices.splitlines():
            yield DeviceInfo.deserialize(
                serialization=dev_serialized,
                expected_backend_domain=self._vm,
                expected_devclass=self._class,
            )

    def update_assignment(self, device: Port, required: AssignmentMode):
        """
        Update assignment of already attached device.

        :param VirtualDevice device: device for which change required flag
        :param bool required: new assignment:
                              `None` -> unassign device from qube
                              `False` -> device will be auto-attached to qube
                              `True` -> device is required to start qube
        """
        self._vm.qubesd_call(
            None,
            "admin.vm.device.{}.Set.assignment".format(self._class),
            repr(device),
            required.value.encode("utf-8"),
        )

    __iter__ = get_exposed_devices

    def clear_cache(self):
        """
        Clear cache of available devices.
        """
        self._dev_cache.clear()

    def __getitem__(self, item):
        """Get device object with given port_id.

        :returns: py:class:`DeviceInfo`

        If domain isn't running, it is impossible to check device validity,
        so return UnknownDevice object. Also do the same for non-existing
        devices - otherwise it will be impossible to detach already
        disconnected device.
        """
        # fist, check if we have cached device info
        if item in self._dev_cache:
            return self._dev_cache[item]
        # then look for available devices
        for dev in self.get_exposed_devices():
            if dev.port_id == item:
                self._dev_cache[item] = dev
                return dev
        # if still nothing, return UnknownDevice instance for the reason
        # explained in docstring, but don't cache it
        return UnknownDevice(Port(self._vm, item, devclass=self._class))


class DeviceManager(dict):
    """Device manager that hold all devices by their classes.

    :param vm: VM for which we manage devices
    """

    def __init__(self, vm):
        super().__init__()
        self._vm = vm

    def __missing__(self, key):
        self[key] = DeviceCollection(self._vm, key)
        return self[key]

    def __iter__(self):
        return iter(self._vm.app.list_deviceclass())

    def keys(self):
        return self._vm.app.list_deviceclass()

    def deny(self, *interfaces: Iterable[DeviceInterface]):
        """
        Deny a device with any of the given interfaces from attaching to the VM.
        """
        self._vm.qubesd_call(
            None,
            "admin.vm.device.denied.Add",
            None,
            "".join(repr(ifc) for ifc in interfaces).encode('ascii'),
        )

    def allow(self, *interfaces: Iterable[DeviceInterface]):
        """
        Remove given interfaces from denied list.
        """
        self._vm.qubesd_call(
            None,
            "admin.vm.device.denied.Remove",
            None,
            "".join(repr(ifc) for ifc in interfaces).encode('ascii'),
        )

import enum


class Roles(enum.IntEnum):
    """Basic machine roles."""

    #: Where the database (mariadb runs).
    DB = 1

    #: Rabbit.
    RB = 2

    #: Map servers are the parent + glance + keystone + top level things.
    MAP = 3

    #: Cap servers are what we call child cells.
    CAP = 4

    #: A kvm/qemu hypervisor + n-cpu + n-api-meta.
    HV = 5

import enum


class Roles(enum.Enum):
    """Basic machine roles."""

    #: Cap servers are what we call child cells.
    CAP = 'cap'

    #: Where the database (mariadb runs).
    DB = 'db'

    #: Map servers are the parent + glance + keystone + top level things.
    MAP = 'map'

    #: Rabbit.
    RB = 'rb'

    #: A kvm/qemu hypervisor + n-cpu + n-api-meta.
    HV = 'hv'

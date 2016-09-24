from builder.roles import Roles

# The default stack user name and password...
#
# Someday make this better?
DEF_USER, DEF_PW = ('stack', 'stack')

DEF_SETTINGS = {
    # We can't seem to alter this one more than once,
    # so just leave it as is... todo fix this and make it so that
    # we reset it...
    'DATABASE_USER': DEF_USER,
    # Devstack will also change the root database password to this,
    # unsure why it desires to do that...
    #
    # This may require work...
    'DATABASE_PASSWORD': DEF_PW,
    # This appears to be the default, leave it be...
    'RABBIT_USER': 'stackrabbit',
}

DEF_FLAVORS = {
    Roles.CAP: 'm1.medium',
    Roles.DB: 'm1.medium',
    Roles.MAP: 'm1.large',
    Roles.RB: 'm1.medium',
    Roles.HV: 'm1.large',
}

DEF_TOPO = {
    'templates':  {
        Roles.CAP: 'cap-%(rand)s',
        Roles.MAP: 'map-%(rand)s',
        Roles.DB: 'db-%(rand)s',
        Roles.RB: 'rb-%(rand)s',
        Roles.HV: 'hv-%(rand)s',
    },
    'control': {},
    'compute': [],
}

STACK_SH = '/home/%s/devstack/stack.sh' % DEF_USER
STACK_SOURCE = 'git://git.openstack.org/openstack-dev/devstack'

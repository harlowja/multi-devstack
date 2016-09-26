[[local|localrc]]

{% include 'local.shared.tpl' %}

ENABLED_SERVICES=rabbit

# This signals to the rabbit setup script to ensure that the
# needed vhost settings are included & adjusted... (it does not mean we
# are going to install nova).
ENABLED_SERVICES+=,n-cell

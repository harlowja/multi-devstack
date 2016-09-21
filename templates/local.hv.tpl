[[local|localrc]]

{% include 'local.shared.tpl' %}

VIRT_DRIVER=libvirt
LIBVIRT_TYPE=qemu
FORCE_CONFIG_DRIVE=True

ENABLED_SERVICES=nova
ENABLED_SERVICES+=n-api-meta,n-cpu,n-net,
DISABLED_SERVICE+=,n-sch,n-api,n-obj,n-novnc,n-xvnc,n-spice
DISABLED_SERVICE+=,n-crt,n-cauth,n-sproxy
DISABLED_SERVICE+=,mysql,postgresql

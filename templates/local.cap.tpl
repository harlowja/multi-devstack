[[local|localrc]]

{% include 'local.shared.tpl' %}

ENABLED_SERVICES=nova
ENABLED_SERVICES+=,n-cell,n-cell-child,n-cond
DISABLED_SERVICE+=,n-cpu,n-net,n-sch,n-api-meta,n-obj,n-novnc,n-xvnc,n-spice
DISABLED_SERVICE+=,n-crt,n-cauth,n-sproxy,n-api
DISABLED_SERVICE+=,mysql,postgresql

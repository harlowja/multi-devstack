[[local|localrc]]

{% include 'local.shared.tpl' %}

ENABLED_SERVICES=key,glance,nova,mysql
ENABLED_SERVICES+=,n-cell,n-cell-region,n-api,g-api,g-reg
DISABLED_SERVICE+=,n-cpu,n-net,n-sch,n-api-meta,n-obj,n-novnc,n-xvnc,n-spice
DISABLED_SERVICE+=,n-crt,n-cauth,n-sproxy,n-cell-child

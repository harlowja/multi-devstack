[[local|localrc]]

{% include 'local.shared.tpl' %}

ENABLED_SERVICES=key,glance,nova
ENABLED_SERVICES+=,n-cell-region,n-api,g-api,g-reg,n-api-db
DISABLED_SERVICE+=,n-cpu,n-net,n-sch,n-api-meta,n-obj,n-novnc,n-xvnc,n-spice
DISABLED_SERVICE+=,n-crt,n-cauth,n-sproxy,n-cell-child

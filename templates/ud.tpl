#!/bin/bash
set -x

# Install some common things...
yum install -y git nano
yum install -y python-devel
yum install -y libffi-devel openssl-devel mysql-devel \
               postgresql-devel libxml2-devel libxslt-devel openldap-devel

# Seems needed as a fix to avoid devstack later breaking...
touch /etc/sysconfig/iptables

# Create the user we want...
tobe_user='{{ USER }}'
tobe_user_pw='{{ USER_PW }}'
id -u $tobe_user &>/dev/null
if [ $? -ne 0 ]; then
    useradd "$tobe_user" --groups root --gid 0 -m -s /bin/bash -d "/home/$tobe_user"
fi
echo "$tobe_user_pw" | passwd --stdin "$tobe_user"

sudo_fn="99_sudo_${tobe_user}"
cat > /etc/sudoers.d/$sudo_fn << EOF
# Automatically generated at slave creation time.
#
# Do not edit.
$tobe_user ALL=(ALL) NOPASSWD:ALL
EOF


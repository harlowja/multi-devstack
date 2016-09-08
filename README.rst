=====================================
A somewhat ghetto multi-node devstack
=====================================

Made by people that care.

How to use it
-------------

So a basic understanding of `devstack`_ is useful
but not needed, what is required is a cloud that you can
get virtual machine from (and currently we assume centos7
so it may not work elsewhere without changes).

Once you have that ensured the the following should
be done:

1. Make a virtualenv, install the ``requirements.txt`` file into that.
2. Ensure that you have exported the following ``OS_AUTH_URL``
   and ``OS_USERNAME`` and ``OS_TENANT_NAME`` and most importantly
   ``OS_PASSWORD``.

    For example::

        $ env | grep OS_ | grep -v OS_PASS
        OS_AUTH_URL=https://openstack.int.godaddy.com:35357/v2.0
        OS_USERNAME=jxharlow
        OS_TENANT_NAME=user-jxharlow

3. Run the ``make_vms`` (ansible) playbook (you may need to edit this
   and the shell scripts that will be processed to adjust names
   of servers or names of keys that you are using in your cloud).

.. _devstack: http://docs.openstack.org/developer/devstack/

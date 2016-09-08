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

* First make a virtualenv, install the ``requirements.txt`` file
  into that.
* First run the ``make_vm`` (ansible) playbook.

.. _devstack: http://docs.openstack.org/developer/devstack/
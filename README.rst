=======================================
A python-based multi-node devstack tool
=======================================

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

3. Run ``./builder.sh create`` and watch the steps trigger that
   once successful will have build yourself a cluster.

   * If it fails after the ``stack.sh`` commands have started to
     run **ensure to run** ``./builder.sh destroy`` before re-running
     because those commands are not idempotent (the other actions are).

What it does (during create)
----------------------------

* Creates a cloud instance (configuring itself via the mechanisms
  supported by the `shade`_ library).
* Creates/opens a local sqlite3 database (used for state tracking and data
  retention; primarily used for resuming, allowing for this program
  to be mostly crash-tolerant).
* Validates provided ``create`` arguments against the matched cloud (for
  example to ensure the image provided is actually found).
* Creates a desired instance layout (and saves it).
* Scans current cloud servers and sees if layout is satisfied (if not servers
  are spawned to match the desired layout).
* Performs (and records what was done and what was not) remote server
  commands on all matched (or spawned) servers to turn
  them into a multi-node `devstack`_ cloud.

What it does (during destroy)
-----------------------------

* Creates a cloud instance (configuring itself via the mechanisms
  supported by the `shade`_ library).
* Creates/opens a local sqlite3 database (used for state tracking and data
  retention; primarily used for resuming, allowing for this program
  to be mostly crash-tolerant).
* Extracts prior servers from local sqlite3 database and
  destroys them (by whatever mechanism the underlying cloud performs
  such actions).

What is not done (yet)
----------------------

* Creating a overlay network (so that the VMs
  spawned can connect and communicate, likely in a private
  only fashion).

  * Perhaps using something inspired by and/or like
    https://github.com/openstack-infra/devstack-gate/blob/master/functions.sh#L1105 ?

* Ensuring the hypervisors spun up are all connected
  together correctly and actually work.
* Making sure the cells (parent and child nova cell)
  actually works.
* Using neutron (vs nova-net).
* Making it easier to plug-in new projects (in some modular
  manner?).
* Removing various godaddy artifacts (metadata for example).

Get involved
------------

Contributions are always welcome, find me on IRC
or `openstack-dev`_ (ML) or in person or just open
a pull request.

.. _devstack: http://docs.openstack.org/developer/devstack/
.. _shade: https://pypi.python.org/pypi/shade
.. _openstack-dev: http://lists.openstack.org/pipermail/openstack-dev/

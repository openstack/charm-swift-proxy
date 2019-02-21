Overview
--------

This charm provides the swift-proxy component of the OpenStack Swift object
storage system.  It can be deployed as part of its own stand-alone storage
cluster or it can be integrated with the other OpenStack components, assuming
those are also managed by Juju.  For Swift to function, you'll also need to
deploy additional swift-storage nodes using the cs:precise/swift-storage
charm.

For more information about Swift and its architecture, visit the
[official project website](https://docs.openstack.org/developer/swift)

This charm was developed to support deploying multiple version of Swift on
Ubuntu Precise 12.04, as they relate to the release series of OpenStack.  That
is, OpenStack Essex corresponds to Swift 1.4.8 while OpenStack Folsom shipped
1.7.4.  This charm can be used to deploy either (and future) versions of Swift
onto an Ubuntu Precise 12.04 or Trusty 14.04 making use of the Ubuntu Cloud
Archive when needed.

Usage
-----

Currently, Swift may be deployed in two ways.   In either case, additional
storage nodes are required.  The configuration option that dictates
how to deploy this charm is the 'zone-assignment' setting.  This section
describes how to select the appropriate zone assignment policy, as well as
a few other configuration settings of interest.  Many of the configuration
settings can be left as default.

**Zone Assignment**

This setting determines how the charm assigns new storage nodes to storage
zones.

The default, 'manual' option is suggested for production as it allows
administrators to carefully architect the storage cluster.  It requires each
swift-storage service to be deployed with an explicit storage zone configured
in its deployment settings.  Upon relation to a swift-proxy, the storage node
will request membership to its configured zone and be assigned by the
swift-proxy charm accordingly.  Using the cs:precise/swift-storage charm with
this charm, a deployment would look something like:

    $ cat >swift.cfg <<END
        swift-proxy:
            zone-assignment: manual
            replicas: 3
        swift-storage-zone1:
            zone: 1
            block-device: /etc/swift/storage.img|2G
        swift-storage-zone2:
            zone: 2
            block-device: /etc/swift/storage.img|2G
        swift-storage-zone3:
            zone: 3
            block-device: /etc/swift/storage.img|2G
    END
    $ juju deploy --config=swift.cfg swift-proxy
    $ juju deploy --config=swift.cfg swift-storage swift-storage-zone1
    $ juju deploy --config=swift.cfg swift-storage swift-storage-zone2
    $ juju deploy --config=swift.cfg swift-storage swift-storage-zone3
    $ juju add-relation swift-proxy swift-storage-zone1
    $ juju add-relation swift-proxy swift-storage-zone2
    $ juju add-relation swift-proxy swift-storage-zone3

This will result in a configured storage cluster of 3 zones, each with one
node.  To expand capacity of the storage system, nodes can be added to specific
zones in the ring.

    $ juju add-unit swift-storage-zone1
    $ juju add-unit -n5 swift-storage-zone3    # Adds 5 units to zone3

This charm will not balance the storage ring until there are enough storage
zones to meet its minimum replica requirement, in this case 3.

The other option for zone assignment is 'auto'.  In this mode, swift-proxy
gets a relation to a single swift-storage service unit.  Each machine unit
assigned to that service unit will be distributed evenly across zones.

    $ cat >swift.cfg <<END
    swift-proxy:
        zone-assignment: auto
        replicas: 3
    swift-storage:
        zone: 1
        block-device: /etc/swift/storage.img|2G
    END
    $ juju deploy --config=swift.cfg swift-proxy
    $ juju deploy --config=swift.cfg swift-storage
    $ juju add-relation swift-proxy swift-storage
    # The swift-storage/0 unit ends up the first node in zone 1
    $ juju add-unit swift-storage
    # swift-storage/1 ends up the first node in zone 2.
    $ juju add-unit swift-storage
    # swift-storage/2 is the first in zone 3, replica requirement is satisfied
    # the ring is balanced.

Extending the ring in the case is just a matter of adding more units to the
single service unit.  New units will be distributed across the existing zones.

    $ juju add-unit swift-storage
    # swift-storage/3 is assigned to zone 1.
    $ juju add-unit swift-storage
    # swift-storage/4 is assigned to zone 2.
    etc.

**Global Cluster.**

This charm supports Swift Global Cluster feature as described at
https://docs.openstack.org/swift/latest/overview_global_cluster.html .
In order to enable it the 'enable-multi-region' option has to be set to 'True'.
Additional options ('read-affinity', 'write-affinity' and
'write-affinity-node-count') can be used to influence how the objects will be
read and written.

In addition storage nodes have to be configured with the 'region' option and
related to all proxies participating in the global cluster. More than one proxy
can be deployed, but they have to be related using 'rings-distributor'
/ 'rings-consumer' endpoints and the 'swift-hash' option has to be unique across
them. Only one proxy can act as a rings-distributor at a time.

    $ cat >swift.cfg <<END
    sp-r1:
        region: RegionOne
        zone-assignment: manual
        replicas: 2
        enable-multi-region: true
        swift-hash: "global-cluster"
        read-affinity: "r1=100, r2=200"
        write-affinity: "r1, r2"
        write-affinity-node-count: "1"
    sp-r2:
        region: RegionTwo
        zone-assignment: manual
        replicas: 2
        enable-multi-region: true
        swift-hash: "global-cluster"
        read-affinity: "r2=100, r1=200"
        write-affinity: "r2, r1"
        write-affinity-node-count: "1"
    ss-r1:
        storage-region: 1
        zone: 1
        block-device: /etc/swift/storage.img|2G
    ss-r2:
        storage-region: 2
        zone: 1
        block-device: /etc/swift/storage.img|2G
    END
    $ juju deploy --config=swift.cfg swift-proxy sp-r1
    $ juju deploy --config=swift.cfg swift-proxy sp-r2
    $ juju deploy --config=swift.cfg swift-storage ss-r1
    $ juju deploy --config=swift.cfg swift-storage ss-r2
    $ juju add-relation sp-r1:swift-storage ss-r1:swift-storage
    $ juju add-relation sp-r1:swift-storage ss-r2:swift-storage
    $ juju add-relation sp-r2:swift-storage ss-r1:swift-storage
    $ juju add-relation sp-r2:swift-storage ss-r2:swift-storage
    $ juju add-relation sp-r1:rings-distributor sp-r2:rings-consumer

In case of the failure of 'sp-r1', if it is not possible to recover it, the
relation should be removed:

    $ juju remove-relation sp-r2:rings-consumer sp-r1:rings-distributor

Additional proxy can be deployed later and related to 'swift-proxy-region2'
using 'rings-distributor' / 'rings-consumer' endpoints.

**Installation repository.**

The 'openstack-origin' setting allows Swift to be installed from installation
repositories and can be used to setup access to the Ubuntu Cloud Archive
to support installing Swift versions more recent than what is shipped with
Ubuntu 12.04 (1.4.8).  For more information, see config.yaml.

**Authentication.**

By default, the charm will be deployed using the tempauth auth system.  This is
a simple and not-recommended auth system that functions without any external
dependencies.  See Swift documentation for details.

The charm may also be configured to use Keystone, either manually (via config)
or automatically via a relation to an existing Keystone service using the
cs:precise/keystone charm.  The latter is preferred, however, if a Keystone
service is desired but it is not managed by Juju, the configuration for the
auth token middleware can be set manually via the charm's config.  A relation
to a Keystone server via the identity-service interface will configure
swift-proxy with the appropriate credentials to make use of Keystone and is
required for any integration with other OpenStack components.

**Glance**

Swift may be used to as a storage backend for the Glance image service.  To do
so, simply add a relation between swift-proxy and an existing Glance service
deployed using the cs:precise/glance charm.

HA/Clustering
-------------

There are two mutually exclusive high availability options: using virtual
IP(s) or DNS. In both cases, a relationship to hacluster is required which
provides the corosync back end HA functionality.

To use virtual IP(s) the clustered nodes must be on the same subnet such that
the VIP is a valid IP on the subnet for one of the node's interfaces and each
node has an interface in said subnet. The VIP becomes a highly-available API
endpoint.

At a minimum, the config option 'vip' must be set in order to use virtual IP
HA. If multiple networks are being used, a VIP should be provided for each
network, separated by spaces. Optionally, vip_iface or vip_cidr may be
specified.

To use DNS high availability there are several prerequisites. However, DNS HA
does not require the clustered nodes to be on the same subnet.
Currently the DNS HA feature is only available for MAAS 2.0 or greater
environments. MAAS 2.0 requires Juju 2.0 or greater. The clustered nodes must
have static or "reserved" IP addresses registered in MAAS. The DNS hostname(s)
must be pre-registered in MAAS before use with DNS HA.

At a minimum, the config option 'dns-ha' must be set to true and at least one
of 'os-public-hostname', 'os-internal-hostname' or 'os-internal-hostname' must
be set in order to use DNS HA. One or more of the above hostnames may be set.

The charm will throw an exception in the following circumstances:
If neither 'vip' nor 'dns-ha' is set and the charm is related to hacluster
If both 'vip' and 'dns-ha' are set as they are mutually exclusive
If 'dns-ha' is set and none of the os-{admin,internal,public}-hostname(s) are
set

Network Space support
---------------------

This charm supports the use of Juju Network Spaces, allowing the charm to be bound to network space configurations managed directly by Juju.  This is only supported with Juju 2.0 and above.

API endpoints can be bound to distinct network spaces supporting the network separation of public, internal and admin endpoints.

To use this feature, use the --bind option when deploying the charm:

    juju deploy swift-proxy --bind "public=public-space internal=internal-space admin=admin-space"

alternatively these can also be provided as part of a juju native bundle configuration:

    swift-proxy:
      charm: cs:xenial/swift-proxy
      num_units: 1
      bindings:
        public: public-space
        admin: admin-space
        internal: internal-space

**NOTE:** Spaces must be configured in the underlying provider prior to attempting to use them.

**NOTE:** Existing deployments using os-\*-network configuration options will continue to function; these options are preferred over any network space binding provided if set.

Telemetry support
------------------

For OpenStack releases >= Mitaka, improved telemetry collection support is possible by
adding a relation between swift-proxy and rabbitmq-server:

    juju add-relation swift-proxy rabbitmq-server

**NOTE:** In a busy Swift deployment this can place additional load on the underlying
message bus.

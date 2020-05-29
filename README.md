# Overview

OpenStack [Swift][swift-upstream] is a highly available, distributed,
eventually consistent object/blob store.

The swift-proxy charm deploys Swift's proxy component. The charm's basic
function is to manage zone assignment and enforce replica requirements for the
storage nodes. It works in tandem with the [swift-storage][swift-storage-charm]
charm, which is used to add storage nodes.

# Usage

## Configuration

This section covers common configuration options. See file `config.yaml` for
the full list of options, along with their descriptions and default values.

### `zone-assignment`

The `zone-assignment` option defines the zone assignment method for storage
nodes. Values include 'manual' (the default) and 'auto'.

### `replicas`

The `replicas` option stipulates the number of data replicas are needed. This
value should be equal to the number of zones. The default value is '3'.

## Deployment

Let file ``swift.yaml`` contain the deployment configuration:

```yaml
    swift-proxy:
        zone-assignment: manual
        replicas: 3
    swift-storage-zone1:
        zone: 1
        block-device: /dev/sdb
    swift-storage-zone2:
        zone: 2
        block-device: /dev/sdb
    swift-storage-zone3:
        zone: 3
        block-device: /dev/sdb
```

Deploy the proxy and storage nodes:

    juju deploy --config swift.yaml swift-proxy
    juju deploy --config swift.yaml swift-storage swift-storage-zone1
    juju deploy --config swift.yaml swift-storage swift-storage-zone2
    juju deploy --config swift.yaml swift-storage swift-storage-zone3

Add relations between the proxy node and all storage nodes:

    juju add-relation swift-proxy:swift-storage swift-storage-zone1:swift-storage
    juju add-relation swift-proxy:swift-storage swift-storage-zone2:swift-storage
    juju add-relation swift-proxy:swift-storage swift-storage-zone3:swift-storage

This will result in a three-zone cluster, with each zone consisting of a single
storage node, thereby satisfying the replica requirement of three.

Storage capacity is increased by adding swift-storage units to a zone. For
example, to add two storage nodes to zone '3':

    juju add-unit -n 2 swift-storage-zone3

> **Note**: When scaling out ensure the candidate machines are equipped with
  the block devices currently configured for the associated application.

This charm will not balance the storage ring until there are enough storage
zones to meet its minimum replica requirement, in this case three.

Appendix [Swift usage][cdg-app-swift] in the [OpenStack Charms Deployment
Guide][cdg] offers in-depth guidance for deploying Swift with charms. In
particular, it shows how to set up a multi-region (global) cluster.

## Swift as backend for Glance

Swift may be used as a storage backend for the Glance image service. To do so,
add a relation between the swift-proxy and glance applications:

    juju add-relation swift-proxy:object-store glance:object-store

## Telemetry

Starting with OpenStack Mitaka improved telemetry collection support can be
achieved by adding a relation to rabbitmq-server:

    juju add-relation swift-proxy rabbitmq-server

Doing the above in a busy Swift deployment can add a significant amount of load
to the underlying message bus.

## High availability

When more than one unit is deployed with the [hacluster][hacluster-charm]
application the charm will bring up an HA active/active cluster.

There are two mutually exclusive high availability options: using virtual IP(s)
or DNS. In both cases the hacluster subordinate charm is used to provide the
Corosync and Pacemaker backend HA functionality.

See [OpenStack high availability][cdg-ha-apps] in the [OpenStack Charms
Deployment Guide][cdg] for details.

## Network spaces

This charm supports the use of Juju [network spaces][juju-docs-spaces] (Juju
`v.2.0`). This feature optionally allows specific types of the application's
network traffic to be bound to subnets that the underlying hardware is
connected to.

> **Note**: Spaces must be configured in the backing cloud prior to deployment.

API endpoints can be bound to distinct network spaces supporting the network
separation of public, internal, and admin endpoints.

For example, providing that spaces 'public-space', 'internal-space', and
'admin-space' exist, the deploy command above could look like this:

    juju deploy --config swift-proxy.yaml swift-proxy \
       --bind "public=public-space internal=internal-space admin=admin-space"

Alternatively, configuration can be provided as part of a bundle:

```yaml
    swift-proxy:
      charm: cs:swift-proxy
      num_units: 1
      bindings:
        public: public-space
        internal: internal-space
        admin: admin-space
```

> **Note**: Existing cinder units configured with the `os-admin-network`,
  `os-internal-network`, or `os-public-network` options will continue to honour
  them. Furthermore, these options override any space bindings, if set.

## Actions

This section lists Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis.

* `add-user`
* `diskusage`
* `dispersion-populate`
* `dispersion-report`
* `openstack-upgrade`
* `pause`
* `remove-devices`
* `resume`
* `set-weight`

To display action descriptions run `juju actions swift-proxy`.

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-swift-proxy].

For general charm questions refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[lp-bugs-charm-swift-proxy]: https://bugs.launchpad.net/charm-swift-proxy/+filebug
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-spaces]: https://jaas.ai/docs/spaces
[swift-storage]: https://jaas.ai/swift-storage
[cdg-app-swift]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-swift.html
[swift-upstream]: https://docs.openstack.org/developer/swift
[swift-storage-charm]: https://jaas.ai/swift-storage
[hacluster-charm]: https://jaas.ai/hacluster
[cdg-ha-apps]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-ha.html#ha-applications

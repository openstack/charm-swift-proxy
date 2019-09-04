#!/usr/bin/env python3
#
# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import sys
from subprocess import (
    check_call,
    CalledProcessError,
)
import time


_path = os.path.dirname(os.path.realpath(__file__))
_parent = os.path.abspath(os.path.join(_path, '..'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_parent)


from lib.swift_utils import (
    SwiftProxyCharmException,
    register_configs,
    restart_map,
    services,
    determine_packages,
    ensure_swift_dir,
    SWIFT_RINGS,
    get_www_dir,
    initialize_ring,
    SWIFT_HA_RES,
    get_zone,
    do_openstack_upgrade,
    setup_ipv6,
    update_rings,
    balance_rings,
    fully_synced,
    sync_proxy_rings,
    broadcast_rings_available,
    mark_www_rings_deleted,
    SwiftProxyClusterRPC,
    get_first_available_value,
    all_responses_equal,
    ensure_www_dir_permissions,
    sync_builders_and_rings_if_changed,
    cluster_sync_rings,
    is_most_recent_timestamp,
    timestamps_available,
    assess_status,
    try_initialize_swauth,
    clear_storage_rings_available,
    determine_replicas,
)

import charmhelpers.contrib.openstack.utils as openstack

from charmhelpers.contrib.openstack.ha.utils import (
    generate_ha_relation_data,
)

from charmhelpers.contrib.hahelpers.cluster import (
    is_elected_leader,
    determine_api_port,
)

from charmhelpers.core.hookenv import (
    config,
    local_unit,
    remote_unit,
    relation_set,
    relation_ids,
    relation_get,
    related_units,
    log,
    DEBUG,
    INFO,
    WARNING,
    Hooks, UnregisteredHookError,
    open_port,
    status_set,
)
from charmhelpers.core.host import (
    service_reload,
    service_restart,
    service_stop,
    service_start,
)
from charmhelpers.fetch import (
    apt_install,
    apt_update,
    filter_installed_packages,
)
from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.openstack.ip import (
    canonical_url,
    PUBLIC,
    INTERNAL,
    ADMIN,
)
from charmhelpers.contrib.network.ip import (
    get_relation_ip,
    format_ipv6_addr,
)
from charmhelpers.contrib.openstack.context import ADDRESS_TYPES
from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.contrib.hardening.harden import harden
from charmhelpers.contrib.openstack.cert_utils import (
    get_certificate_request,
    process_certificates,
)

extra_pkgs = [
    "haproxy",
    "python-jinja2"
]

hooks = Hooks()
CONFIGS = register_configs()
restart_on_change = openstack.pausable_restart_on_change


@hooks.hook('install.real')
@harden()
def install():
    status_set('maintenance', 'Executing pre-install')
    execd_preinstall()
    src = config('openstack-origin')
    if src != 'distro':
        openstack.configure_installation_source(src)

    status_set('maintenance', 'Installing apt packages')
    apt_update(fatal=True)
    rel = openstack.get_os_codename_install_source(src)
    pkgs = determine_packages(rel)
    apt_install(pkgs, fatal=True)
    apt_install(extra_pkgs, fatal=True)
    ensure_swift_dir()
    # configure a directory on webserver for distributing rings.
    ensure_www_dir_permissions(get_www_dir())


@hooks.hook('config-changed')
@restart_on_change(restart_map())
@harden()
def config_changed():
    if is_elected_leader(SWIFT_HA_RES):
        log("Leader established, generating ring builders", level=INFO)
        # initialize new storage rings.
        for ring, path in SWIFT_RINGS.items():
            if not os.path.exists(path):
                initialize_ring(path,
                                config('partition-power'),
                                determine_replicas(ring),
                                config('min-hours'))

    if config('prefer-ipv6'):
        status_set('maintenance', 'Configuring ipv6')
        setup_ipv6()

    configure_https()
    open_port(config('bind-port'))
    update_nrpe_config()

    # Determine whether or not we should do an upgrade.
    if not config('action-managed-upgrade') and \
            openstack.openstack_upgrade_available('swift'):
        do_openstack_upgrade(CONFIGS)
        status_set('maintenance', 'Running openstack upgrade')

    status_set('maintenance', 'Updating and (maybe) balancing rings')
    update_rings(min_part_hours=config('min-hours'))

    if not config('disable-ring-balance') and is_elected_leader(SWIFT_HA_RES):
        # Try ring balance. If rings are balanced, no sync will occur.
        balance_rings()

    for r_id in relation_ids('identity-service'):
        keystone_joined(relid=r_id)

    for r_id in relation_ids('cluster'):
        cluster_joined(relation_id=r_id)

    for r_id in relation_ids('object-store'):
        object_store_joined(relation_id=r_id)

    for r_id in relation_ids('amqp'):
        amqp_joined(relation_id=r_id)

    for r_id in relation_ids('ha'):
        ha_relation_joined(relation_id=r_id)

    try_initialize_swauth()


@hooks.hook('identity-service-relation-joined')
def keystone_joined(relid=None):
    port = config('bind-port')
    admin_url = '{}:{}'.format(canonical_url(CONFIGS, ADMIN), port)
    internal_url = ('{}:{}/v1/AUTH_$(tenant_id)s'
                    .format(canonical_url(CONFIGS, INTERNAL), port))
    public_url = ('{}:{}/v1/AUTH_$(tenant_id)s'
                  .format(canonical_url(CONFIGS, PUBLIC), port))
    region = config('region')

    s3_public_url = ('{}:{}'.format(canonical_url(CONFIGS, PUBLIC), port))
    s3_internal_url = ('{}:{}'.format(canonical_url(CONFIGS, INTERNAL), port))
    s3_admin_url = '{}:{}'.format(canonical_url(CONFIGS, ADMIN), port)

    relation_set(relation_id=relid,
                 region=None, public_url=None,
                 internal_url=None, admin_url=None, service=None,
                 swift_service='swift', swift_region=region,
                 swift_public_url=public_url,
                 swift_internal_url=internal_url,
                 swift_admin_url=admin_url,
                 s3_service='s3', s3_region=region,
                 s3_public_url=s3_public_url,
                 s3_admin_url=s3_admin_url,
                 s3_internal_url=s3_internal_url)


@hooks.hook('identity-service-relation-changed')
@restart_on_change(restart_map())
def keystone_changed():
    configure_https()


@hooks.hook('swift-storage-relation-joined')
def storage_joined(rid=None):
    if not is_elected_leader(SWIFT_HA_RES):
        log("New storage relation joined - stopping proxy until ring builder "
            "synced", level=INFO)
        service_stop('swift-proxy')

        # This unit is not currently responsible for distributing rings but
        # may become so at some time in the future so we do this to avoid the
        # possibility of storage nodes getting out-of-date rings by deprecating
        # any existing ones from the www dir.
        mark_www_rings_deleted()
        clear_storage_rings_available()

    try_initialize_swauth()

    # NOTE(jamespage): Ensure private-address is set to any network-space
    #                  binding provided by the charm user.
    relation_set(
        relation_id=rid,
        relation_settings={'private-address': get_relation_ip('swift-storage')}
    )


def get_host_ip(rid=None, unit=None):
    addr = relation_get('private-address', rid=rid, unit=unit)
    if config('prefer-ipv6'):
        host_ip = format_ipv6_addr(addr)
        if host_ip:
            return host_ip
        else:
            msg = ("Did not get IPv6 address from storage relation "
                   "(got={})".format(addr))
            log(msg, level=WARNING)

    return openstack.get_host_ip(addr)


def update_rsync_acls():
    """Get Host IP of each storage unit and broadcast acl to all units."""
    hosts = []

    if not is_elected_leader(SWIFT_HA_RES):
        log("Skipping rsync acl update since not leader", level=DEBUG)
        return

    # Get all unit addresses
    for rid in relation_ids('swift-storage'):
        for unit in related_units(rid):
            hosts.append(get_host_ip(rid=rid, unit=unit))

    rsync_hosts = ' '.join(hosts)
    log("Broadcasting acl '{}' to all storage units".format(rsync_hosts),
        level=DEBUG)
    # We add a timestamp so that the storage units know which is the newest
    settings = {'rsync_allowed_hosts': rsync_hosts,
                'timestamp': time.time()}
    for rid in relation_ids('swift-storage'):
        relation_set(relation_id=rid, **settings)


@hooks.hook('swift-storage-relation-changed')
@restart_on_change(restart_map())
def storage_changed():
    """Storage relation.

    Only the leader unit can update and distribute rings so if we are not the
    leader we ignore this event and wait for a resync request from the leader.
    """
    if not is_elected_leader(SWIFT_HA_RES):
        log("Not the leader - deferring storage relation change to leader "
            "unit.", level=DEBUG)
        return

    log("Storage relation changed -processing", level=DEBUG)
    host_ip = get_host_ip()
    if not host_ip:
        log("No host ip found in storage relation - deferring storage "
            "relation", level=WARNING)
        return

    update_rsync_acls()

    zone = get_zone(config('zone-assignment'))
    node_settings = {
        'ip': host_ip,
        'zone': zone,
        'account_port': relation_get('account_port'),
        'object_port': relation_get('object_port'),
        'container_port': relation_get('container_port'),
    }

    if None in node_settings.values():
        missing = [k for k, v in node_settings.items() if v is None]
        log("Relation not ready - some required values not provided by "
            "relation (missing={})".format(', '.join(missing)), level=INFO)
        return None

    for k in ['zone', 'account_port', 'object_port', 'container_port']:
        node_settings[k] = int(node_settings[k])

    CONFIGS.write_all()

    # Allow for multiple devs per unit, passed along as a : separated list
    # Update and balance rings.
    nodes = []
    devs = relation_get('device')
    if devs:
        for dev in devs.split(':'):
            node = {k: v for k, v in node_settings.items()}
            node['device'] = dev
            nodes.append(node)

    update_rings(nodes)
    if not openstack.is_unit_paused_set():
        # Restart proxy here in case no config changes made (so
        # restart_on_change() ineffective).
        service_restart('swift-proxy')


@hooks.hook('swift-storage-relation-broken')
@restart_on_change(restart_map())
def storage_broken():
    CONFIGS.write_all()


@hooks.hook('object-store-relation-joined')
def object_store_joined(relation_id=None):
    relation_data = {
        'swift-url':
        "{}:{}".format(canonical_url(CONFIGS, INTERNAL), config('bind-port'))
    }

    relation_set(relation_id=relation_id, **relation_data)


@hooks.hook('cluster-relation-joined')
def cluster_joined(relation_id=None):
    settings = {}

    for addr_type in ADDRESS_TYPES:
        address = get_relation_ip(
            addr_type,
            cidr_network=config('os-{}-network'.format(addr_type)))
        if address:
            settings['{}-address'.format(addr_type)] = address

    settings['private-address'] = get_relation_ip('cluster')

    relation_set(relation_id=relation_id, relation_settings=settings)


def is_all_peers_stopped(responses):
    """Establish whether all peers have stopped their proxy services.

    Each peer unit will set stop-proxy-service-ack to rq value to indicate that
    it has stopped its proxy service. We wait for all units to be stopped
    before triggering a sync. Peer services will be  restarted once their rings
    are synced with the leader.

    To be safe, default expectation is that api is still running.
    """
    rq_key = SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC
    ack_key = SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC_ACK
    token = relation_get(attribute=rq_key, unit=local_unit())
    if not token or token != responses[0].get(ack_key):
        log("Token mismatch, rq and ack tokens differ (expected ack={}, "
            "got={})".format(token, responses[0].get(ack_key)), level=DEBUG)
        return False

    if not all_responses_equal(responses, ack_key):
        log("Not all ack responses are equal. Either we are still waiting "
            "for responses or we were not the request originator.",
            level=DEBUG)
        return False

    return True


def cluster_leader_actions():
    """Cluster relation hook actions to be performed by leader units.

    NOTE: must be called by leader from cluster relation hook.
    """
    log("Cluster changed by unit={} (local is leader)".format(remote_unit()),
        level=DEBUG)

    rx_settings = relation_get() or {}
    tx_settings = relation_get(unit=local_unit()) or {}

    rx_rq_token = rx_settings.get(SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC)
    rx_ack_token = rx_settings.get(SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC_ACK)

    tx_rq_token = tx_settings.get(SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC)
    tx_ack_token = tx_settings.get(SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC_ACK)

    rx_leader_changed = \
        rx_settings.get(SwiftProxyClusterRPC.KEY_NOTIFY_LEADER_CHANGED)
    if rx_leader_changed:
        log("Leader change notification received and this is leader so "
            "retrying sync.", level=INFO)
        # FIXME: check that we were previously part of a successful sync to
        #        ensure we have good rings.
        cluster_sync_rings(peers_only=tx_settings.get('peers-only', False),
                           token=rx_leader_changed)
        return

    rx_resync_request = \
        rx_settings.get(SwiftProxyClusterRPC.KEY_REQUEST_RESYNC)
    resync_request_ack_key = SwiftProxyClusterRPC.KEY_REQUEST_RESYNC_ACK
    tx_resync_request_ack = tx_settings.get(resync_request_ack_key)
    if rx_resync_request and tx_resync_request_ack != rx_resync_request:
        log("Unit '{}' has requested a resync".format(remote_unit()),
            level=INFO)
        cluster_sync_rings(peers_only=True)
        relation_set(**{resync_request_ack_key: rx_resync_request})
        return

    # If we have received an ack token ensure it is not associated with a
    # request we received from another peer. If it is, this would indicate
    # a leadership change during a sync and this unit will abort the sync or
    # attempt to restore the original leader so to be able to complete the
    # sync.

    if rx_ack_token and rx_ack_token == tx_rq_token:
        # Find out if all peer units have been stopped.
        responses = []
        for rid in relation_ids('cluster'):
            for unit in related_units(rid):
                responses.append(relation_get(rid=rid, unit=unit))

        # Ensure all peers stopped before starting sync
        if is_all_peers_stopped(responses):
            key = 'peers-only'
            if not all_responses_equal(responses, key, must_exist=False):
                msg = ("Did not get equal response from every peer unit for "
                       "'{}'".format(key))
                raise SwiftProxyCharmException(msg)

            peers_only = bool(get_first_available_value(responses, key,
                                                        default=0))
            log("Syncing rings and builders (peers-only={})"
                .format(peers_only), level=DEBUG)
            broadcast_rings_available(broker_token=rx_ack_token,
                                      storage=not peers_only)
        else:
            key = SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC_ACK
            acks = ', '.join([rsp[key] for rsp in responses if key in rsp])
            log("Not all peer apis stopped - skipping sync until all peers "
                "ready (current='{}', token='{}')".format(acks, tx_ack_token),
                level=INFO)
    elif ((rx_ack_token and (rx_ack_token == tx_ack_token)) or
          (rx_rq_token and (rx_rq_token == rx_ack_token))):
        log("It appears that the cluster leader has changed mid-sync - "
            "stopping proxy service", level=WARNING)
        service_stop('swift-proxy')
        broker = rx_settings.get('builder-broker')
        if broker:
            # If we get here, manual intervention will be required in order
            # to restore the cluster.
            raise SwiftProxyCharmException(
                "Failed to restore previous broker '{}' as leader"
                .format(broker))
        else:
            raise SwiftProxyCharmException(
                "No builder-broker on rx_settings relation from '{}' - "
                "unable to attempt leader restore".format(remote_unit()))
    else:
        log("Not taking any sync actions", level=DEBUG)

    CONFIGS.write_all()


def cluster_non_leader_actions():
    """Cluster relation hook actions to be performed by non-leader units.

    NOTE: must be called by non-leader from cluster relation hook.
    """
    log("Cluster changed by unit={} (local is non-leader)"
        .format(remote_unit()), level=DEBUG)
    rx_settings = relation_get() or {}
    tx_settings = relation_get(unit=local_unit()) or {}

    token = rx_settings.get(SwiftProxyClusterRPC.KEY_NOTIFY_LEADER_CHANGED)
    if token:
        log("Leader-changed notification received from peer unit. Since "
            "this most likely occurred during a ring sync proxies will "
            "be disabled until the leader is restored and a fresh sync "
            "request is set out", level=WARNING)
        service_stop("swift-proxy")
        return

    rx_rq_token = rx_settings.get(SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC)

    # Check whether we have been requested to stop proxy service
    if rx_rq_token:
        log("Peer request to stop proxy service received ({}) - sending ack"
            .format(rx_rq_token), level=INFO)
        service_stop('swift-proxy')
        peers_only = rx_settings.get('peers-only', None)
        rq = SwiftProxyClusterRPC().stop_proxy_ack(echo_token=rx_rq_token,
                                                   echo_peers_only=peers_only)
        relation_set(relation_settings=rq)
        return

    # Check if there are any builder files we can sync from the leader.
    broker = rx_settings.get('builder-broker', None)
    broker_token = rx_settings.get('broker-token', None)
    broker_timestamp = rx_settings.get('broker-timestamp', None)
    tx_ack_token = tx_settings.get(SwiftProxyClusterRPC.KEY_STOP_PROXY_SVC_ACK)
    if not broker:
        log("No ring/builder update available", level=DEBUG)
        if not openstack.is_unit_paused_set():
            service_start('swift-proxy')

        return
    elif broker_token:
        if tx_ack_token:
            if broker_token == tx_ack_token:
                log("Broker and ACK tokens match ({})".format(broker_token),
                    level=DEBUG)
            else:
                log("Received ring/builder update notification but tokens do "
                    "not match (broker-token={}/ack-token={})"
                    .format(broker_token, tx_ack_token), level=WARNING)
                return
        else:
            log("Broker token available without handshake, assuming we just "
                "joined and rings won't change", level=DEBUG)
    else:
        log("Not taking any sync actions", level=DEBUG)
        return

    # If we upgrade from cluster that did not use timestamps, the new peer will
    # need to request a re-sync from the leader
    if not is_most_recent_timestamp(broker_timestamp):
        if not timestamps_available(excluded_unit=remote_unit()):
            log("Requesting resync")
            rq = SwiftProxyClusterRPC().request_resync(broker_token)
            relation_set(relation_settings=rq)
        else:
            log("Did not receive most recent broker timestamp but timestamps "
                "are available - waiting for next timestamp", level=INFO)

        return

    log("Ring/builder update available", level=DEBUG)
    builders_only = int(rx_settings.get('sync-only-builders', 0))
    path = os.path.basename(get_www_dir())
    try:
        sync_proxy_rings('http://{}/{}'.format(broker, path),
                         rings=not builders_only)
    except CalledProcessError:
        log("Ring builder sync failed, builders not yet available - "
            "leader not ready?", level=WARNING)
        return

    # Re-enable the proxy once all builders and rings are synced
    if fully_synced():
        log("Ring builders synced - starting proxy", level=INFO)
        CONFIGS.write_all()
        if not openstack.is_unit_paused_set():
            service_start('swift-proxy')
    else:
        log("Not all builders and rings synced yet - waiting for peer sync "
            "before starting proxy", level=INFO)


@hooks.hook('cluster-relation-changed')
@restart_on_change(restart_map())
def cluster_changed():
    if is_elected_leader(SWIFT_HA_RES):
        cluster_leader_actions()
    else:
        cluster_non_leader_actions()


@hooks.hook('ha-relation-changed')
@sync_builders_and_rings_if_changed
def ha_relation_changed():
    clustered = relation_get('clustered')
    if clustered:
        log("Cluster configured, notifying other services and updating "
            "keystone endpoint configuration", level=INFO)
        # Tell all related services to start using
        # the VIP instead
        for r_id in relation_ids('identity-service'):
            keystone_joined(relid=r_id)


@hooks.hook('ha-relation-joined')
def ha_relation_joined(relation_id=None):
    settings = generate_ha_relation_data('swift')
    relation_set(relation_id=relation_id, **settings)


def configure_https():
    """Enables SSL API Apache config if appropriate and kicks identity-service
    with any required api updates.
    """
    # need to write all to ensure changes to the entire request pipeline
    # propagate (c-api, haprxy, apache)
    CONFIGS.write_all()
    if 'https' in CONFIGS.complete_contexts():
        cmd = ['a2ensite', 'openstack_https_frontend']
        check_call(cmd)
    else:
        cmd = ['a2dissite', 'openstack_https_frontend']
        check_call(cmd)

    # Apache 2.4 required enablement of configuration
    if os.path.exists('/usr/sbin/a2enconf'):
        check_call(['a2enconf', 'swift-rings'])

    if not openstack.is_unit_paused_set():
        # TODO: improve this by checking if local CN certs are available
        # first then checking reload status (see LP #1433114).
        service_reload('apache2', restart_on_failure=True)

    for rid in relation_ids('identity-service'):
        keystone_joined(relid=rid)

    env_vars = {'OPENSTACK_SERVICE_SWIFT': 'proxy-server',
                'OPENSTACK_PORT_API': config('bind-port'),
                'OPENSTACK_PORT_MEMCACHED': 11211}
    openstack.save_script_rc(**env_vars)


@hooks.hook('nrpe-external-master-relation-joined',
            'nrpe-external-master-relation-changed')
def update_nrpe_config():
    # python-dbus is used by check_upstart_job
    apt_install('python-dbus')
    hostname = nrpe.get_nagios_hostname()
    current_unit = nrpe.get_nagios_unit_name()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    nrpe.copy_nrpe_checks()
    nrpe.add_init_service_checks(nrpe_setup, services(), current_unit)
    nrpe.add_haproxy_checks(nrpe_setup, current_unit)
    api_port = determine_api_port(config('bind-port'),
                                  singlenode_mode=True)
    nrpe_setup.add_check(
        shortname="swift-proxy-healthcheck",
        description="Check Swift Proxy Healthcheck",
        check_cmd="/usr/lib/nagios/plugins/check_http \
                  -I localhost -u /healthcheck -p {} \
                  -e \"OK\"".format(api_port)
    )
    nrpe_setup.write()


@hooks.hook('upgrade-charm')
@harden()
def upgrade_charm():
    rel = openstack.get_os_codename_install_source(config('openstack-origin'))
    pkgs = determine_packages(rel)
    new_packages = filter_installed_packages(pkgs)
    if new_packages:
        apt_install(new_packages)
    update_rsync_acls()


@hooks.hook('update-status')
@harden()
def update_status():
    log('Updating status.')


@hooks.hook('amqp-relation-joined')
def amqp_joined(relation_id=None):
    relation_set(relation_id=relation_id,
                 username=config('rabbit-user'),
                 vhost=config('rabbit-vhost'))


@hooks.hook('amqp-relation-changed',
            'amqp-relation-departed')
@restart_on_change(restart_map())
def amqp_changed():
    CONFIGS.write_all()


@hooks.hook('certificates-relation-joined')
def certs_joined(relation_id=None):
    relation_set(
        relation_id=relation_id,
        relation_settings=get_certificate_request())


@hooks.hook('certificates-relation-changed')
@restart_on_change(restart_map(), stopstart=True)
def certs_changed():
    process_certificates('swift', None, None)
    configure_https()


@hooks.hook('pre-series-upgrade')
def pre_series_upgrade():
    log("Running prepare series upgrade hook", "INFO")
    if not openstack.is_unit_paused_set():
        for service in services():
            stopped = service_stop(service)
            if not stopped:
                raise Exception("{} didn't stop cleanly.".format(service))
        openstack.set_unit_paused()
    openstack.set_unit_upgrading()


@hooks.hook('post-series-upgrade')
def post_series_upgrade():
    log("Running complete series upgrade hook", "INFO")
    openstack.clear_unit_paused()
    openstack.clear_unit_upgrading()
    if not openstack.is_unit_paused_set():
        for service in services():
            started = service_start(service)
            if not started:
                raise Exception("{} didn't start cleanly.".format(service))


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e), level=DEBUG)
    assess_status(CONFIGS)


if __name__ == '__main__':
    main()

import copy
from collections import OrderedDict
import functools
import glob
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from lib.swift_context import (
    get_swift_hash,
    SwiftHashContext,
    SwiftIdentityContext,
    HAProxyContext,
    SwiftRingContext,
    ApacheSSLContext,
    MemcachedContext,
    SwiftS3Context,
)

import charmhelpers.contrib.openstack.context as context
import charmhelpers.contrib.openstack.templating as templating
from charmhelpers.contrib.openstack.utils import (
    os_release,
    get_os_codename_package,
    get_os_codename_install_source,
    configure_installation_source,
    make_assess_status_func,
    os_application_version_set,
    CompareOpenStackReleases,
)
from charmhelpers.contrib.hahelpers.cluster import (
    is_elected_leader,
    peer_units,
    determine_api_port,
)
from charmhelpers.core.hookenv import (
    log,
    DEBUG,
    ERROR,
    INFO,
    WARNING,
    local_unit,
    relation_get,
    unit_get,
    relation_set,
    relation_ids,
    related_units,
    config,
    is_leader,
    leader_set,
    leader_get,
)
from charmhelpers.fetch import (
    apt_update,
    apt_upgrade,
    apt_install,
    add_source
)
from charmhelpers.core.host import (
    lsb_release,
    CompareHostReleases,
)
from charmhelpers.contrib.network.ip import (
    format_ipv6_addr,
    get_ipv6_addr,
    is_ipv6,
)
from charmhelpers.core.decorators import (
    retry_on_exception,
)


# Various config files that are managed via templating.
SWIFT_CONF_DIR = '/etc/swift'
SWIFT_RING_EXT = 'ring.gz'
SWIFT_CONF = os.path.join(SWIFT_CONF_DIR, 'swift.conf')
SWIFT_PROXY_CONF = os.path.join(SWIFT_CONF_DIR, 'proxy-server.conf')
MEMCACHED_CONF = '/etc/memcached.conf'
SWIFT_RINGS_CONF = '/etc/apache2/conf.d/swift-rings'
SWIFT_RINGS_24_CONF = '/etc/apache2/conf-available/swift-rings.conf'
HAPROXY_CONF = '/etc/haproxy/haproxy.cfg'
APACHE_SITES_AVAILABLE = '/etc/apache2/sites-available'
APACHE_SITE_CONF = os.path.join(APACHE_SITES_AVAILABLE,
                                'openstack_https_frontend')
APACHE_SITE_24_CONF = os.path.join(APACHE_SITES_AVAILABLE,
                                   'openstack_https_frontend.conf')

WWW_DIR = '/var/www/swift-rings'
ALTERNATE_WWW_DIR = '/var/www/html/swift-rings'

RING_SYNC_SEMAPHORE = threading.Semaphore()

VERSION_PACKAGE = 'swift-proxy'


def get_www_dir():
    if os.path.isdir(os.path.dirname(ALTERNATE_WWW_DIR)):
        return ALTERNATE_WWW_DIR
    else:
        return WWW_DIR


SWIFT_RINGS = OrderedDict((
    ('account', os.path.join(SWIFT_CONF_DIR, 'account.builder')),
    ('container', os.path.join(SWIFT_CONF_DIR, 'container.builder')),
    ('object', os.path.join(SWIFT_CONF_DIR, 'object.builder')),
))

SSL_CERT = os.path.join(SWIFT_CONF_DIR, 'cert.crt')
SSL_KEY = os.path.join(SWIFT_CONF_DIR, 'cert.key')

# Essex packages
BASE_PACKAGES = [
    'swift',
    'swift-proxy',
    'memcached',
    'apache2',
    'python-keystone',
]
# > Folsom specific packages
FOLSOM_PACKAGES = ['swift-plugin-s3', 'swauth']
# > Mitaka specific packages
MITAKA_PACKAGES = [
    'python-ceilometermiddleware',
    'python-keystonemiddleware',
]

SWIFT_HA_RES = 'grp_swift_vips'
TEMPLATES = 'templates/'

# Map config files to hook contexts and services that will be associated
# with file in restart_on_changes()'s service map.
CONFIG_FILES = OrderedDict([
    (SWIFT_CONF, {
        'hook_contexts': [SwiftHashContext()],
        'services': ['swift-proxy'],
    }),
    (SWIFT_PROXY_CONF, {
        'hook_contexts': [SwiftIdentityContext(),
                          SwiftS3Context(),
                          context.BindHostContext(),
                          context.AMQPContext(ssl_dir=SWIFT_CONF_DIR)],
        'services': ['swift-proxy'],
    }),
    (HAPROXY_CONF, {
        'hook_contexts': [context.HAProxyContext(singlenode_mode=True),
                          HAProxyContext()],
        'services': ['haproxy'],
    }),
    (SWIFT_RINGS_CONF, {
        'hook_contexts': [SwiftRingContext()],
        'services': ['apache2'],
    }),
    (SWIFT_RINGS_24_CONF, {
        'hook_contexts': [SwiftRingContext()],
        'services': ['apache2'],
    }),
    (APACHE_SITE_CONF, {
        'hook_contexts': [ApacheSSLContext()],
        'services': ['apache2'],
    }),
    (APACHE_SITE_24_CONF, {
        'hook_contexts': [ApacheSSLContext()],
        'services': ['apache2'],
    }),
    (MEMCACHED_CONF, {
        'hook_contexts': [MemcachedContext()],
        'services': ['memcached'],
    }),
])


class SwiftProxyCharmException(Exception):
    pass


class SwiftProxyClusterRPC(object):
    """Provides cluster relation rpc dicts.

    Crucially, this ensures that any settings we don't use in any given call
    are set to None, therefore removing them from the relation so they don't
    get accidentally interpreted by the receiver as part of the request.

    NOTE: these are only intended to be used from cluster peer relations.
    """

    KEY_STOP_PROXY_SVC = 'stop-proxy-service'
    KEY_STOP_PROXY_SVC_ACK = 'stop-proxy-service-ack'
    KEY_NOTIFY_LEADER_CHANGED = 'leader-changed-notification'
    KEY_REQUEST_RESYNC = 'resync-request'
    KEY_REQUEST_RESYNC_ACK = 'resync-request-ack'

    def __init__(self, version=1):
        self._version = version

    @property
    def _hostname(self):
        hostname = get_hostaddr()
        return format_ipv6_addr(hostname) or hostname

    def template(self):
        # Everything must be None by default so it gets dropped from the
        # relation unless we want it to be set.
        templates = {1: {'trigger': None,
                         'broker-token': None,
                         'builder-broker': None,
                         'broker-timestamp': None,
                         self.KEY_STOP_PROXY_SVC: None,
                         self.KEY_STOP_PROXY_SVC_ACK: None,
                         self.KEY_NOTIFY_LEADER_CHANGED: None,
                         self.KEY_REQUEST_RESYNC: None,
                         'peers-only': None,
                         'sync-only-builders': None}}
        return copy.deepcopy(templates[self._version])

    def stop_proxy_request(self, peers_only=False, token=None):
        """Request to stop peer proxy service.

        A token can optionally be supplied in case we want to restart a
        previously triggered sync e.g. following a leader change notification.

        NOTE: this action must only be performed by the cluster leader.

        :param peers_only: If True, indicates that we only want peer
                           (i.e. proxy not storage) units to be notified.
        :param token: optional request token expected to be echoed in ACK from
                      peer. If token not provided, a new one is generated.
        """
        if not is_elected_leader(SWIFT_HA_RES):
            errmsg = "Leader function called by non-leader"
            raise SwiftProxyCharmException(errmsg)

        rq = self.template()
        if not token:
            token = str(uuid.uuid4())

        rq['trigger'] = token
        rq[self.KEY_STOP_PROXY_SVC] = rq['trigger']
        if peers_only:
            rq['peers-only'] = 1

        rq['builder-broker'] = self._hostname
        return rq

    def stop_proxy_ack(self, echo_token, echo_peers_only):
        """Ack that peer proxy service is stopped.

        NOTE: this action must NOT be performed by the cluster leader.

        :param echo_peers_only: peers_only value from leader that we echo back.
        :param echo_token: request token received from leader that we must now
                           echo as an ACK.
        """
        rq = self.template()
        rq['trigger'] = str(uuid.uuid4())
        # These echo values should match those received in the request
        rq[self.KEY_STOP_PROXY_SVC_ACK] = echo_token
        rq['peers-only'] = echo_peers_only
        return rq

    def sync_rings_request(self, broker_token, broker_timestamp,
                           builders_only=False):
        """Request for peers to sync rings from leader.

        NOTE: this action must only be performed by the cluster leader.

        :param broker_token: token to identify sync request.
        :param broker_timestamp: timestamp for peer and storage sync - this
                                 MUST bethe same as the one used for storage
                                 sync.
        :param builders_only: if False, tell peers to sync builders only (not
                              rings).
        """
        if not is_elected_leader(SWIFT_HA_RES):
            errmsg = "Leader function called by non-leader"
            raise SwiftProxyCharmException(errmsg)

        rq = self.template()
        rq['trigger'] = str(uuid.uuid4())

        if builders_only:
            rq['sync-only-builders'] = 1

        rq['broker-token'] = broker_token
        rq['broker-timestamp'] = broker_timestamp
        rq['builder-broker'] = self._hostname
        return rq

    def notify_leader_changed(self, token):
        """Notify peers that leader has changed.

        The token passed in must be that associated with the sync we claim to
        have been interrupted. It will be re-used by the restored leader once
        it receives this notification.

        NOTE: this action must only be performed by the cluster leader that
              has relinquished it's leader status as part of the current hook
              context.
        """
        if not is_elected_leader(SWIFT_HA_RES):
            errmsg = "Leader function called by non-leader"
            raise SwiftProxyCharmException(errmsg)

        rq = self.template()
        rq['trigger'] = str(uuid.uuid4())
        rq[self.KEY_NOTIFY_LEADER_CHANGED] = token
        return rq

    def request_resync(self, token):
        """Request re-sync from leader.

        NOTE: this action must not be performed by the cluster leader.
        """
        rq = self.template()
        rq['trigger'] = str(uuid.uuid4())
        rq[self.KEY_REQUEST_RESYNC] = token
        return rq


def try_initialize_swauth():
    if is_leader() and config('auth-type') == 'swauth':
        if leader_get('swauth-init') is not True:
            try:
                admin_key = config('swauth-admin-key')
                if admin_key == '' or admin_key is None:
                    admin_key = leader_get('swauth-admin-key')
                    if admin_key is None:
                        admin_key = uuid.uuid4()
                leader_set({'swauth-admin-key': admin_key})

                bind_port = config('bind-port')
                bind_port = determine_api_port(bind_port, singlenode_mode=True)
                subprocess.check_call([
                    'swauth-prep',
                    '-A',
                    'http://localhost:{}/auth'.format(bind_port),
                    '-K', admin_key])
                leader_set({'swauth-init': True})
            except subprocess.CalledProcessError:
                log("had a problem initializing swauth!")


def get_first_available_value(responses, key, default=None):
    for r in responses:
        if key in r:
            return r[key]

    return default


def all_responses_equal(responses, key, must_exist=True):
    """If key exists in responses, all values for it must be equal.

    If all equal return True. If key does not exist and must_exist is True
    return False otherwise True.
    """
    sentinel = object()
    val = None
    all_equal = True
    for r in responses:
        _val = r.get(key, sentinel)
        if val is not None and val != _val:
            all_equal = False
            break
        elif _val != sentinel:
            val = _val

    if must_exist and val is None:
        all_equal = False

    if all_equal:
        return True

    log("Responses not all equal for key '{}'".format(key), level=DEBUG)
    return False


def register_configs():
    """Register config files with their respective contexts.

    Registration of some configs may not be required depending on
    existing of certain relations.
    """
    # if called without anything installed (eg during install hook)
    # just default to earliest supported release. configs dont get touched
    # till post-install, anyway.
    release = get_os_codename_package('swift-proxy', fatal=False) or 'essex'
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)

    confs = [SWIFT_CONF,
             SWIFT_PROXY_CONF,
             HAPROXY_CONF,
             MEMCACHED_CONF]

    for conf in confs:
        configs.register(conf, CONFIG_FILES[conf]['hook_contexts'])

    if os.path.exists('/etc/apache2/conf-available'):
        configs.register(SWIFT_RINGS_24_CONF,
                         CONFIG_FILES[SWIFT_RINGS_24_CONF]['hook_contexts'])
        configs.register(APACHE_SITE_24_CONF,
                         CONFIG_FILES[APACHE_SITE_24_CONF]['hook_contexts'])
    else:
        configs.register(SWIFT_RINGS_CONF,
                         CONFIG_FILES[SWIFT_RINGS_CONF]['hook_contexts'])
        configs.register(APACHE_SITE_CONF,
                         CONFIG_FILES[APACHE_SITE_CONF]['hook_contexts'])
    return configs


def restart_map():
    """Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    """
    _map = []
    for f, ctxt in CONFIG_FILES.items():
        svcs = []
        for svc in ctxt['services']:
            svcs.append(svc)
        if svcs:
            _map.append((f, svcs))

    return OrderedDict(_map)


def services():
    ''' Returns a list of services associate with this charm '''
    _services = []
    for v in restart_map().values():
        _services.extend(v)
    return list(set(_services))


def swift_user(username='swift'):
    user = pwd.getpwnam(username)
    return (user.pw_uid, user.pw_gid)


def ensure_swift_dir(conf_dir=os.path.dirname(SWIFT_CONF)):
    if not os.path.isdir(conf_dir):
        os.mkdir(conf_dir, 0o750)

    uid, gid = swift_user()
    os.chown(conf_dir, uid, gid)


def determine_packages(release):
    """Determine what packages are needed for a given OpenStack release."""
    cmp_openstack = CompareOpenStackReleases(release)
    pkgs = BASE_PACKAGES[:]
    if cmp_openstack >= 'folsom':
        pkgs += FOLSOM_PACKAGES
    if cmp_openstack >= 'mitaka':
        pkgs += MITAKA_PACKAGES
        pkgs.remove('python-keystone')
    if cmp_openstack >= 'rocky':
        pkgs.remove('swift-plugin-s3')
    return pkgs


def initialize_ring(path, part_power, replicas, min_hours):
    get_manager().initialize_ring(path, part_power, replicas, min_hours)


def exists_in_ring(ring_path, node):
    node['port'] = _ring_port(ring_path, node)
    result = get_manager().exists_in_ring(ring_path, node)
    if result:
        log('Node already exists in ring ({}).'
            .format(ring_path), level=INFO)
    return result


def add_to_ring(ring_path, node):
    port = _ring_port(ring_path, node)

    # Note: this code used to attempt to calculate new dev ids, but made
    # various assumptions (e.g. in order devices, all devices in the ring
    # being valid, etc). The RingBuilder from the Swift library will
    # automatically calculate the new device's ID if no id is provided (at
    # least since the Icehouse release).
    new_dev = {
        'zone': node['zone'],
        'ip': node['ip'],
        'port': port,
        'device': node['device'],
        'weight': 100,
        'meta': '',
    }
    get_manager().add_dev(ring_path, new_dev)
    msg = 'Added new device to ring {}: {}'.format(ring_path, new_dev)
    log(msg, level=INFO)


def _ring_port(ring_path, node):
    """Determine correct port from relation settings for a given ring file."""
    for name in ['account', 'object', 'container']:
        if name in ring_path:
            return node[('{}_port'.format(name))]


def get_zone(assignment_policy):
    """Determine appropriate zone based on configured assignment policy.

    Manual assignment relies on each storage zone being deployed as a
    separate service unit with its desired zone set as a configuration
    option.

    Auto assignment distributes swift-storage machine units across a number
    of zones equal to the configured minimum replicas.  This allows for a
    single swift-storage service unit, with each 'add-unit'd machine unit
    being assigned to a different zone.

    :param assignment_policy: <string> the policy
    :returns: <integer> zone id
    """
    if assignment_policy == 'manual':
        return relation_get('zone')
    elif assignment_policy == 'auto':
        _manager = get_manager()
        potential_zones = [_manager.get_zone(ring_path)
                           for ring_path in SWIFT_RINGS.values()]
        return set(potential_zones).pop()
    else:
        raise SwiftProxyCharmException(
            'Invalid zone assignment policy: {}'.format(assignment_policy))


def balance_ring(ring_path):
    """Balance a ring.

    Returns True if it needs redistribution.
    """
    # shell out to swift-ring-builder instead, since the balancing code there
    # does a bunch of un-importable validation.
    cmd = ['swift-ring-builder', ring_path, 'rebalance']
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:
            # Ring builder exit-code=1 is supposed to indicate warning but I
            # have noticed that it can also return 1 with the following sort of
            # message:
            #
            #   NOTE: Balance of 166.67 indicates you should push this ring,
            #         wait at least 0 hours, and rebalance/repush.
            #
            # This indicates that a balance has occurred and a resync would be
            # required so not sure why 1 is returned in this case.
            return False

        raise SwiftProxyCharmException(
            'balance_ring: {} returned {}'.format(cmd, e.returncode))

    # return True if it needs redistribution
    return True


def should_balance(rings):
    """Determine whether or not a re-balance is required and allowed.

    Ring balance can be disabled/postponed using the disable-ring-balance
    config option.

    Otherwise, using zones vs min. replicas, determine whether or not the rings
    should be balanced.
    """
    if config('disable-ring-balance'):
        return False

    return has_minimum_zones(rings)


def do_openstack_upgrade(configs):
    new_src = config('openstack-origin')
    new_os_rel = get_os_codename_install_source(new_src)

    log('Performing OpenStack upgrade to {}.'.format(new_os_rel), level=DEBUG)
    configure_installation_source(new_src)
    dpkg_opts = [
        '--option', 'Dpkg::Options::=--force-confnew',
        '--option', 'Dpkg::Options::=--force-confdef',
    ]
    apt_update()
    apt_upgrade(options=dpkg_opts, fatal=True, dist=True)
    configs.set_release(openstack_release=new_os_rel)
    configs.write_all()


def setup_ipv6():
    """Validate that we can support IPv6 mode.

    This should be called if prefer-ipv6 is True to ensure that we are running
    in an environment that supports ipv6.
    """
    ubuntu_rel = lsb_release()['DISTRIB_CODENAME'].lower()
    if CompareHostReleases(ubuntu_rel) < "trusty":
        msg = ("IPv6 is not supported in the charms for Ubuntu versions less "
               "than Trusty 14.04")
        raise SwiftProxyCharmException(msg)

    # Need haproxy >= 1.5.3 for ipv6 so for Trusty if we are <= Kilo we need to
    # use trusty-backports otherwise we can use the UCA.
    if (ubuntu_rel == 'trusty' and
            CompareOpenStackReleases(os_release('swift-proxy')) < 'liberty'):
        add_source('deb http://archive.ubuntu.com/ubuntu trusty-backports '
                   'main')
        apt_update()
        apt_install('haproxy/trusty-backports', fatal=True)


@retry_on_exception(3, base_delay=2, exc_type=subprocess.CalledProcessError)
def sync_proxy_rings(broker_url, builders=True, rings=True):
    """The leader proxy is responsible for intialising, updating and
    rebalancing the ring. Once the leader is ready the rings must then be
    synced into each other proxy unit.

    Note that we sync the ring builder and .gz files since the builder itself
    is linked to the underlying .gz ring.
    """
    log('Fetching swift rings & builders from proxy @ {}.'.format(broker_url),
        level=DEBUG)
    target = SWIFT_CONF_DIR
    synced = []
    tmpdir = tempfile.mkdtemp(prefix='swiftrings')
    try:
        for server in ['account', 'object', 'container']:
            if builders:
                url = '{}/{}.builder'.format(broker_url, server)
                log('Fetching {}.'.format(url), level=DEBUG)
                builder = "{}.builder".format(server)
                cmd = ['wget', url, '--retry-connrefused', '-t', '10', '-O',
                       os.path.join(tmpdir, builder)]
                subprocess.check_call(cmd)
                synced.append(builder)

            if rings:
                url = '{}/{}.{}'.format(broker_url, server, SWIFT_RING_EXT)
                log('Fetching {}.'.format(url), level=DEBUG)
                ring = '{}.{}'.format(server, SWIFT_RING_EXT)
                cmd = ['wget', url, '--retry-connrefused', '-t', '10', '-O',
                       os.path.join(tmpdir, ring)]
                subprocess.check_call(cmd)
                synced.append(ring)

        # Once all have been successfully downloaded, move them to actual
        # location.
        for f in synced:
            os.rename(os.path.join(tmpdir, f), os.path.join(target, f))
    finally:
        shutil.rmtree(tmpdir)


def ensure_www_dir_permissions(www_dir):
    if not os.path.isdir(www_dir):
        os.mkdir(www_dir, 0o755)
    else:
        os.chmod(www_dir, 0o755)

    uid, gid = swift_user()
    os.chown(www_dir, uid, gid)


def update_www_rings(rings=True, builders=True):
    """Copy rings to apache www dir.

    Try to do this as atomically as possible to avoid races with storage nodes
    syncing rings.
    """
    if not (rings or builders):
        return

    tmp_dir = tempfile.mkdtemp(prefix='swift-rings-www-tmp')
    for ring, builder_path in SWIFT_RINGS.items():
        if rings:
            ringfile = '{}.{}'.format(ring, SWIFT_RING_EXT)
            src = os.path.join(SWIFT_CONF_DIR, ringfile)
            dst = os.path.join(tmp_dir, ringfile)
            shutil.copyfile(src, dst)

        if builders:
            src = builder_path
            dst = os.path.join(tmp_dir, os.path.basename(builder_path))
            shutil.copyfile(src, dst)

    www_dir = get_www_dir()
    deleted = "{}.deleted".format(www_dir)
    ensure_www_dir_permissions(tmp_dir)
    os.rename(www_dir, deleted)
    os.rename(tmp_dir, www_dir)
    shutil.rmtree(deleted)


def get_rings_checksum():
    """Returns sha256 checksum for rings in /etc/swift."""
    sha = hashlib.sha256()
    for ring in SWIFT_RINGS.keys():
        path = os.path.join(SWIFT_CONF_DIR, '{}.{}'
                            .format(ring, SWIFT_RING_EXT))
        if not os.path.isfile(path):
            continue

        with open(path, 'rb') as fd:
            sha.update(fd.read())

    return sha.hexdigest()


def get_builders_checksum():
    """Returns sha256 checksum for builders in /etc/swift."""
    sha = hashlib.sha256()
    for builder in SWIFT_RINGS.values():
        if not os.path.exists(builder):
            continue

        with open(builder, 'rb') as fd:
            sha.update(fd.read())

    return sha.hexdigest()


def non_null_unique(data):
    """Return True if data is a list containing all non-null values that are
    all equal.
    """
    return (all(data) and len(set(data)) > 1)


def previously_synced():
    """If a full sync is not known to have been performed from this unit, False
    is returned."""
    broker = None
    for rid in relation_ids('cluster'):
        broker = relation_get(attribute='builder-broker', rid=rid,
                              unit=local_unit())
        only_builders_synced = relation_get(attribute='sync-only-builders',
                                            rid=rid, unit=local_unit())

    if broker and not only_builders_synced:
        return True

    return False


def sync_builders_and_rings_if_changed(f):
    """Only trigger a ring or builder sync if they have changed as a result of
    the decorated operation.
    """
    @functools.wraps(f)
    def _inner_sync_builders_and_rings_if_changed(*args, **kwargs):
        if not is_elected_leader(SWIFT_HA_RES):
            log("Sync rings called by non-leader - skipping", level=WARNING)
            return

        try:
            # Ensure we don't do a double sync if we are nested.
            do_sync = False
            if RING_SYNC_SEMAPHORE.acquire(blocking=0):
                do_sync = True
                rings_before = get_rings_checksum()
                builders_before = get_builders_checksum()

            ret = f(*args, **kwargs)

            if not do_sync:
                return ret

            rings_after = get_rings_checksum()
            builders_after = get_builders_checksum()

            rings_path = os.path.join(SWIFT_CONF_DIR, '*.{}'
                                      .format(SWIFT_RING_EXT))
            rings_ready = len(glob.glob(rings_path)) == len(SWIFT_RINGS)
            rings_changed = ((rings_after != rings_before) or
                             not previously_synced())
            builders_changed = builders_after != builders_before
            if rings_changed or builders_changed:
                # Copy builders and rings (if available) to the server dir.
                update_www_rings(rings=rings_ready)
                if rings_changed and rings_ready:
                    # Trigger sync
                    cluster_sync_rings(peers_only=not rings_changed)
                else:
                    log("Rings not ready for sync - syncing builders",
                        level=DEBUG)
                    cluster_sync_rings(peers_only=True, builders_only=True)
            else:
                log("Rings/builders unchanged - skipping sync", level=DEBUG)

            return ret
        finally:
            RING_SYNC_SEMAPHORE.release()

    return _inner_sync_builders_and_rings_if_changed


@sync_builders_and_rings_if_changed
def update_rings(nodes=None, min_part_hours=None):
    """Update builder with node settings and balance rings if necessary.

    Also update min_part_hours if provided.
    """
    if not is_elected_leader(SWIFT_HA_RES):
        log("Update rings called by non-leader - skipping", level=WARNING)
        return

    balance_required = False

    if min_part_hours is not None:
        # NOTE: no need to stop the proxy since we are not changing the rings,
        # only the builder.

        # Only update if all exist
        if all(os.path.exists(p) for p in SWIFT_RINGS.values()):
            for ring, path in SWIFT_RINGS.items():
                current_min_part_hours = get_min_part_hours(path)
                if min_part_hours != current_min_part_hours:
                    log("Setting ring {} min_part_hours to {}"
                        .format(ring, min_part_hours), level=INFO)
                    try:
                        set_min_part_hours(path, min_part_hours)
                    except SwiftProxyCharmException as exc:
                        # TODO: ignore for now since this should not be
                        # critical but in the future we should support a
                        # rollback.
                        log(str(exc), level=WARNING)
                    else:
                        balance_required = True

    if nodes is not None:
        for node in nodes:
            for ring in SWIFT_RINGS.values():
                if not exists_in_ring(ring, node):
                    add_to_ring(ring, node)
                    balance_required = True

    if balance_required:
        balance_rings()


def get_min_part_hours(path):
    """Just a proxy to the manager.py:get_min_part_hours() function

    :param path: the path to get the min_part_hours for
    :returns: integer
    """
    return get_manager().get_min_part_hours(path)


def set_min_part_hours(path, value):
    cmd = ['swift-ring-builder', path, 'set_min_part_hours', str(value)]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        raise SwiftProxyCharmException(
            "Failed to set min_part_hours={} on {}".format(value, path))


@sync_builders_and_rings_if_changed
def balance_rings():
    """Rebalance each ring and notify peers that new rings are available."""
    if not is_elected_leader(SWIFT_HA_RES):
        log("Balance rings called by non-leader - skipping", level=WARNING)
        return

    if not should_balance([r for r in SWIFT_RINGS.values()]):
        log("Not yet ready to balance rings - insufficient replicas?",
            level=INFO)
        return

    rebalanced = False
    log("Rebalancing rings", level=INFO)
    for path in SWIFT_RINGS.values():
        if balance_ring(path):
            log('Balanced ring {}'.format(path), level=DEBUG)
            rebalanced = True
        else:
            log('Ring {} not rebalanced'.format(path), level=DEBUG)

    if not rebalanced:
        log("Rings unchanged by rebalance", level=DEBUG)
        # NOTE: checksum will tell for sure


def mark_www_rings_deleted():
    """Mark any rings from the apache server directory as deleted so that
    storage units won't see them.
    """
    www_dir = get_www_dir()
    for ring in SWIFT_RINGS.keys():
        path = os.path.join(www_dir, '{}.ring.gz'.format(ring))
        if os.path.exists(path):
            os.rename(path, "{}.deleted".format(path))


def notify_peers_builders_available(broker_token, broker_timestamp,
                                    builders_only=False):
    """Notify peer swift-proxy units that they should synchronise ring and
    builder files.

    Note that this should only be called from the leader unit.

    @param broker_timestamp: timestamp for peer and storage sync - this MUST be
    the same as the one used for storage sync.
    """
    if not is_elected_leader(SWIFT_HA_RES):
        log("Ring availability peer broadcast requested by non-leader - "
            "skipping", level=WARNING)
        return

    if not broker_token:
        log("No broker token - aborting sync", level=WARNING)
        return

    cluster_rids = relation_ids('cluster')
    if not cluster_rids:
        log("Cluster relation not yet available - skipping sync", level=DEBUG)
        return

    if builders_only:
        _type = "builders"
    else:
        _type = "builders & rings"

    # Notify peers that builders are available
    log("Notifying peer(s) that {} are ready for sync."
        .format(_type), level=INFO)
    rq = SwiftProxyClusterRPC().sync_rings_request(broker_token,
                                                   broker_timestamp,
                                                   builders_only=builders_only)
    for rid in cluster_rids:
        log("Notifying rid={} ({})".format(rid, rq), level=DEBUG)
        relation_set(relation_id=rid, relation_settings=rq)


def broadcast_rings_available(storage=True, builders_only=False,
                              broker_token=None):
    """Notify storage relations and cluster (peer) relations that rings and
    builders are availble for sync.

    We can opt to only notify peer or storage relations if needs be.
    """

    # NOTE: this MUST be the same for peer and storage sync
    broker_timestamp = "{:f}".format(time.time())

    if storage:
        # TODO: get ack from storage units that they are synced before
        # syncing proxies.
        notify_storage_rings_available(broker_timestamp)
    else:
        log("Skipping notify storage relations", level=DEBUG)

    # Always set peer info even if not clustered so that info is present when
    # units join
    notify_peers_builders_available(broker_token,
                                    broker_timestamp,
                                    builders_only=builders_only)


def cluster_sync_rings(peers_only=False, builders_only=False, token=None):
    """Notify peer relations that they should stop their proxy services.

    Peer units will then be expected to do a relation_set with
    stop-proxy-service-ack set rq value. Once all peers have responded, the
    leader will send out notification to all relations that rings are available
    for sync.

    If peers_only is True, only peer units will be synced. This is typically
    used when only builder files have been changed.

    This should only be called by the leader unit.
    """
    if not is_elected_leader(SWIFT_HA_RES):
        # Only the leader can do this.
        return

    if not peer_units():
        # If we have no peer units just go ahead and broadcast to storage
        # relations. If we have been instructed to only broadcast to peers this
        # should do nothing.
        broadcast_rings_available(broker_token=str(uuid.uuid4()),
                                  storage=not peers_only)
        return
    elif builders_only:
        if not token:
            token = str(uuid.uuid4())

        # No need to stop proxies if only syncing builders between peers.
        broadcast_rings_available(storage=False, builders_only=True,
                                  broker_token=token)
        return

    log("Sending stop proxy service request to all peers", level=INFO)
    rq = SwiftProxyClusterRPC().stop_proxy_request(peers_only, token=token)
    for rid in relation_ids('cluster'):
        relation_set(relation_id=rid, relation_settings=rq)


def notify_storage_rings_available(broker_timestamp):
    """Notify peer swift-storage relations that they should synchronise ring
    and builder files.

    Note that this should only be called from the leader unit.

    @param broker_timestamp: timestamp for peer and storage sync - this MUST be
                             the same as the one used for peer sync.
    """
    if not is_elected_leader(SWIFT_HA_RES):
        log("Ring availability storage-relation broadcast requested by "
            "non-leader - skipping", level=WARNING)
        return

    hostname = get_hostaddr()
    hostname = format_ipv6_addr(hostname) or hostname
    path = os.path.basename(get_www_dir())
    rings_url = 'http://{}/{}'.format(hostname, path)

    # TODO(hopem): consider getting rid of this trigger since the timestamp
    #              should do the job.
    trigger = uuid.uuid4()

    # Notify storage nodes that there is a new ring to fetch.
    log("Notifying storage nodes that new rings are ready for sync.",
        level=INFO)
    for relid in relation_ids('swift-storage'):
        relation_set(relation_id=relid, swift_hash=get_swift_hash(),
                     rings_url=rings_url, broker_timestamp=broker_timestamp,
                     trigger=trigger)


def clear_storage_rings_available():
    """Clear the rings_url setting so that storage units don't accidentally try
    to sync from this unit (which is presumably no longer the leader).
    """
    for relid in relation_ids('swift-storage'):
        relation_set(relation_id=relid, rings_url=None)


def fully_synced():
    """Check that we have all the rings and builders synced from the leader.

    Returns True if we have all rings and builders.
    """
    not_synced = []
    for ring, builder in SWIFT_RINGS.items():
        if not os.path.exists(builder):
            not_synced.append(builder)

        ringfile = os.path.join(SWIFT_CONF_DIR,
                                '{}.{}'.format(ring, SWIFT_RING_EXT))
        if not os.path.exists(ringfile):
            not_synced.append(ringfile)

    if not_synced:
        log("Not yet synced: {}".format(', '.join(not_synced), level=INFO))
        return False

    return True


def get_hostaddr():
    if config('prefer-ipv6'):
        return get_ipv6_addr(exc_list=[config('vip')])[0]

    return unit_get('private-address')


def is_most_recent_timestamp(timestamp):
    ts = []
    for rid in relation_ids('cluster'):
        for unit in related_units(rid):
            settings = relation_get(rid=rid, unit=unit)
            t = settings.get('broker-timestamp')
            if t:
                ts.append(t)

    if not ts or not timestamp:
        return False

    return timestamp >= max(ts)


def timestamps_available(excluded_unit):
    for rid in relation_ids('cluster'):
        for unit in related_units(rid):
            if unit == excluded_unit:
                continue

            settings = relation_get(rid=rid, unit=unit)
            if settings.get('broker-timestamp'):
                return True

    return False


def get_manager():
    return ManagerProxy()


class ManagerProxy(object):

    def __init__(self, path=None):
        self._path = path or []

    def __getattribute__(self, attr):
        if attr in ['__class__', '_path', 'api_version']:
            return super().__getattribute__(attr)
        return self.__class__(path=self._path + [attr])

    def __call__(self, *args, **kwargs):
        # Following line retained commented-out for future debugging
        # print("Called: {} ({}, {})".format(self._path, args, kwargs))
        return _proxy_manager_call(self._path, args, kwargs)


JSON_ENCODE_OPTIONS = dict(
    sort_keys=True,
    allow_nan=False,
    indent=None,
    separators=(',', ':'),
)


def _proxy_manager_call(path, args, kwargs):
    package = dict(path=path,
                   args=args,
                   kwargs=kwargs)
    serialized = json.dumps(package, **JSON_ENCODE_OPTIONS)
    script = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                          '..',
                                          'swift_manager',
                                          'manager.py'))
    env = os.environ
    try:
        if sys.version_info < (3, 5):
            # remove this after trusty support is removed.  No subprocess.run
            # in Python 3.4
            process = subprocess.Popen([script, serialized],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       env=env)
            out, err = process.communicate()
            result = json.loads(out.decode('UTF-8'))
        else:
            completed = subprocess.run([script, serialized],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       env=env)
            result = json.loads(completed.stdout.decode('UTF-8'))
        if 'error' in result:
            s = ("The call within manager.py failed with the error: '{}'. "
                 "The call was: path={}, args={}, kwargs={}"
                 .format(result['error'], path, args, kwargs))
            log(s, level=ERROR)
            raise RuntimeError(s)
        return result['result']
    except subprocess.CalledProcessError as e:
        s = ("manger.py failed when called with path={}, args={}, kwargs={},"
             " with the error: {}".format(path, args, kwargs, str(e)))
        log(s, level=ERROR)
        if sys.version_info < (3, 5):
            # remove this after trusty support is removed.
            log("stderr was:\n{}\n".format(err.decode('UTF-8')),
                level=ERROR)
        else:
            log("stderr was:\n{}\n".format(completed.stderr.decode('UTF-8')),
                level=ERROR)
        raise RuntimeError(s)
    except Exception as e:
        s = ("Decoding the result from the call to manager.py resulted in "
             "error '{}' (command: path={}, args={}, kwargs={}"
             .format(str(e), path, args, kwargs))
        log(s, level=ERROR)
        raise RuntimeError(s)


def customer_check_assess_status(configs):
    """Custom check function provided to assess_status() to check the current
    status of current unit beyond checking that the relevant services are
    running

    @param configs: An OSConfigRenderer object of the current services
    @returns (status, message): The outcome of the checks.
    """
    # Check for required swift-storage relation
    if len(relation_ids('swift-storage')) < 1:
        return ('blocked', 'Missing relation: storage')

    # Verify allowed_hosts is populated with enough unit IP addresses
    ctxt = SwiftRingContext()()
    replicas = [config('replicas'),
                config('replicas-account'),
                config('replicas-container')]
    # Remove 'None' object types from the list
    replicas = [r for r in replicas if r is not None]
    if len(ctxt['allowed_hosts']) < max(replicas):
        return ('blocked', 'Not enough related storage nodes')

    # Verify there are enough storage zones to satisfy minimum replicas
    rings = [r for r in SWIFT_RINGS.values()]
    if not has_minimum_zones(rings):
        return ('blocked', 'Not enough storage zones for minimum replicas')

    if config('prefer-ipv6'):
        for rid in relation_ids('swift-storage'):
            for unit in related_units(rid):
                addr = relation_get(attribute='private-address', unit=unit,
                                    rid=rid)
                if not is_ipv6(addr):
                    return ('blocked',
                            'Did not get IPv6 address from '
                            'storage relation (got={})'.format(addr))

    return 'active', 'Unit is ready'


def has_minimum_zones(rings):
    """Determine if enough zones exist to satisfy minimum replicas

    Uses manager.py as it accesses the ring_builder object in swift

    :param rings: the list of ring_paths to check
    :returns: Boolean
    """
    result = get_manager().has_minimum_zones(rings)
    if 'log' in result:
        log(result['log'], level=result['level'])
    return result['result']


def assess_status(configs, check_services=None):
    """Assess status of current unit
    Decides what the state of the unit should be based on the current
    configuration.
    SIDE EFFECT: calls set_os_workload_status(...) which sets the workload
    status of the unit.
    Also calls status_set(...) directly if paused state isn't complete.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    assess_status_func(configs, check_services)()
    os_application_version_set(VERSION_PACKAGE)


def assess_status_func(configs, check_services=None):
    """Helper function to create the function that will assess_status() for
    the unit.
    Uses charmhelpers.contrib.openstack.utils.make_assess_status_func() to
    create the appropriate status function and then returns it.
    Used directly by assess_status() and also for pausing and resuming
    the unit.

    NOTE(ajkavanagh) ports are not checked due to race hazards with services
    that don't behave sychronously w.r.t their service scripts.  e.g.
    apache2.
    @param configs: a templating.OSConfigRenderer() object
    @param check_services: a list of services to check.  If None, then
    services() is used instead.  To not check services pass an empty list.
    @return f() -> None : a function that assesses the unit's workload status
    """
    if check_services is None:
        check_services = services()
    required_interfaces = {}
    if relation_ids('identity-service'):
        required_interfaces['identity'] = ['identity-service']
    return make_assess_status_func(
        configs, required_interfaces,
        charm_func=customer_check_assess_status,
        services=check_services, ports=None)


def determine_replicas(ring):
    """ Determine replicas (lp1823696)

    Determine replicas based on 'replicas', 'replicas-account' and
    'replicas-container' options.

    :param ring: ring name
    :type ring: str
    :returns: replicas for the ring
    """
    if ring == 'account':
        if config('replicas-account'):
            return config('replicas-account')
        else:
            return config('replicas')
    elif ring == 'container':
        if config('replicas-container'):
            return config('replicas-container')
        else:
            return config('replicas')
    else:
        return config('replicas')

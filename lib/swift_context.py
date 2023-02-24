import os
import re
import uuid

from charmhelpers.core.hookenv import (
    config,
    log,
    relation_ids,
    related_units,
    relation_get,
    unit_get,
    service_name,
    leader_get,
    DEBUG,
    status_set,
)
from charmhelpers.contrib.openstack.context import (
    OSContextGenerator,
    ApacheSSLContext as SSLContext,
    IdentityServiceContext,
)
from charmhelpers.contrib.hahelpers.cluster import (
    determine_api_port,
    determine_apache_port,
    https,
)
from charmhelpers.contrib.network.ip import (
    format_ipv6_addr,
    get_ipv6_addr,
)
from charmhelpers.contrib.openstack.utils import (
    os_release,
    CompareOpenStackReleases,
    get_host_ip,
)


SWIFT_HASH_FILE = '/var/lib/juju/swift-hash-path.conf'
WWW_DIR = '/var/www/swift-rings'


class SwiftProxyCharmException(Exception):
    pass


class HAProxyContext(OSContextGenerator):
    interfaces = ['cluster']

    def __call__(self):
        """Extends the main charmhelpers HAProxyContext with a port mapping
        specific to this charm.
        Also used to extend cinder.conf context with correct api_listening_port
        """
        haproxy_port = config('bind-port')
        api_port = determine_apache_port(config('bind-port'),
                                         singlenode_mode=True)

        healthcheck = [{
            'option': 'httpchk GET /healthcheck',
            'http-check': 'expect status 200',
        }]

        backend_options = {'swift_api': healthcheck}

        ctxt = {
            'service_ports': {'swift_api': [haproxy_port, api_port]},
            'backend_options': backend_options,
            'https': https()
        }
        return ctxt


class ApacheSSLContext(SSLContext):
    interfaces = ['https']
    service_namespace = 'swift'

    # We make this a property so that we avoid import-time
    # dependencies on config()

    @property
    def external_ports(self):
        return [config('bind-port')]


class SwiftRingContext(OSContextGenerator):

    def __call__(self):
        allowed_hosts = []
        for relid in relation_ids('swift-storage'):
            for unit in related_units(relid):
                host = relation_get('private-address', unit, relid)
                if config('prefer-ipv6'):
                    host_ip = get_ipv6_addr(exc_list=[config('vip')])[0]
                else:
                    host_ip = get_host_ip(host)

                allowed_hosts.append(host_ip)

        ctxt = {
            'www_dir': WWW_DIR,
            'allowed_hosts': allowed_hosts
        }
        return ctxt


class SwiftS3Context(OSContextGenerator):

    def __call__(self):
        ctxt = {}
        if CompareOpenStackReleases(os_release('swift-common')) >= 'queens':
            if config('region'):
                ctxt['location'] = config('region')
            else:
                log("Region config not provided so location cannot be set - "
                    "required from S3 Sig V4 support to work correctly", DEBUG)

        return ctxt


class SwiftIdentityContext(OSContextGenerator):
    interfaces = ['identity-service']

    def __call__(self):
        bind_port = config('bind-port')
        workers = config('workers')
        if workers == 0:
            import multiprocessing
            workers = multiprocessing.cpu_count()
        if config('prefer-ipv6'):
            proxy_ip = ('[{}]'
                        .format(get_ipv6_addr(exc_list=[config('vip')])[0]))
            memcached_ip = 'ip6-localhost'
        else:
            proxy_ip = get_host_ip(unit_get('private-address'))
            memcached_ip = get_host_ip(unit_get('private-address'))

        ctxt = {
            'proxy_ip': proxy_ip,
            'memcached_ip': memcached_ip,
            'bind_port': determine_api_port(bind_port, singlenode_mode=True),
            'workers': workers,
            'operator_roles': config('operator-roles'),
            'delay_auth_decision': config('delay-auth-decision'),
            'node_timeout': config('node-timeout'),
            'recoverable_node_timeout': config('recoverable-node-timeout'),
            'log_headers': config('log-headers'),
            'statsd_host': config('statsd-host'),
            'statsd_port': config('statsd-port'),
            'statsd_sample_rate': config('statsd-sample-rate'),
            'static_large_object_segments': config(
                'static-large-object-segments'),
            'enable_multi_region': config('enable-multi-region'),
            'read_affinity': get_read_affinity(),
            'write_affinity': get_write_affinity(),
            'write_affinity_node_count': get_write_affinity_node_count()
        }

        cmp_openstack = CompareOpenStackReleases(os_release('swift'))
        if cmp_openstack < 'train':
            # swauth is no longer supported for OpenStack Train and later
            admin_key = leader_get('swauth-admin-key')
            if admin_key is not None:
                ctxt['swauth_admin_key'] = admin_key

        if config('debug'):
            ctxt['log_level'] = 'DEBUG'
        else:
            ctxt['log_level'] = 'INFO'

        # Instead of duplicating code lets use charm-helpers to set signing_dir
        # TODO(hopem): refactor this context handler to use charm-helpers
        #              code.
        _ctxt = IdentityServiceContext(service='swift', service_user='swift')()
        signing_dir = _ctxt.get('signing_dir')
        if signing_dir:
            ctxt['signing_dir'] = signing_dir

        ctxt['ssl'] = False

        auth_type = config('auth-type')
        ctxt['auth_type'] = auth_type

        auth_host = config('keystone-auth-host')
        admin_user = config('keystone-admin-user')
        admin_password = config('keystone-admin-user')
        if (auth_type == 'keystone' and auth_host and
                admin_user and admin_password):
            log('Using user-specified Keystone configuration.')
            ks_auth = {
                'auth_type': 'keystone',
                'auth_protocol': config('keystone-auth-protocol'),
                'keystone_host': auth_host,
                'auth_port': config('keystone-auth-port'),
                'service_user': admin_user,
                'service_password': admin_password,
                'service_tenant': config('keystone-admin-tenant-name'),
            }
            ctxt.update(ks_auth)

        # Sometime during the 20.08 development cycle, keystone changed from
        # every unit setting relation data to just the leader.  This means that
        # the charm needs to data from the first relation that actually has
        # data, or almalgamate the data from all the relations. For this charm,
        # it merges from the relation ids available like the charms.reactive
        # system does.
        _keys = (('auth_protocol', 'auth_protocol', 'http'),
                 ('service_protocol', 'service_protocol', 'http'),
                 ('keystone_host', 'auth_host', None),
                 ('service_host', 'service_host', None),
                 ('auth_port', 'auth_port', None),
                 ('service_user', 'service_username', None),
                 ('service_password', 'service_password', None),
                 ('service_tenant', 'service_tenant', None),
                 ('service_port', 'service_port', None),
                 ('api_version', 'api_version', '2'))
        _keysv3 = (('admin_domain_id', 'admin_domain_id'),
                   ('service_tenant_id', 'service_tenant_id'),
                   ('admin_domain_name', 'service_domain'),
                   ('admin_tenant_name', 'service_tenant'))

        kvs = {}
        relids = relation_ids('identity-service')
        # if we have relids at all, then set the auth_type to keystone
        if relids:
            kvs['auth_type'] = 'keystone'
        # merge the data from the related units
        for (key, source, default) in _keys:
            for relid in relids:
                for unit in related_units(relid):
                    value = relation_get(source, unit, relid)
                    if value is not None:
                        kvs[key] = value
                    else:
                        kvs[key] = kvs.get(key, default)
        # if the api is version 3, also merge the additional keys
        if kvs.get('api_version', None) == '3':
            for (key, source) in _keysv3:
                for relid in relids:
                    for unit in related_units(relid):
                        value = relation_get(source, unit, relid)
                        if value is not None:
                            kvs[key] = value

        # merge in the creds from the relation; which override the config
        ctxt.update(kvs)

        if config('prefer-ipv6'):
            for key in ['keystone_host', 'service_host']:
                host = ctxt.get(key)
                if host:
                    ctxt[key] = format_ipv6_addr(host)

        return ctxt


class MemcachedContext(OSContextGenerator):

    def __call__(self):
        ctxt = {}
        if config('prefer-ipv6'):
            ctxt['memcached_ip'] = 'ip6-localhost'
        else:
            ctxt['memcached_ip'] = get_host_ip(unit_get('private-address'))

        return ctxt


def get_swift_hash():
    if os.path.isfile(SWIFT_HASH_FILE):
        with open(SWIFT_HASH_FILE, 'r') as hashfile:
            swift_hash = hashfile.read().strip()
    elif config('swift-hash'):
        swift_hash = config('swift-hash')
        with open(SWIFT_HASH_FILE, 'w') as hashfile:
            hashfile.write(swift_hash)
    else:
        model_uuid = os.environ.get("JUJU_ENV_UUID",
                                    os.environ.get("JUJU_MODEL_UUID"))
        swift_hash = str(uuid.uuid3(uuid.UUID(model_uuid),
                                    service_name()))
        with open(SWIFT_HASH_FILE, 'w') as hashfile:
            hashfile.write(swift_hash)

    return swift_hash


def get_read_affinity():
    """ Gets read-affinity config option (lp1815879)

    Checks whether read-affinity config option is set correctly and if so
    returns its value.

    :returns: read-affinity config option
    :rtype: str
    :raises: SwiftProxyCharmException
    """
    if config('read-affinity'):
        read_affinity = config('read-affinity')
        pattern = re.compile(r"^r\d+z?(\d+)?=\d+(,\s?r\d+z?(\d+)?=\d+)*$")
        if not pattern.match(read_affinity):
            msg = "'read-affinity' config option is malformed"
            status_set('blocked', msg)
            raise SwiftProxyCharmException(msg)
        return read_affinity
    else:
        return None


def get_write_affinity():
    """ Gets write-affinity config option (lp1815879)

    Checks whether write-affinity config option is set correctly and if so
    returns its value.

    :returns: write-affinity config option
    :rtype: str
    :raises: SwiftProxyCharmException
    """
    if config('write-affinity'):
        write_affinity = config('write-affinity')
        pattern = re.compile(r"^r\d+(,\s?r\d+)*$")
        if not pattern.match(write_affinity):
            msg = "'write-affinity' config option is malformed"
            status_set('blocked', msg)
            raise SwiftProxyCharmException(msg)
        return write_affinity
    else:
        return None


def get_write_affinity_node_count():
    """ Gets write-affinity-node-count config option (lp1815879)

    Checks whether write-affinity-node-count config option is set correctly
    and if so returns its value.

    :returns: write-affinity-node-count config option
    :rtype: str
    :raises: SwiftProxyCharmException
    """
    if config('write-affinity-node-count'):
        write_affinity_node_count = config('write-affinity-node-count')
        pattern = re.compile(r"^\d+(\s\*\sreplicas)?$")
        if not pattern.match(write_affinity_node_count):
            msg = "'write-affinity-node-count' config option is malformed"
            status_set('blocked', msg)
            raise SwiftProxyCharmException(msg)
        return write_affinity_node_count
    else:
        return None


class SwiftHashContext(OSContextGenerator):

    def __call__(self):
        ctxt = {
            'swift_hash': get_swift_hash()
        }
        return ctxt

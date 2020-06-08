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

import importlib
import subprocess
import sys
import uuid

import unittest

from unittest.mock import (
    call,
    patch,
    MagicMock,
)
import lib.swift_utils
# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
sys.modules['apt'] = MagicMock()
sys.modules['apt_pkg'] = MagicMock()

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec, \
        patch('lib.swift_utils.sync_builders_and_rings_if_changed') as rdec, \
        patch('charmhelpers.core.hookenv.log'), \
        patch('lib.swift_utils.register_configs'):
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    rdec.side_effect = lambda f: f
    import hooks.swift_hooks as swift_hooks
    importlib.reload(swift_hooks)


# @unittest.skip("debugging ...")
class SwiftHooksTestCase(unittest.TestCase):

    @patch.object(swift_hooks, "relation_get")
    @patch.object(swift_hooks, "local_unit")
    def test_is_all_peers_stopped(self, mock_local_unit, mock_relation_get):
        token1 = str(uuid.uuid4())
        token2 = str(uuid.uuid4())
        mock_relation_get.return_value = token1

        responses = [{'some-other-key': token1}]
        self.assertFalse(swift_hooks.is_all_peers_stopped(responses))

        responses = [{'stop-proxy-service-ack': token1},
                     {'stop-proxy-service-ack': token2}]
        self.assertFalse(swift_hooks.is_all_peers_stopped(responses))

        responses = [{'stop-proxy-service-ack': token1},
                     {'stop-proxy-service-ack': token1},
                     {'some-other-key': token1}]
        self.assertFalse(swift_hooks.is_all_peers_stopped(responses))

        responses = [{'stop-proxy-service-ack': token1},
                     {'stop-proxy-service-ack': token1}]
        self.assertTrue(swift_hooks.is_all_peers_stopped(responses))

        mock_relation_get.return_value = token2

        responses = [{'stop-proxy-service-ack': token1},
                     {'stop-proxy-service-ack': token1}]
        self.assertFalse(swift_hooks.is_all_peers_stopped(responses))

    @patch.object(swift_hooks, 'config')
    @patch('charmhelpers.contrib.openstack.ip.config')
    @patch.object(swift_hooks, 'CONFIGS')
    @patch('charmhelpers.core.hookenv.local_unit')
    @patch('charmhelpers.core.hookenv.service_name')
    @patch('charmhelpers.contrib.openstack.ip.unit_get')
    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    @patch.object(swift_hooks, 'relation_set')
    def test_keystone_joined(self, _relation_set, _is_clustered, _unit_get,
                             _service_name, _local_unit, _CONFIGS, _ip_config,
                             _config):
        config_dict = {
            'bind-port': '1234',
            'region': 'RegionOne',
            'operator-roles': 'Operator,Monitor'
        }

        def foo(key=None):
            if key is None:
                return config_dict
            else:
                return config_dict.get(key)

        _config.side_effect = foo
        _ip_config.side_effect = foo
        _unit_get.return_value = 'swift-proxy'
        _local_unit.return_value = 'swift-proxy/0'
        _service_name.return_value = 'swift-proxy'
        _is_clustered.return_value = False

        swift_hooks.keystone_joined()

        _relation_set.assert_called_with(
            admin_url=None,
            internal_url=None,
            public_url=None,
            region=None,
            relation_id=None,
            s3_admin_url='http://swift-proxy:1234',
            s3_internal_url='http://swift-proxy:1234',
            s3_public_url='http://swift-proxy:1234',
            s3_region='RegionOne',
            s3_service='s3',
            service=None,
            swift_admin_url='http://swift-proxy:1234',
            swift_internal_url='http://swift-proxy:1234/v1/AUTH_$(tenant_id)s',
            swift_public_url='http://swift-proxy:1234/v1/AUTH_$(tenant_id)s',
            swift_region='RegionOne',
            swift_service='swift'
        )

    @patch.object(swift_hooks, 'config')
    @patch('charmhelpers.contrib.openstack.ip.config')
    @patch.object(swift_hooks, 'CONFIGS')
    @patch('charmhelpers.core.hookenv.local_unit')
    @patch('charmhelpers.core.hookenv.service_name')
    @patch('charmhelpers.contrib.openstack.ip.unit_get')
    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    @patch.object(swift_hooks, 'relation_set')
    def test_keystone_joined_public_hostname(self,
                                             _relation_set,
                                             _is_clustered,
                                             _unit_get,
                                             _service_name,
                                             _local_unit,
                                             _CONFIGS,
                                             _ip_config,
                                             _config):
        config_dict = {
            'bind-port': '1234',
            'region': 'RegionOne',
            'operator-roles': 'Operator,Monitor',
            'os-public-hostname': 'public.example.com'
        }

        def foo(key=None):
            if key is None:
                return config_dict
            else:
                return config_dict.get(key)

        _config.side_effect = _ip_config.side_effect = foo
        _unit_get.return_value = _service_name.return_value = 'swift-proxy'
        _local_unit.return_value = 'swift-proxy/0'
        _is_clustered.return_value = False

        swift_hooks.keystone_joined()

        _relation_set.assert_called_with(
            admin_url=None,
            internal_url=None,
            public_url=None,
            region=None,
            relation_id=None,
            s3_admin_url='http://swift-proxy:1234',
            s3_internal_url='http://swift-proxy:1234',
            s3_public_url='http://public.example.com:1234',
            s3_region='RegionOne',
            s3_service='s3',
            service=None,
            swift_admin_url='http://swift-proxy:1234',
            swift_internal_url='http://swift-proxy:1234/v1/AUTH_$(tenant_id)s',
            swift_public_url=('http://public.example.com' +
                              ':1234/v1/AUTH_$(tenant_id)s'),
            swift_region='RegionOne',
            swift_service='swift'
        )

    @patch.object(swift_hooks.time, 'time')
    @patch.object(swift_hooks, 'get_host_ip')
    @patch.object(swift_hooks, 'is_elected_leader')
    @patch.object(swift_hooks, 'related_units')
    @patch.object(swift_hooks, 'relation_ids')
    @patch.object(swift_hooks, 'relation_set')
    def test_update_rsync_acls(self, mock_rel_set, mock_rel_ids,
                               mock_rel_units, mock_is_leader,
                               mock_get_host_ip, mock_time):
        mock_time.return_value = 1234
        mock_is_leader.return_value = True
        mock_rel_ids.return_value = ['storage:1']
        mock_rel_units.return_value = ['unit/0', 'unit/1']

        def fake_get_host_ip(rid, unit):
            if unit == 'unit/0':
                return '10.0.0.1'
            elif unit == 'unit/1':
                return '10.0.0.2'

        mock_get_host_ip.side_effect = fake_get_host_ip
        swift_hooks.update_rsync_acls()
        calls = [call(rsync_allowed_hosts='10.0.0.1 10.0.0.2',
                      relation_id='storage:1', timestamp=1234)]
        mock_rel_set.assert_has_calls(calls)

    @patch.object(swift_hooks, 'get_relation_ip')
    @patch.object(swift_hooks, 'relation_set')
    @patch.object(swift_hooks, 'config')
    def test_cluster_joined(self, mock_config, mock_relation_set,
                            mock_get_relation_ip):
        mock_get_relation_ip.side_effect = [
            '10.0.2.1',
            '10.0.1.1',
            '10.0.0.1',
            '10.0.0.100']
        config = {'os-public-network': '10.0.0.0/24',
                  'os-internal-network': '10.0.1.0/24',
                  'os-admin-network': '10.0.2.0/24'}

        def fake_config(key):
            return config.get(key)

        mock_config.side_effect = fake_config

        swift_hooks.cluster_joined()
        mock_relation_set.assert_has_calls(
            [call(relation_id=None,
                  relation_settings={'private-address': '10.0.0.100',
                                     'admin-address': '10.0.2.1',
                                     'internal-address': '10.0.1.1',
                                     'public-address': '10.0.0.1'})])

    @patch.object(swift_hooks, 'generate_ha_relation_data')
    @patch.object(swift_hooks, 'relation_set')
    def test_ha_relation_joined(self, relation_set, generate_ha_relation_data):
        generate_ha_relation_data.return_value = {'rel_data': 'data'}
        swift_hooks.ha_relation_joined(relation_id='rid:23')
        relation_set.assert_called_once_with(
            relation_id='rid:23', rel_data='data')

    @patch.object(swift_hooks, 'clear_storage_rings_available')
    @patch.object(swift_hooks, 'is_elected_leader')
    @patch.object(swift_hooks, 'get_relation_ip')
    @patch.object(swift_hooks, 'try_initialize_swauth')
    @patch.object(swift_hooks, 'mark_www_rings_deleted')
    @patch.object(swift_hooks, 'service_stop')
    @patch.object(swift_hooks, 'relation_set')
    def test_swift_storage_joined(self, relation_set, service_stop,
                                  mark_www_rings_deleted,
                                  try_initialize_swauth,
                                  get_relation_ip,
                                  is_elected_leader,
                                  mock_clear_storage_rings_available):
        is_elected_leader.return_value = False
        get_relation_ip.return_value = '10.10.20.243'
        swift_hooks.storage_joined(rid='swift-storage:23')
        get_relation_ip.assert_called_with('swift-storage')
        relation_set.assert_called_with(
            relation_id='swift-storage:23',
            relation_settings={'private-address': '10.10.20.243'}
        )
        try_initialize_swauth.assert_called_once()
        mock_clear_storage_rings_available.assert_called_once()

    @patch.object(swift_hooks, 'log')
    @patch.object(swift_hooks, 'service_restart')
    @patch.object(swift_hooks.openstack, 'is_unit_paused_set')
    @patch.object(swift_hooks, 'update_rings')
    @patch.object(swift_hooks, 'config')
    @patch.object(swift_hooks, 'get_zone')
    @patch.object(swift_hooks, 'update_rsync_acls')
    @patch.object(swift_hooks, 'get_host_ip')
    @patch.object(swift_hooks, 'is_elected_leader')
    @patch.object(swift_hooks, 'relation_get')
    def test_swift_storage_changed(self, relation_get, is_elected_leader,
                                   get_host_ip, update_rsync_acls, get_zone,
                                   config, update_rings, is_unit_paused_set,
                                   service_restart, log):
        is_elected_leader.return_value = True
        get_host_ip.return_value = '10.0.0.10'
        rel_data = {
            'account_port': '6002',
            'container_port': '6001',
            'device': 'vdc',
            'egress-subnets': '10.5.0.37/32',
            'ingress-address': '10.5.0.37',
            'object_port': '6000',
            'private-address': '10.5.0.37',
            'zone': '1'}
        relation_get.side_effect = lambda x: rel_data.get(x)
        swift_hooks.storage_changed()
        update_rings.assert_called_once_with([{
            'ip': '10.0.0.10',
            'zone': 1,
            'account_port': 6002,
            'object_port': 6000,
            'container_port': 6001,
            'device': 'vdc'}])

    @patch.object(swift_hooks, 'status_set')
    @patch.object(swift_hooks, 'is_leader')
    @patch.object(lib.swift_utils, 'leader_get')
    @patch.object(lib.swift_utils, 'leader_set')
    def test_rings_distributor_joined(self, leader_set, leader_get, is_leader,
                                      status_set):
        leader_get.return_value = None
        is_leader.return_value = True
        swift_hooks.rings_distributor_joined()
        leader_set.assert_called_once_with(
            {'swift-proxy-rings-distributor': True})
        leader_set.reset_mock()
        is_leader.return_value = False
        swift_hooks.rings_distributor_joined()
        self.assertFalse(leader_set.called)

    @patch.object(swift_hooks, 'status_set')
    @patch.object(lib.swift_utils, 'leader_get')
    def test_rings_distributor_joined_consumer(self, leader_get, status_set):
        leader_get.return_value = True
        with self.assertRaises(lib.swift_utils.SwiftProxyCharmException):
            swift_hooks.rings_distributor_joined()
        status_set.assert_called_once_with(
            'blocked',
            ('Swift Proxy cannot act as both rings distributor and rings '
             'consumer'))

    @patch.object(swift_hooks, 'broadcast_rings_available')
    def test_rings_distributor_changed(self, broadcast_rings_available):
        swift_hooks.rings_distributor_changed()
        broadcast_rings_available.assert_called_once_with()

    @patch.object(swift_hooks, 'is_leader')
    @patch.object(lib.swift_utils, 'leader_set')
    def test_rings_distributor_departed(self, leader_set, is_leader):
        is_leader.return_value = True
        swift_hooks.rings_distributor_departed()
        leader_set.assert_called_once_with(
            {'swift-proxy-rings-distributor': None})
        leader_set.reset_mock()
        is_leader.return_value = False
        swift_hooks.rings_distributor_departed()
        self.assertFalse(leader_set.called)

    @patch.object(swift_hooks, 'is_leader')
    @patch.object(lib.swift_utils, 'leader_get')
    @patch.object(lib.swift_utils, 'leader_set')
    def test_rings_consumer_joined(self, leader_set, leader_get, is_leader):
        leader_data = {}
        leader_get.side_effect = lambda x: leader_data.get(x)
        is_leader.return_value = True
        swift_hooks.rings_consumer_joined()
        leader_set.assert_called_once_with(
            {'swift-proxy-rings-consumer': True})
        leader_set.reset_mock()
        is_leader.return_value = False
        swift_hooks.rings_consumer_joined()
        self.assertFalse(leader_set.called)

    @patch.object(lib.swift_utils, 'leader_get')
    @patch.object(swift_hooks, 'status_set')
    def test_rings_consumer_joined_distributor(self, status_set, leader_get):
        leader_data = {
            'swift-proxy-rings-distributor': True}
        leader_get.side_effect = lambda x: leader_data.get(x)
        swift_hooks.rings_consumer_joined()
        status_set.assert_called_once_with(
            'blocked',
            ('Swift Proxy cannot act as both rings distributor and rings '
             'consumer'))

    @patch.object(lib.swift_utils, 'leader_get')
    @patch.object(swift_hooks, 'status_set')
    def test_rings_consumer_joined_consumer(self, status_set, leader_get):
        leader_data = {
            'swift-proxy-rings-consumer': True}
        leader_get.side_effect = lambda x: leader_data.get(x)
        swift_hooks.rings_consumer_joined()
        status_set.assert_called_once_with(
            'blocked',
            'Swift Proxy already acting as rings consumer')

    @patch.object(swift_hooks, 'get_swift_hash')
    @patch.object(swift_hooks, 'broadcast_rings_available')
    @patch.object(swift_hooks, 'fetch_swift_rings_and_builders')
    @patch.object(swift_hooks, 'relation_get')
    def test_rings_consumer_changed(self, relation_get,
                                    fetch_swift_rings_and_builders,
                                    broadcast_rings_available,
                                    get_swift_hash):
        rel_data = {
            'rings_url': 'http://some-url:999',
            'swift_hash': 'swhash'}
        relation_get.side_effect = lambda x: rel_data.get(x)
        get_swift_hash.return_value = 'swhash'
        swift_hooks.rings_consumer_changed()
        fetch_swift_rings_and_builders.assert_called_once_with(
            'http://some-url:999')
        broadcast_rings_available.assert_called_once_with()

    @patch.object(swift_hooks, 'log')
    @patch.object(swift_hooks, 'get_swift_hash')
    @patch.object(swift_hooks, 'broadcast_rings_available')
    @patch.object(swift_hooks, 'fetch_swift_rings_and_builders')
    @patch.object(swift_hooks, 'relation_get')
    def test_rings_consumer_changed_no_url(self, relation_get,
                                           fetch_swift_rings_and_builders,
                                           broadcast_rings_available,
                                           get_swift_hash,
                                           log):
        rel_data = {'swift_hash': 'swhash'}
        relation_get.side_effect = lambda x: rel_data.get(x)
        swift_hooks.rings_consumer_changed()
        self.assertFalse(fetch_swift_rings_and_builders.called)
        self.assertFalse(broadcast_rings_available.called)
        log.assert_called_once_with(
            'rings_consumer_relation_changed: Peer not ready?')

    @patch.object(swift_hooks, 'log')
    @patch.object(swift_hooks, 'get_swift_hash')
    @patch.object(swift_hooks, 'broadcast_rings_available')
    @patch.object(swift_hooks, 'fetch_swift_rings_and_builders')
    @patch.object(swift_hooks, 'relation_get')
    def test_rings_consumer_changed_empty_str(self, relation_get,
                                              fetch_swift_rings_and_builders,
                                              broadcast_rings_available,
                                              get_swift_hash,
                                              log):
        rel_data = {
            'rings_url': '',
            'swift_hash': 'swhash'}
        relation_get.side_effect = lambda x: rel_data.get(x)
        swift_hooks.rings_consumer_changed()
        self.assertFalse(fetch_swift_rings_and_builders.called)
        self.assertFalse(broadcast_rings_available.called)
        log.assert_called_once_with(
            'rings_consumer_relation_changed: Peer not ready?')

    @patch.object(swift_hooks, 'status_set')
    @patch.object(swift_hooks, 'get_swift_hash')
    @patch.object(swift_hooks, 'broadcast_rings_available')
    @patch.object(swift_hooks, 'fetch_swift_rings_and_builders')
    @patch.object(swift_hooks, 'relation_get')
    def test_rings_consumer_changed_hash_miss(self, relation_get,
                                              fetch_swift_rings_and_builders,
                                              broadcast_rings_available,
                                              get_swift_hash,
                                              status_set):
        rel_data = {
            'rings_url': 'http://some-url:999',
            'swift_hash': 'swhash'}
        relation_get.side_effect = lambda x: rel_data.get(x)
        get_swift_hash.return_value = 'mismatch'
        with self.assertRaises(lib.swift_utils.SwiftProxyCharmException):
            swift_hooks.rings_consumer_changed()
        self.assertFalse(fetch_swift_rings_and_builders.called)
        self.assertFalse(broadcast_rings_available.called)
        status_set.assert_called_once_with(
            'blocked',
            'Swift hash has to be unique in multi-region setup')

    @patch.object(swift_hooks, 'log')
    @patch.object(swift_hooks, 'get_swift_hash')
    @patch.object(swift_hooks, 'broadcast_rings_available')
    @patch.object(swift_hooks, 'fetch_swift_rings_and_builders')
    @patch.object(swift_hooks, 'relation_get')
    def test_rings_consumer_changed_fetch_fail(self, relation_get,
                                               fetch_swift_rings_and_builders,
                                               broadcast_rings_available,
                                               get_swift_hash,
                                               log):
        rel_data = {
            'rings_url': 'http://some-url:999',
            'swift_hash': 'swhash'}
        relation_get.side_effect = lambda x: rel_data.get(x)
        get_swift_hash.return_value = 'swhash'

        def _fetch(url):
            raise subprocess.CalledProcessError('cmd', 1)
        fetch_swift_rings_and_builders.side_effect = _fetch
        swift_hooks.rings_consumer_changed()
        fetch_swift_rings_and_builders.assert_called_once_with(
            'http://some-url:999')
        log.assert_called_once_with(
            ('Failed to sync rings from http://some-url:999 - no longer '
             'available from that unit?'),
            level='WARNING')
        broadcast_rings_available.assert_called_once_with()

    @patch.object(swift_hooks, 'is_leader')
    @patch.object(lib.swift_utils, 'leader_set')
    def test_rings_consumer_departed(self, leader_set, is_leader):
        is_leader.return_value = True
        swift_hooks.rings_consumer_departed()
        leader_set.assert_called_once_with(
            {'swift-proxy-rings-consumer': None})

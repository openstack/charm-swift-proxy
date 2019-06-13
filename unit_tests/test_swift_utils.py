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

import mock
import os
import shutil
import tempfile
import uuid
import unittest
import subprocess

with mock.patch('charmhelpers.core.hookenv.config'):
    import lib.swift_utils as swift_utils


def init_ring_paths(tmpdir):
    swift_utils.SWIFT_CONF_DIR = tmpdir
    for ring in swift_utils.SWIFT_RINGS.keys():
        path = os.path.join(tmpdir, "{}.builder".format(ring))
        swift_utils.SWIFT_RINGS[ring] = path
        with open(path, 'w') as fd:
            fd.write("0\n")


def create_mock_load_builder_fn(mock_rings):
    """To avoid the need for swift.common.ring library, mock a basic rings
    dictionary, keyed by path. Each ring has enough logic to hold a dictionary
    with a single 'devs' key, which stores the list of passed dev(s) by
    add_dev().

    If swift (actual) ring representation diverges (see _load_builder),
    this mock will need to be adapted.

    :param mock_rings: a dict containing the dict form of the rings
    """
    def mock_load_builder_fn(path):
        class mock_ring(object):
            def __init__(self, path):
                self.path = path

            def to_dict(self):
                return mock_rings[self.path]

            def add_dev(self, dev):
                mock_rings[self.path]['devs'].append(dev)

        return mock_ring(path)
    return mock_load_builder_fn


class SwiftUtilsTestCase(unittest.TestCase):

    @mock.patch.object(swift_utils, 'previously_synced')
    @mock.patch('lib.swift_utils.update_www_rings')
    @mock.patch('lib.swift_utils.get_builders_checksum')
    @mock.patch('lib.swift_utils.get_rings_checksum')
    @mock.patch('lib.swift_utils.balance_rings')
    @mock.patch('lib.swift_utils.log')
    @mock.patch('lib.swift_utils.os.path.exists')
    @mock.patch('lib.swift_utils.is_elected_leader')
    @mock.patch('lib.swift_utils.get_min_part_hours')
    @mock.patch('lib.swift_utils.set_min_part_hours')
    def test_update_rings(self, mock_set_min_hours,
                          mock_get_min_hours,
                          mock_is_elected_leader, mock_path_exists,
                          mock_log, mock_balance_rings,
                          mock_get_rings_checksum,
                          mock_get_builders_checksum, mock_update_www_rings,
                          mock_previously_synced):

        # Make sure same is returned for both so that we don't try to sync
        mock_get_rings_checksum.return_value = None
        mock_get_builders_checksum.return_value = None
        mock_previously_synced.return_value = True

        # Test blocker 1
        mock_is_elected_leader.return_value = False
        swift_utils.update_rings()
        self.assertFalse(mock_balance_rings.called)

        # Test blocker 2
        mock_path_exists.return_value = False
        mock_is_elected_leader.return_value = True
        swift_utils.update_rings()
        self.assertFalse(mock_get_min_hours.called)
        self.assertFalse(mock_balance_rings.called)

        # Test blocker 3
        mock_path_exists.return_value = True
        mock_is_elected_leader.return_value = True
        mock_get_min_hours.return_value = 10
        swift_utils.update_rings(min_part_hours=10)
        self.assertTrue(mock_get_min_hours.called)
        self.assertFalse(mock_set_min_hours.called)
        self.assertFalse(mock_balance_rings.called)

        mock_get_min_hours.reset_mock()

        # Test go through
        mock_path_exists.return_value = True
        mock_is_elected_leader.return_value = True
        mock_get_min_hours.return_value = 0
        swift_utils.update_rings(min_part_hours=10)
        self.assertTrue(mock_get_min_hours.called)
        self.assertTrue(mock_set_min_hours.called)
        self.assertTrue(mock_balance_rings.called)

    @mock.patch('lib.swift_utils.previously_synced')
    @mock.patch('lib.swift_utils.balance_rings')
    @mock.patch('lib.swift_utils.add_to_ring')
    @mock.patch('lib.swift_utils.exists_in_ring')
    @mock.patch('lib.swift_utils.is_elected_leader')
    def test_update_rings_multiple_devs(self,
                                        mock_is_leader_elected,
                                        mock_exists_in_ring,
                                        mock_add_to_ring,
                                        mock_balance_rings,
                                        mock_previously_synced):
        # note that this test does not (and neither did its predecessor) test
        # the 'min_part_hours is non None' part of update_rings()
        devices = ['sdb', 'sdc']
        node_settings = {
            'object_port': 6000,
            'container_port': 6001,
            'account_port': 6002,
            'zone': 1,
            'ip': '1.2.3.4',
        }

        nodes = []
        for dev in devices:
            node = node_settings.copy()
            node['device'] = dev
            nodes.append(node)

        mock_is_leader_elected.return_value = True
        mock_previously_synced.return_value = True
        mock_exists_in_ring.side_effect = lambda *args: False

        swift_utils.update_rings(nodes)
        calls = [mock.call(os.path.join(swift_utils.SWIFT_CONF_DIR,
                                        'account.builder'),
                           {
                               'zone': 1,
                               'object_port': 6000,
                               'ip': '1.2.3.4',
                               'container_port': 6001,
                               'device': 'sdb',
                               'account_port': 6002}),
                 mock.call(os.path.join(swift_utils.SWIFT_CONF_DIR,
                                        'container.builder'),
                           {
                               'zone': 1,
                               'object_port': 6000,
                               'ip': '1.2.3.4',
                               'container_port': 6001,
                               'device': 'sdb',
                               'account_port': 6002}),
                 mock.call(os.path.join(swift_utils.SWIFT_CONF_DIR,
                                        'object.builder'),
                           {
                               'zone': 1,
                               'object_port': 6000,
                               'ip': '1.2.3.4',
                               'container_port': 6001,
                               'device': 'sdb',
                               'account_port': 6002}),
                 mock.call(os.path.join(swift_utils.SWIFT_CONF_DIR,
                                        'account.builder'),
                           {
                               'zone': 1,
                               'object_port': 6000,
                               'ip': '1.2.3.4',
                               'container_port': 6001,
                               'device': 'sdc',
                               'account_port': 6002}),
                 mock.call(os.path.join(swift_utils.SWIFT_CONF_DIR,
                                        'container.builder'),
                           {
                               'zone': 1,
                               'object_port': 6000,
                               'ip': '1.2.3.4',
                               'container_port': 6001,
                               'device': 'sdc',
                               'account_port': 6002}),
                 mock.call(os.path.join(swift_utils.SWIFT_CONF_DIR,
                                        'object.builder'),
                           {
                               'zone': 1,
                               'object_port': 6000,
                               'ip': '1.2.3.4',
                               'container_port': 6001,
                               'device': 'sdc',
                               'account_port': 6002})]
        mock_exists_in_ring.assert_has_calls(calls)
        mock_balance_rings.assert_called_once_with()
        mock_add_to_ring.assert_called()

        # try re-adding, assert add_to_ring was not called
        mock_add_to_ring.reset_mock()
        mock_exists_in_ring.side_effect = lambda *args: True
        swift_utils.update_rings(nodes)
        mock_add_to_ring.assert_not_called()

    @mock.patch('lib.swift_utils.balance_rings')
    @mock.patch('lib.swift_utils.log')
    @mock.patch('lib.swift_utils.is_elected_leader')
    @mock.patch('lib.swift_utils.config')
    @mock.patch('lib.swift_utils.update_www_rings')
    @mock.patch('lib.swift_utils.cluster_sync_rings')
    def test_sync_builders_and_rings_if_changed(self, mock_cluster_sync_rings,
                                                mock_update_www_rings,
                                                mock_config,
                                                mock_is_elected_leader,
                                                mock_log,
                                                mock_balance_rings):

        @swift_utils.sync_builders_and_rings_if_changed
        def mock_balance():
            for ring, builder in swift_utils.SWIFT_RINGS.items():
                ring = os.path.join(swift_utils.SWIFT_CONF_DIR,
                                    '{}.ring.gz'.format(ring))
                with open(ring, 'w') as fd:
                    fd.write(str(uuid.uuid4()))

                with open(builder, 'w') as fd:
                    fd.write(str(uuid.uuid4()))

        mock_balance_rings.side_effect = mock_balance

        init_ring_paths(tempfile.mkdtemp())
        try:
            swift_utils.balance_rings()
        finally:
            shutil.rmtree(swift_utils.SWIFT_CONF_DIR)

        self.assertTrue(mock_update_www_rings.called)
        self.assertTrue(mock_cluster_sync_rings.called)

    @mock.patch('lib.swift_utils.get_www_dir')
    def test_mark_www_rings_deleted(self, mock_get_www_dir):
        try:
            tmpdir = tempfile.mkdtemp()
            mock_get_www_dir.return_value = tmpdir
            swift_utils.mark_www_rings_deleted()
        finally:
            shutil.rmtree(tmpdir)

    @mock.patch('lib.swift_utils.is_elected_leader', lambda arg: True)
    @mock.patch.object(swift_utils, 'get_hostaddr', lambda *args: '1.2.3.4')
    @mock.patch('lib.swift_utils.uuid')
    def test_cluster_rpc_stop_proxy_request(self, mock_uuid):
        mock_uuid.uuid4.return_value = 'test-uuid'
        rpc = swift_utils.SwiftProxyClusterRPC()
        rq = rpc.stop_proxy_request(peers_only=True)
        self.assertEqual({'trigger': 'test-uuid',
                          'broker-token': None,
                          'broker-timestamp': None,
                          'builder-broker': '1.2.3.4',
                          'peers-only': 1,
                          'leader-changed-notification': None,
                          'resync-request': None,
                          'stop-proxy-service': 'test-uuid',
                          'stop-proxy-service-ack': None,
                          'sync-only-builders': None}, rq)

        rq = rpc.stop_proxy_request()
        self.assertEqual({'trigger': 'test-uuid',
                          'broker-token': None,
                          'broker-timestamp': None,
                          'builder-broker': '1.2.3.4',
                          'peers-only': None,
                          'leader-changed-notification': None,
                          'resync-request': None,
                          'stop-proxy-service': 'test-uuid',
                          'stop-proxy-service-ack': None,
                          'sync-only-builders': None}, rq)

        template_keys = set(rpc.template())
        self.assertTrue(set(rq.keys()).issubset(template_keys))

    @mock.patch('lib.swift_utils.uuid')
    def test_cluster_rpc_stop_proxy_ack(self, mock_uuid):
        mock_uuid.uuid4.return_value = 'token2'
        rpc = swift_utils.SwiftProxyClusterRPC()
        rq = rpc.stop_proxy_ack(echo_token='token1', echo_peers_only='1')
        self.assertEqual({'trigger': 'token2',
                          'broker-token': None,
                          'builder-broker': None,
                          'broker-timestamp': None,
                          'peers-only': '1',
                          'leader-changed-notification': None,
                          'resync-request': None,
                          'stop-proxy-service': None,
                          'stop-proxy-service-ack': 'token1',
                          'sync-only-builders': None}, rq)

        template_keys = set(rpc.template())
        self.assertTrue(set(rq.keys()).issubset(template_keys))

    @mock.patch('lib.swift_utils.is_elected_leader', lambda arg: True)
    @mock.patch.object(swift_utils, 'get_hostaddr', lambda *args: '1.2.3.4')
    @mock.patch('lib.swift_utils.uuid')
    def test_cluster_rpc_sync_request(self, mock_uuid):
        mock_uuid.uuid4.return_value = 'token2'
        rpc = swift_utils.SwiftProxyClusterRPC()
        rq = rpc.sync_rings_request('token1', '1.234000')
        self.assertEqual({'trigger': 'token2',
                          'broker-token': 'token1',
                          'broker-timestamp': '1.234000',
                          'builder-broker': '1.2.3.4',
                          'peers-only': None,
                          'leader-changed-notification': None,
                          'resync-request': None,
                          'stop-proxy-service': None,
                          'stop-proxy-service-ack': None,
                          'sync-only-builders': None}, rq)

        template_keys = set(rpc.template())
        self.assertTrue(set(rq.keys()).issubset(template_keys))

    @mock.patch('lib.swift_utils.is_elected_leader', lambda arg: True)
    @mock.patch('lib.swift_utils.uuid')
    def test_cluster_rpc_notify_leader_changed(self, mock_uuid):
        mock_uuid.uuid4.return_value = 'e4b67426-6cc0-4aa3-829d-227999cd0a75'
        rpc = swift_utils.SwiftProxyClusterRPC()
        rq = rpc.notify_leader_changed('token1')
        self.assertEqual({'trigger': 'e4b67426-6cc0-4aa3-829d-227999cd0a75',
                          'broker-token': None,
                          'builder-broker': None,
                          'broker-timestamp': None,
                          'peers-only': None,
                          'leader-changed-notification': 'token1',
                          'stop-proxy-service': None,
                          'stop-proxy-service-ack': None,
                          'resync-request': None,
                          'sync-only-builders': None}, rq)

        template_keys = set(rpc.template().keys())
        self.assertTrue(set(rq.keys()).issubset(template_keys))

    def test_all_responses_equal(self):
        responses = [{'a': 1, 'c': 3}]
        self.assertTrue(swift_utils.all_responses_equal(responses, 'b',
                                                        must_exist=False))

        responses = [{'a': 1, 'c': 3}]
        self.assertFalse(swift_utils.all_responses_equal(responses, 'b'))

        responses = [{'a': 1, 'b': 2, 'c': 3}]
        self.assertTrue(swift_utils.all_responses_equal(responses, 'b'))

        responses = [{'a': 1, 'b': 2, 'c': 3}, {'a': 1, 'b': 2, 'c': 3}]
        self.assertTrue(swift_utils.all_responses_equal(responses, 'b'))

        responses = [{'a': 1, 'b': 2, 'c': 3}, {'a': 2, 'b': 2, 'c': 3}]
        self.assertTrue(swift_utils.all_responses_equal(responses, 'b'))

        responses = [{'a': 1, 'b': 2, 'c': 3}, {'a': 1, 'b': 3, 'c': 3}]
        self.assertFalse(swift_utils.all_responses_equal(responses, 'b'))

    def test_get_first_available_value(self):
        rsps = [{'key1': 'A'}, {'key1': 'B'}]
        self.assertEqual('A',
                         swift_utils.get_first_available_value(rsps, 'key1'))

        rsps = [{'key2': 'A'}, {'key1': 'B'}]
        self.assertEqual('B',
                         swift_utils.get_first_available_value(rsps, 'key1'))

        rsps = [{'key2': 'A'}, {'key1': 'B'}]
        self.assertIsNone(swift_utils.get_first_available_value(rsps, 'key3'))

        rsps = []
        self.assertIsNone(swift_utils.get_first_available_value(rsps, 'key3'))

    @mock.patch.object(swift_utils, 'relation_get')
    @mock.patch.object(swift_utils, 'related_units', lambda arg: ['proxy/1'])
    @mock.patch.object(swift_utils, 'relation_ids', lambda arg: ['cluster:1'])
    def test_is_most_recent_timestamp(self, mock_rel_get):
        mock_rel_get.return_value = {'broker-timestamp': '1111'}
        self.assertTrue(swift_utils.is_most_recent_timestamp('1234'))
        mock_rel_get.return_value = {'broker-timestamp': '2234'}
        self.assertFalse(swift_utils.is_most_recent_timestamp('1234'))
        mock_rel_get.return_value = {}
        self.assertFalse(swift_utils.is_most_recent_timestamp('1234'))
        mock_rel_get.return_value = {'broker-timestamp': '2234'}
        self.assertFalse(swift_utils.is_most_recent_timestamp(None))

    @mock.patch.object(swift_utils, 'relation_get')
    @mock.patch.object(swift_utils, 'related_units', lambda arg: ['proxy/1'])
    @mock.patch.object(swift_utils, 'relation_ids', lambda arg: ['cluster:1'])
    def test_timestamps_available(self, mock_rel_get):
        mock_rel_get.return_value = {}
        self.assertFalse(swift_utils.timestamps_available('proxy/1'))
        mock_rel_get.return_value = {'broker-timestamp': '1234'}
        self.assertFalse(swift_utils.timestamps_available('proxy/1'))
        mock_rel_get.return_value = {'broker-timestamp': '1234'}
        self.assertTrue(swift_utils.timestamps_available('proxy/2'))

    @mock.patch.object(swift_utils, 'get_manager')
    def test_add_to_ring(self, mock_get_manager):
        ring = 'account'
        node = {
            'ip': '172.16.0.2',
            'region': 1,
            'account_port': 6000,
            'zone': 1,
            'device': '/dev/sdb',
        }
        swift_utils.add_to_ring(ring, node)
        mock_get_manager().add_dev.assert_called_once_with('account', {
            'meta': '',
            'zone': 1,
            'ip': '172.16.0.2',
            'device': '/dev/sdb',
            'port': 6000,
            'weight': 100
        })

    @mock.patch.object(subprocess, 'check_call')
    def test_remove_from_ring(self, check_call):
        swift_utils.remove_from_ring('/etc/swift/account.builder', 'd1')
        check_call.assert_called_once_with(['swift-ring-builder',
                                            '/etc/swift/account.builder',
                                            'remove',
                                            'd1'])

    @mock.patch.object(subprocess, 'check_call')
    def test_set_weight_in_ring(self, check_call):
        swift_utils.set_weight_in_ring('/etc/swift/account.builder', 'd1', 0.0)
        check_call.assert_called_once_with(['swift-ring-builder',
                                            '/etc/swift/account.builder',
                                            'set_weight',
                                            'd1',
                                            0.0])

    @mock.patch('lib.swift_utils.config')
    @mock.patch('lib.swift_utils.set_os_workload_status')
    @mock.patch('lib.swift_utils.SwiftRingContext.__call__')
    @mock.patch('lib.swift_utils.has_minimum_zones')
    @mock.patch('lib.swift_utils.relation_ids')
    def customer_check_assess_status(
            self, relation_ids, has_min_zones,
            ctxt, workload_status, config):
        config.side_effect = [3, 3, 3]

        relation_ids.return_value = []
        s, m = swift_utils.customer_check_assess_status(None)
        assert s, m == ('blocked', 'Missing relation: storage')

        relation_ids.return_value = ['swift-storage:1']

        ctxt.return_value = {'allowed_hosts': ['1.2.3.4']}
        s, m = swift_utils.customer_check_assess_status(None)
        assert s, m == ('blocked',
                        'Not enough related storage nodes')

        ctxt.return_value = {'allowed_hosts': ['1.2.3.4', '2.3.4.5',
                                               '3.4.5.6']}
        has_min_zones.return_value = False
        s, m = swift_utils.customer_check_assess_status(None)
        assert s, m == ('blocked',
                        'Not enough storage zones for minimum '
                        'replicas')

    @mock.patch.object(swift_utils, 'os_application_version_set')
    def test_assess_status(self, os_application_version_set):
        with mock.patch.object(swift_utils, 'assess_status_func') as asf:
            callee = mock.MagicMock()
            asf.return_value = callee
            swift_utils.assess_status('test-config')
            asf.assert_called_once_with('test-config', None)
            callee.assert_called_once_with()
            os_application_version_set.assert_called_with(
                swift_utils.VERSION_PACKAGE
            )

    @mock.patch.object(swift_utils, 'relation_ids')
    @mock.patch.object(swift_utils, 'services')
    @mock.patch.object(swift_utils, 'make_assess_status_func')
    def test_assess_status_func(self,
                                make_assess_status_func,
                                services,
                                relation_ids):
        relation_ids.return_value = True
        services.return_value = 's1'
        required_interfaces = {'identity': ['identity-service']}
        swift_utils.assess_status_func('test-config')
        relation_ids.assert_called_once_with('identity-service')
        # ports=None whilst port checks are disabled.
        make_assess_status_func.assert_called_once_with(
            'test-config', required_interfaces,
            charm_func=swift_utils.customer_check_assess_status,
            services='s1', ports=None)

    @mock.patch.object(swift_utils, 'leader_set')
    @mock.patch.object(swift_utils, 'determine_api_port')
    @mock.patch.object(swift_utils, 'is_leader')
    @mock.patch.object(swift_utils, 'config')
    @mock.patch.object(swift_utils, 'leader_get')
    @mock.patch.object(subprocess, 'check_call')
    def test_config_and_leader_get(self, check_call, leader_get, config,
                                   is_leader, determine_api_port, leader_set):
        """Ensure that we config_get, and then leader_get."""
        config.side_effect = lambda key: {
            'auth-type': 'swauth',
            'swauth-admin-key': None,
            'bind-port': 8080}[key]
        determine_api_port.return_value = 8080
        is_leader.return_value = True
        leader_get.return_value = "Test"
        swift_utils.try_initialize_swauth()
        check_call.assert_called_with(['swauth-prep',
                                       '-A',
                                       'http://localhost:8080/auth',
                                       '-K',
                                       'Test'])

    @mock.patch.object(swift_utils.uuid, 'uuid4')
    @mock.patch.object(swift_utils, 'relation_set')
    @mock.patch.object(swift_utils, 'get_swift_hash')
    @mock.patch.object(swift_utils, 'log')
    @mock.patch.object(swift_utils, 'relation_ids')
    @mock.patch.object(swift_utils, 'get_www_dir')
    @mock.patch.object(swift_utils, 'format_ipv6_addr')
    @mock.patch.object(swift_utils, 'get_hostaddr')
    @mock.patch.object(swift_utils, 'is_elected_leader')
    def test_notify_storage_rings_available(self, mock_is_leader,
                                            mock_get_hostaddr,
                                            mock_format_ipv6_addr,
                                            mock_get_www_dir,
                                            mock_relation_ids,
                                            mock_log,
                                            mock_get_swift_hash,
                                            mock_relation_set,
                                            mock_uuid):
        mock_is_leader.return_value = True
        mock_get_hostaddr.return_value = '10.0.0.1'
        mock_format_ipv6_addr.return_value = None
        mock_get_www_dir.return_value = 'some/dir'
        mock_relation_ids.return_value = ['storage:0']
        mock_get_swift_hash.return_value = 'greathash'
        mock_uuid.return_value = 'uuid-1234'
        swift_utils.notify_storage_rings_available('1.234')
        mock_relation_set.assert_called_once_with(
            broker_timestamp='1.234',
            relation_id='storage:0',
            rings_url='http://10.0.0.1/dir',
            swift_hash='greathash',
            trigger='uuid-1234')

    @mock.patch.object(swift_utils, 'relation_set')
    @mock.patch.object(swift_utils, 'relation_ids')
    def test_clear_notify_storage_rings_available(self, mock_relation_ids,
                                                  mock_relation_set):
        mock_relation_ids.return_value = ['storage:0']
        swift_utils.clear_storage_rings_available()
        mock_relation_set.assert_called_once_with(
            relation_id='storage:0', rings_url=None)

    def test_determine_packages(self):
        self.assertEqual(
            ['swift',
             'swift-proxy',
             'memcached',
             'apache2',
             'python-keystone'],
            swift_utils.determine_packages('essex')
        )

        self.assertEqual(
            ['swift',
             'swift-proxy',
             'memcached',
             'apache2',
             'python-keystone',
             'swift-plugin-s3',
             'swauth'],
            swift_utils.determine_packages('folsom')
        )

        self.assertEqual(
            ['swift',
             'swift-proxy',
             'memcached',
             'apache2',
             'swift-plugin-s3',
             'swauth',
             'python-ceilometermiddleware',
             'python-keystonemiddleware'],
            swift_utils.determine_packages('mitaka')
        )

        self.assertEqual(
            ['swift',
             'swift-proxy',
             'memcached',
             'apache2',
             'swauth',
             'python-ceilometermiddleware',
             'python-keystonemiddleware'],
            swift_utils.determine_packages('rocky')
        )

    @mock.patch('lib.swift_utils.config')
    def test_determine_replicas_account_set(self, config):
        config.side_effect = lambda key: {
            'replicas': 3,
            'replicas-account': 2,
            'replicas-container': None}[key]
        replicas = swift_utils.determine_replicas('account')
        self.assertEqual(replicas, 2)

    @mock.patch('lib.swift_utils.config')
    def test_determine_replicas_account_not_set(self, config):
        config.side_effect = lambda key: {
            'replicas': 3,
            'replicas-account': None,
            'replicas-container': None}[key]
        replicas = swift_utils.determine_replicas('account')
        self.assertEqual(replicas, 3)

    @mock.patch('lib.swift_utils.config')
    def test_determine_replicas_container_set(self, config):
        config.side_effect = lambda key: {
            'replicas': 3,
            'replicas-account': None,
            'replicas-container': 2}[key]
        replicas = swift_utils.determine_replicas('container')
        self.assertEqual(replicas, 2)

    @mock.patch('lib.swift_utils.config')
    def test_determine_replicas_container_not_set(self, config):
        config.side_effect = lambda key: {
            'replicas': 3,
            'replicas-account': None,
            'replicas-container': None}[key]
        replicas = swift_utils.determine_replicas('container')
        self.assertEqual(replicas, 3)

    @mock.patch('lib.swift_utils.config')
    def test_determine_replicas_object(self, config):
        config.side_effect = lambda key: {
            'replicas': 3,
            'replicas-account': 2,
            'replicas-container': 2}[key]
        replicas = swift_utils.determine_replicas('object')
        self.assertEqual(replicas, 3)

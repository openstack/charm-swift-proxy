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
import tempfile
import unittest
import uuid


with mock.patch('charmhelpers.core.hookenv.config'):
    import lib.swift_context as swift_context


class SwiftIdentityContextTest(unittest.TestCase):

    @mock.patch('lib.swift_context.os_release')
    @mock.patch('lib.swift_context.leader_get')
    @mock.patch('lib.swift_context.relation_get')
    @mock.patch('lib.swift_context.related_units')
    @mock.patch('lib.swift_context.relation_ids')
    @mock.patch('lib.swift_context.IdentityServiceContext')
    @mock.patch('lib.swift_context.determine_api_port')
    @mock.patch('lib.swift_context.unit_get')
    @mock.patch('lib.swift_context.get_host_ip')
    @mock.patch('lib.swift_context.config')
    def test_context_api_v2(self, mock_config, mock_get_host_ip,
                            mock_unit_get, mock_determine_api_port,
                            mock_IdentityServiceContext, mock_relation_ids,
                            mock_related_units, mock_relation_get,
                            mock_leader_get, mock_os_release):
        _relinfo = {
            'auth_protocol': 'http',
            'service_protocol': 'http',
            'auth_host': 'kshost',
            'service_host': 'kshost',
            'auth_port': '5000',
            'service_username': 'svcuser',
            'service_password': 'svcpasswd',
            'service_tenant': 'svctenant',
            'service_port': 'svcport',
            'api_version': None,
        }
        mock_config.return_value = None
        mock_relation_ids.return_value = ['rid1']
        mock_related_units.return_value = ['ksunit/0']
        mock_relation_get.side_effect = lambda x, y, z: _relinfo[x]
        mock_os_release.return_value = 'queens'
        ctxt = swift_context.SwiftIdentityContext()
        self.assertEqual(ctxt()['api_version'], '2')

    @mock.patch('lib.swift_context.os_release')
    @mock.patch('lib.swift_context.leader_get')
    @mock.patch('lib.swift_context.relation_get')
    @mock.patch('lib.swift_context.related_units')
    @mock.patch('lib.swift_context.relation_ids')
    @mock.patch('lib.swift_context.IdentityServiceContext')
    @mock.patch('lib.swift_context.determine_api_port')
    @mock.patch('lib.swift_context.unit_get')
    @mock.patch('lib.swift_context.get_host_ip')
    @mock.patch('lib.swift_context.config')
    def test_context_api_v3(self, mock_config, mock_get_host_ip,
                            mock_unit_get, mock_determine_api_port,
                            mock_IdentityServiceContext, mock_relation_ids,
                            mock_related_units, mock_relation_get,
                            mock_leader_get, mock_os_release):
        _relinfo = {
            'auth_protocol': 'http',
            'service_protocol': 'http',
            'auth_host': 'kshost',
            'service_host': 'kshost',
            'auth_port': '5000',
            'service_username': 'svcuser',
            'service_password': 'svcpasswd',
            'service_domain': 'service_domain',
            'service_tenant': 'svctenant',
            'service_port': 'svcport',
            'api_version': '3',
            'admin_domain_id': 'admin_dom_id',
            'service_tenant_id': 'svc_tenant_id',
        }
        self.maxDiff = None
        mock_relation_ids.return_value = ['rid1']
        mock_related_units.return_value = ['ksunit/0']
        mock_relation_get.side_effect = lambda x, y, z: _relinfo[x]
        mock_config.return_value = None
        mock_os_release.return_value = 'queens'
        ctxt = swift_context.SwiftIdentityContext()
        self.assertEqual(ctxt()['api_version'], '3')
        self.assertEqual(ctxt()['admin_domain_id'], 'admin_dom_id')
        self.assertEqual(ctxt()['service_tenant_id'], 'svc_tenant_id')
        self.assertEqual(ctxt()['admin_domain_name'], 'service_domain')
        self.assertEqual(ctxt()['admin_tenant_name'], 'svctenant')


class SwiftContextTestCase(unittest.TestCase):

    @mock.patch('lib.swift_context.config')
    def test_get_swift_hash_file(self, mock_config):
        expected = '##FILEHASH##'
        with tempfile.NamedTemporaryFile() as tmpfile:
            swift_context.SWIFT_HASH_FILE = tmpfile.name
            tmpfile.write(expected.encode('UTF-8'))
            tmpfile.seek(0)
            os.fsync(tmpfile)
            hash = swift_context.get_swift_hash()

        self.assertFalse(mock_config.called)
        self.assertEqual(expected, hash)

    @mock.patch('lib.swift_context.config')
    def test_get_swift_hash_config(self, mock_config):
        expected = '##CFGHASH##'
        mock_config.return_value = expected
        tmpfile = tempfile.mktemp()
        swift_context.SWIFT_HASH_FILE = tmpfile
        hash = swift_context.get_swift_hash()

        with open(tmpfile, 'r') as fd:
            self.assertEqual(expected, fd.read())

        self.assertTrue(mock_config.called)
        self.assertEqual(expected, hash)

    @mock.patch('lib.swift_context.service_name')
    @mock.patch('lib.swift_context.config')
    def test_get_swift_hash_env(self, mock_config, mock_service_name):
        mock_config.return_value = None
        mock_service_name.return_value = "testsvc"
        tmpfile = tempfile.mktemp()
        swift_context.SWIFT_HASH_FILE = tmpfile
        with mock.patch('lib.swift_context.os.environ.get') as mock_env_get:
            mock_env_get.return_value = str(uuid.uuid4())
            hash_ = swift_context.get_swift_hash()
            mock_env_get.assert_has_calls([
                mock.call('JUJU_MODEL_UUID'),
                mock.call('JUJU_ENV_UUID',
                          mock_env_get.return_value)
            ])

        with open(tmpfile, 'r') as fd:
            self.assertEqual(hash_, fd.read())

        self.assertTrue(mock_config.called)

    @mock.patch('lib.swift_context.config')
    def test_get_read_affinity_no_config(self, mock_config):
        mock_config.return_value = None
        read_affinity = swift_context.get_read_affinity()

        self.assertIsNone(read_affinity)

    @mock.patch('lib.swift_context.config')
    def test_get_read_affinity_config_not_malformed(self, mock_config):
        mock_config.return_value = 'r1z1=100, r1=200, r2=300'
        expected = 'r1z1=100, r1=200, r2=300'
        read_affinity = swift_context.get_read_affinity()

        self.assertEqual(expected, read_affinity)

    @mock.patch('lib.swift_context.config')
    def test_get_read_affinity_config_malformed(self, mock_config):
        mock_config.return_value = 'XYZ'

        with self.assertRaises(Exception):
            swift_context.get_read_affinity()

    @mock.patch('lib.swift_context.config')
    def test_get_write_affinity_no_config(self, mock_config):
        mock_config.return_value = None
        write_affinity = swift_context.get_write_affinity()

        self.assertIsNone(write_affinity)

    @mock.patch('lib.swift_context.config')
    def test_get_write_affinity_config_not_malformed(self, mock_config):
        mock_config.return_value = 'r1, r2, r3'
        expected = 'r1, r2, r3'
        write_affinity = swift_context.get_write_affinity()

        self.assertEqual(expected, write_affinity)

    @mock.patch('lib.swift_context.config')
    def test_get_write_affinity_config_malformed(self, mock_config):
        mock_config.return_value = 'XYZ'

        with self.assertRaises(Exception):
            swift_context.get_write_affinity()

    @mock.patch('lib.swift_context.config')
    def test_get_write_affinity_node_count_no_config(self, mock_config):
        mock_config.return_value = None
        write_affinity_node_count = \
            swift_context.get_write_affinity_node_count()

        self.assertIsNone(write_affinity_node_count)

    @mock.patch('lib.swift_context.config')
    def test_get_write_affinity_node_count_config_not_malformed(self,
                                                                mock_config):
        mock_config.return_value = '2 * replicas'
        expected = '2 * replicas'
        write_affinity_node_count = \
            swift_context.get_write_affinity_node_count()

        self.assertEqual(expected, write_affinity_node_count)

    @mock.patch('lib.swift_context.config')
    def test_get_write_affinity_node_count_config_malformed(self, mock_config):
        mock_config.return_value = 'XYZ'

        with self.assertRaises(Exception):
            swift_context.get_write_affinity_node_count()


class SwiftS3ContextTestCase(unittest.TestCase):

    @mock.patch('lib.swift_context.os_release')
    @mock.patch('lib.swift_context.config')
    def test_location(self, mock_config, mock_os_release):
        cfg = {'region': 'London'}

        def fake_config(key):
            return cfg.get(key)

        mock_config.side_effect = fake_config

        mock_os_release.return_value = 'queens'
        ctxt = swift_context.SwiftS3Context()
        self.assertEqual({'location': 'London'}, ctxt())

        mock_os_release.return_value = 'pike'
        ctxt = swift_context.SwiftS3Context()
        self.assertEqual({}, ctxt())

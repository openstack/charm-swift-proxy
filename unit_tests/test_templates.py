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

from unittest import mock
import unittest

from jinja2 import Environment

from charmhelpers.contrib.openstack.templating import get_loader


class ProxyServerTemplateTestCase(unittest.TestCase):

    @mock.patch('charmhelpers.contrib.openstack.templating.log')
    def get_template_for_release(self, os_release, mock_log):
        loader = get_loader('./templates', os_release)
        env = Environment(loader=loader)

        return env.get_template('proxy-server.conf')

    def test_statsd_config_for_all_releases(self):
        """The configs contain statsd settings if statsd-host is set."""
        for release in ('mitaka'):
            template = self.get_template_for_release(release)

            result = template.render(statsd_host='127.0.0.1')

            self.assertTrue("log_statsd_host" in result)
            self.assertTrue("log_statsd_port" in result)
            self.assertTrue("log_statsd_default_sample_rate" in result)

            result = template.render()

            self.assertFalse("log_statsd_host" in result)
            self.assertFalse("log_statsd_port" in result)
            self.assertFalse("log_statsd_default_sample_rate" in result)

    def test_keystonemiddleware_config_for_newer_release(self):
        """Test template should follow newer keystone middleware config."""
        release = 'yoga'
        template = self.get_template_for_release(release)
        result = template.render(auth_type='keystone')
        self.assertFalse('signing_dir' in result)
        self.assertFalse('hash_algorithms' in result)

        self.assertTrue('filter:authtoken' in result)
        self.assertTrue('www_authenticate_uri' in result)
        self.assertTrue('auth_url' in result)

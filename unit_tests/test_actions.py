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

import argparse
import sys
import tempfile
import subprocess

from unittest import mock
import yaml
import unittest

from unittest.mock import patch, MagicMock, call

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
sys.modules['apt'] = MagicMock()
sys.modules['apt_pkg'] = MagicMock()

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec, \
        patch('lib.swift_utils.register_configs') as configs:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import actions.actions
    import actions.add_user


class CharmTestCase(unittest.TestCase):

    def setUp(self, obj, patches):
        super(CharmTestCase, self).setUp()
        self.patches = patches
        self.obj = obj
        self.patch_all()

    def patch(self, method):
        _m = mock.patch.object(self.obj, method)
        mocked = _m.start()
        self.addCleanup(_m.stop)
        return mocked

    def patch_all(self):
        for method in self.patches:
            setattr(self, method, self.patch(method))


class PauseTestCase(CharmTestCase):

    def setUp(self):
        super(PauseTestCase, self).setUp(
            actions.actions, ["service_pause", "set_unit_paused",
                              "assess_status"])

        class FakeArgs(object):
            services = ['swift-proxy', 'haproxy', 'memcached', 'apache2']
        self.args = FakeArgs()

    def test_pauses_services(self):
        """Pause action pauses all of the Swift services."""
        pause_calls = []

        def fake_service_pause(svc):
            pause_calls.append(svc)
            return True

        self.service_pause.side_effect = fake_service_pause

        actions.actions.pause(self.args)
        self.assertEqual(
            pause_calls, ['swift-proxy', 'haproxy', 'memcached', 'apache2'])

    def test_bails_out_early_on_error(self):
        """Pause action fails early if there are errors stopping a service."""
        pause_calls = []

        def maybe_kill(svc):
            if svc == "haproxy":
                return False
            else:
                pause_calls.append(svc)
                return True

        self.service_pause.side_effect = maybe_kill
        self.assertRaisesRegex(
            Exception, "haproxy didn't stop cleanly.",
            actions.actions.pause, self.args)
        self.assertEqual(pause_calls, ["swift-proxy"])

    def test_pause_sets_value(self):
        """Pause action sets the unit-paused value to True."""
        actions.actions.pause(self.args)
        self.set_unit_paused.assert_called_once_with()


class ResumeTestCase(CharmTestCase):

    def setUp(self):
        super(ResumeTestCase, self).setUp(
            actions.actions, ["service_resume", "clear_unit_paused",
                              "assess_status",
                              "get_managed_services_and_ports"])

        class FakeArgs(object):
            services = ['swift-proxy', 'haproxy', 'memcached', 'apache2']
        self.args = FakeArgs()

        def fake_svcs_and_ports(services, ports):
            services.remove('haproxy')
            return services, ports

        self.get_managed_services_and_ports.side_effect = fake_svcs_and_ports

    def test_resumes_services(self):
        """Resume action resumes all of the Swift services."""
        resume_calls = []

        def fake_service_resume(svc):
            resume_calls.append(svc)
            return True

        self.service_resume.side_effect = fake_service_resume
        actions.actions.resume(self.args)
        self.assertEqual(
            resume_calls, ['swift-proxy', 'memcached', 'apache2'])

    def test_bails_out_early_on_error(self):
        """Resume action fails early if there are errors starting a service."""
        resume_calls = []

        def maybe_kill(svc):
            if svc == "apache2":
                return False
            else:
                resume_calls.append(svc)
                return True

        self.service_resume.side_effect = maybe_kill
        self.assertRaisesRegex(
            Exception, "apache2 didn't start cleanly.",
            actions.actions.resume, self.args)
        self.assertEqual(resume_calls, ['swift-proxy', 'memcached'])

    def test_resume_sets_value(self):
        """Resume action sets the unit-paused value to False."""
        actions.actions.resume(self.args)
        self.clear_unit_paused.assert_called_once_with()


class GetActionParserTestCase(unittest.TestCase):

    def test_definition_from_yaml(self):
        """ArgumentParser is seeded from actions.yaml."""
        actions_yaml = tempfile.NamedTemporaryFile(
            prefix="GetActionParserTestCase", suffix="yaml")
        actions_yaml.write(
            yaml.dump({"foo": {"description": "Foo is bar"}}).encode('UTF-8'))
        actions_yaml.seek(0)
        parser = actions.actions.get_action_parser(actions_yaml.name, "foo",
                                                   get_services=lambda: [])
        self.assertEqual(parser.description, 'Foo is bar')


class MainTestCase(CharmTestCase):

    def setUp(self):
        super(MainTestCase, self).setUp(
            actions.actions, ["_get_action_name",
                              "get_action_parser",
                              "action_fail"])

    def test_invokes_pause(self):
        dummy_calls = []

        def dummy_action(args):
            dummy_calls.append(True)

        self._get_action_name.side_effect = lambda: "foo"
        self.get_action_parser = lambda: argparse.ArgumentParser()
        with mock.patch.dict(actions.actions.ACTIONS, {"foo": dummy_action}):
            actions.actions.main([])
        self.assertEqual(dummy_calls, [True])

    def test_unknown_action(self):
        """Unknown actions aren't a traceback."""
        self._get_action_name.side_effect = lambda: "foo"
        self.get_action_parser = lambda: argparse.ArgumentParser()
        exit_string = actions.actions.main([])
        self.assertEqual("Action foo undefined", exit_string)

    def test_failing_action(self):
        """Actions which traceback trigger action_fail() calls."""
        dummy_calls = []

        self.action_fail.side_effect = dummy_calls.append
        self._get_action_name.side_effect = lambda: "foo"

        def dummy_action(args):
            raise ValueError("uh oh")

        self.get_action_parser = lambda: argparse.ArgumentParser()
        with mock.patch.dict(actions.actions.ACTIONS, {"foo": dummy_action}):
            actions.actions.main([])
        self.assertEqual(dummy_calls, ["uh oh"])


class AddUserTestCase(CharmTestCase):

    def setUp(self):
        super(AddUserTestCase, self).setUp(
            actions.add_user, ["action_get", "action_set",
                               "action_fail", "check_call",
                               "try_initialize_swauth", "config",
                               "determine_api_port", "leader_get"])
        self.mock_os_release = self.patch('os_release')

    def test_success(self):
        """Ensure that the action_set is called on succees."""
        self.mock_os_release.return_value = "queens"
        self.config.return_value = "swauth"
        self.action_get.return_value = "test"
        self.determine_api_port.return_value = 8070
        actions.add_user.add_user()
        self.leader_get.assert_called_with("swauth-admin-key")
        calls = [call("account"), call("username"), call("password")]
        self.action_get.assert_has_calls(calls)
        self.action_set.assert_called_once_with({
            'add-user.result': 'Success',
            'add-user.message': "Successfully added the user test",
        })

    def test_failure(self):
        """Ensure that action_fail is called on failure."""
        self.mock_os_release.return_value = "queens"
        self.config.return_value = "swauth"
        self.action_get.return_value = "test"
        self.determine_api_port.return_value = 8070
        self.CalledProcessError = ValueError

        e = subprocess.CalledProcessError(0, "hi", "no")
        self.check_call.side_effect = e
        actions.add_user.add_user()
        self.leader_get.assert_called_with("swauth-admin-key")
        calls = [call("account"), call("username"), call("password")]
        self.action_get.assert_has_calls(calls)
        self.action_set.assert_not_called()

        self.action_fail.assert_called_once_with(
            'Adding user test failed with: "{}"'.format(str(e)))


class DiskUsageTestCase(CharmTestCase):

    TEST_RECON_OUTPUT = (
        b'==================================================='
        b'============================\n--> Starting '
        b'reconnaissance on 9 hosts\n========================'
        b'==================================================='
        b'====\n[2017-11-03 21:50:30] Checking disk usage now'
        b'\nDistribution Graph:\n 40%  108 ******************'
        b'***************************************************'
        b'\n 41%   15 *********\n 42%   50 ******************'
        b'*************\n 43%    5 ***\n 44%    1 \n 45%    '
        b'1 \nDisk usage: space used: 89358060716032 of '
        b'215829411840000\nDisk usage: space free: '
        b'126471351123968 of 215829411840000\nDisk usage: '
        b'lowest: 40.64%, highest: 45.63%, avg: '
        b'41.4021703318%\n==================================='
        b'============================================\n')

    TEST_RESULT = ['Disk usage: space used: 83221GB of 201006GB',
                   'Disk usage: space free: 117785GB of 201006GB',
                   'Disk usage: lowest: 40.64%, highest: 45.63%, avg: '
                   '41.4021703318%']

    def setUp(self):
        super(DiskUsageTestCase, self).setUp(
            actions.actions, ["check_output", "action_set", "action_fail"])

    def test_success(self):
        """Ensure that the action_set is called on success."""
        self.check_output.return_value = b'Swift recon ran OK'
        actions.actions.diskusage([])
        self.check_output.assert_called_once_with(['swift-recon', '-d'])

        self.action_set.assert_called()
        self.action_fail.assert_not_called()

    def test_check_output_failure(self):
        """Ensure that action_fail and action_set are called on
        check_output failure."""
        self.check_output.side_effect = actions.actions.CalledProcessError(
            1, "Failure")

        actions.actions.diskusage([])
        self.check_output.assert_called_once_with(['swift-recon', '-d'])

        self.action_set.assert_called()
        self.action_fail.assert_called()

    def test_failure(self):
        """Ensure that action_fail is called on any other failure."""
        self.check_output.side_effect = Exception("Failure")
        with self.assertRaises(Exception):
            actions.actions.diskusage([])
        self.check_output.assert_called_once_with(['swift-recon', '-d'])

    def test_recon_result(self):
        """Ensure the data ultimately returned is the right format
        """
        self.check_output.return_value = self.TEST_RECON_OUTPUT
        actions.actions.diskusage([])
        self.action_set.assert_called_once_with({'output': self.TEST_RESULT})


class RemoveDevicesTestCase(CharmTestCase):

    def setUp(self):
        super(RemoveDevicesTestCase, self).setUp(
            actions.actions, ["action_fail",
                              "action_get",
                              "remove_from_ring",
                              "balance_rings",
                              "is_elected_leader"])
        self.is_elected_leader.return_value = True

    def test_not_leader(self):
        self.is_elected_leader.return_value = False
        actions.actions.remove_devices([])
        self.action_fail.assert_called()

    def test_ring_valid(self):
        self.action_get.side_effect = ['account', 'd1']
        actions.actions.remove_devices([])
        self.remove_from_ring.assert_called_once_with(
            '/etc/swift/account.builder', 'd1')
        self.balance_rings.assert_called_once()

    def test_ring_invalid(self):
        self.action_get.side_effect = ['other', 'd1']
        actions.actions.remove_devices([])
        self.action_fail.assert_called()
        self.remove_from_ring.assert_not_called()
        self.balance_rings.assert_not_called()


class SetWeightTestCase(CharmTestCase):

    def setUp(self):
        super(SetWeightTestCase, self).setUp(
            actions.actions, ["action_fail",
                              "action_get",
                              "set_weight_in_ring",
                              "balance_rings",
                              "is_elected_leader"])
        self.is_elected_leader.return_value = True

    def test_not_leader(self):
        self.is_elected_leader.return_value = False
        actions.actions.set_weight([])
        self.action_fail.assert_called()

    def test_ring_valid(self):
        self.action_get.side_effect = ['account', 'd1', '0.0']
        actions.actions.set_weight([])
        self.set_weight_in_ring.assert_called_once_with(
            '/etc/swift/account.builder', 'd1', '0.0')
        self.balance_rings.assert_called_once()

    def test_ring_invalid(self):
        self.action_get.side_effect = ['other', 'd1', '0.0']
        actions.actions.set_weight([])
        self.action_fail.assert_called()
        self.set_weight_in_ring.assert_not_called()
        self.balance_rings.assert_not_called()


class DispersionPopulateTestCase(CharmTestCase):

    TEST_OUTPUT = (
        b'Using storage policy: Policy-0 \n'
        b'[KCreated 2 containers for dispersion reporting, 1s, 0 retries\n'
        b'[KCreated 2 objects for dispersion reporting, 1s, 0 retries\n')

    def setUp(self):
        super(DispersionPopulateTestCase, self).setUp(
            actions.actions, ['check_output', 'action_set', 'action_fail'])

    def test_success(self):
        self.check_output.return_value = self.TEST_OUTPUT
        actions.actions.dispersion_populate([])
        self.check_output.assert_called_once_with('swift-dispersion-populate')
        self.action_set.assert_called_once_with({'output': self.TEST_OUTPUT})

    def test_failure(self):
        self.check_output.side_effect = actions.actions.CalledProcessError(
            1, 'swift-dispersion-populate', output='Failure')
        actions.actions.dispersion_populate([])
        self.check_output.assert_called_once_with('swift-dispersion-populate')
        self.action_set.assert_called_once_with({'output': 'Failure'})
        self.action_fail.assert_called_once_with(
            "Failed to run swift-dispersion-populate")


class DispersionReportTestCase(CharmTestCase):

    TEST_OUTPUT = (
        b'Using storage policy: Policy-0 \n'
        b'[KQueried 2 containers for dispersion reporting, 0s, 0 retries\n'
        b'100.00% of container copies found (2 of2)\n'
        b'Sample represents 0.78% of the container partition space\n'
        b'[KQueried 2 objects for dispersion reporting, 0s, 0 retries\n'
        b'! There were 2 partitions missing 0 copies.\n'
        b'100.00% of object copies found (2 of 2)\n'
        b'Sample represents 0.78% of the object partition space')

    def setUp(self):
        super(DispersionReportTestCase, self).setUp(
            actions.actions, ['check_output', 'action_set', 'action_fail'])

    def test_success(self):
        self.check_output.return_value = self.TEST_OUTPUT
        actions.actions.dispersion_report([])
        self.check_output.assert_called_once_with('swift-dispersion-report')
        self.action_set.assert_called_once_with({'output': self.TEST_OUTPUT})

    def test_failure(self):
        self.check_output.side_effect = actions.actions.CalledProcessError(
            1, 'swift-dispersion-report', output='Failure')
        actions.actions.dispersion_report([])
        self.check_output.assert_called_once_with('swift-dispersion-report')
        self.action_set.assert_called_once_with({'output': 'Failure'})
        self.action_fail.assert_called_once_with(
            "Failed to run swift-dispersion-report")

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
import argparse
import os
from subprocess import (
    check_output,
    CalledProcessError,
)
import sys
import yaml


_path = os.path.dirname(os.path.realpath(__file__))
_parent = os.path.abspath(os.path.join(_path, '..'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_parent)


from charmhelpers.core.host import service_pause, service_resume
from charmhelpers.core.hookenv import (
    action_fail,
    action_get,
    action_set,
)
from charmhelpers.contrib.openstack.utils import (
    set_unit_paused,
    clear_unit_paused,
)
from hooks.swift_hooks import CONFIGS
from lib.swift_utils import (
    assess_status,
    balance_rings,
    remove_from_ring,
    services,
    set_weight_in_ring,
    SWIFT_CONF_DIR,
)


def get_action_parser(actions_yaml_path, action_name,
                      get_services=services):
    """Make an argparse.ArgumentParser seeded from actions.yaml definitions."""
    with open(actions_yaml_path) as fh:
        doc = yaml.load(fh)[action_name]["description"]
    parser = argparse.ArgumentParser(description=doc)
    parser.add_argument("--services", default=get_services())
    # TODO: Add arguments for params defined in the actions.yaml
    return parser


# NOTE(ajkavangh) - swift-proxy has been written with a pause that predates the
# enhanced pause-resume, and allowsa --services argument to be passed to
# control the services that are stopped/started.  Thus, not knowing if changing
# this will break other code, the bulk of this custom code has been retained.

def pause(args):
    """Pause all the swift services.

    @raises Exception if any services fail to stop
    """
    for service in args.services:
        stopped = service_pause(service)
        if not stopped:
            raise Exception("{} didn't stop cleanly.".format(service))
    set_unit_paused()
    assess_status(CONFIGS, args.services)


def resume(args):
    """Resume all the swift services.

    @raises Exception if any services fail to start
    """
    for service in args.services:
        started = service_resume(service)
        if not started:
            raise Exception("{} didn't start cleanly.".format(service))
    clear_unit_paused()
    assess_status(CONFIGS, args.services)


def diskusage(args):
    """Runs 'swift-recon -d' and returns values in GB.
    @raises CalledProcessError on check_output failure
    @raises Exception on any other failure
    """
    try:
        raw_output = check_output(['swift-recon', '-d']).decode('UTF-8')
        recon_result = list(line.strip().split(' ')
                            for line in raw_output.splitlines()
                            if 'Disk' in line)
        for line in recon_result:
            if 'space' in line:
                line[4] = str(int(line[4]) // (1024 * 1024 * 1024)) + 'GB'
                line[6] = str(int(line[6]) // (1024 * 1024 * 1024)) + 'GB'
        result = [' '.join(x) for x in recon_result]
        action_set({'output': result})
    except CalledProcessError as e:
        action_set({'output': e.output})
        action_fail('Failed to run swift-recon -d')
    except:
        raise


def remove_devices(args):
    """ Removes the device(s) from the ring(s).

    Removes the device(s) from the ring(s) based on the search pattern.

    :raises SwiftProxyCharmException: if pattern action_get('search-value')
        doesn't match any device in the ring.
    """
    rings_valid = ['account', 'container', 'object', 'all']
    rings_to_update = []
    ring = action_get('ring')
    if ring not in rings_valid:
        action_fail("Invalid ring name '{}'. Should be one of: {}".format(
            ring, ', '.join(rings_valid)))
        return
    if ring == 'all':
        rings_to_update.extend(['account', 'container', 'object'])
    else:
        rings_to_update.append(ring)
    for ring_to_update in rings_to_update:
        ring_to_update_builder = ring_to_update + '.builder'
        ring_to_update_path = os.path.join(SWIFT_CONF_DIR,
                                           ring_to_update_builder)
        remove_from_ring(ring_to_update_path, action_get('search-value'))
    balance_rings()


def set_weight(args):
    """ Sets the device's weight.

    Sets the device's weight based on the search pattern.

    :raises SwiftProxyCharmException: if pattern action_get('search-value')
        doesn't match any device in the ring.
    """
    rings_valid = ['account', 'container', 'object', 'all']
    ring = action_get('ring')
    if ring not in rings_valid:
        action_fail('Invalid ring name.')
        return
    if ring == 'all':
        rings_to_update = ['account', 'container', 'object']
    else:
        rings_to_update = [ring]
    for ring_to_update in rings_to_update:
        ring_to_update_builder = ring_to_update + '.builder'
        ring_to_update_path = os.path.join(SWIFT_CONF_DIR,
                                           ring_to_update_builder)
        set_weight_in_ring(ring_to_update_path, action_get('search-value'),
                           str(action_get('weight')))
    balance_rings()


def dispersion_populate(args):
    """Runs swift-dispersion-populate command and returns the output
    @raises CalledProcessError
    """
    cmd = 'swift-dispersion-populate'
    try:
        output = check_output(cmd)
        action_set({'output': output})
    except CalledProcessError as e:
        action_set({'output': e.output})
        action_fail("Failed to run {}".format(cmd))


def dispersion_report(args):
    """Runs swift-dispersion-report command and returns the output
    @raises CalledProcessError
    """
    cmd = 'swift-dispersion-report'
    try:
        output = check_output(cmd)
        action_set({'output': output})
    except CalledProcessError as e:
        action_set({'output': e.output})
        action_fail("Failed to run {}".format(cmd))


# A dictionary of all the defined actions to callables (which take
# parsed arguments).
ACTIONS = {
    "pause": pause,
    "resume": resume,
    'diskusage': diskusage,
    'remove-devices': remove_devices,
    'set-weight': set_weight,
    "dispersion-populate": dispersion_populate,
    "dispersion-report": dispersion_report}


def main(argv):
    action_name = _get_action_name()
    actions_yaml_path = _get_actions_yaml_path()
    parser = get_action_parser(actions_yaml_path, action_name)
    args = parser.parse_args(argv)
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return "Action {} undefined".format(action_name)
    else:
        try:
            action(args)
        except Exception as e:
            action_fail(str(e))


def _get_action_name():
    """Return the name of the action."""
    return os.path.basename(__file__)


def _get_actions_yaml_path():
    """Return the path to actions.yaml"""
    cwd = os.path.dirname(__file__)
    return os.path.join(cwd, "..", "actions.yaml")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

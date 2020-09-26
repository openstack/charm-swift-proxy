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

_path = os.path.dirname(os.path.realpath(__file__))
_parent = os.path.abspath(os.path.join(_path, '..'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_parent)


from subprocess import (
    check_call,
    CalledProcessError
)

from charmhelpers.core.hookenv import (
    action_get,
    config,
    action_set,
    action_fail,
    leader_get,
    log,
)

from charmhelpers.contrib.openstack.utils import (
    os_release,
    CompareOpenStackReleases,
)

from charmhelpers.contrib.hahelpers.cluster import (
    determine_api_port,
)

from lib.swift_utils import (
    try_initialize_swauth,
)


def add_user():
    """Add a swauth user to swift."""
    if config('auth-type') == 'swauth':
        cmp_openstack = CompareOpenStackReleases(os_release('swift'))
        if cmp_openstack >= 'train':
            message = "swauth is not supported for OpenStack Train and later"
            log(message)
            action_fail(message)
            return None
        try_initialize_swauth()
        account = action_get('account')
        username = action_get('username')
        password = action_get('password')
        bind_port = config('bind-port')
        bind_port = determine_api_port(bind_port, singlenode_mode=True)
        success = True
        try:
            check_call([
                "swauth-add-user",
                "-A", "http://localhost:{}/auth/".format(bind_port),
                "-K", leader_get('swauth-admin-key'),
                "-a", account, username, password])
        except CalledProcessError as e:
            success = False
            log("Has a problem adding user: {}".format(e.output))
            action_fail(
                "Adding user {} failed with: \"{}\""
                .format(username, str(e)))
        if success:
            message = "Successfully added the user {}".format(username)
            action_set({
                'add-user.result': 'Success',
                'add-user.message': message,
            })


if __name__ == '__main__':
    add_user()

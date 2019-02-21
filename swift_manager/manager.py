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

from __future__ import print_function

try:
    import cPickle as pickle
except ModuleNotFoundError:
    import _pickle as pickle
import json
import os
import sys


_usage = """This file is called from the swift_utils.py file to implement
various swift ring builder calls and functions.  It is called with one
parameter which is a json encoded string that contains the 'arguments' string
with the following parameters:

{
    'path': The function that needs ot be performed
    'args': the non-keyword argument to supply to the swift manager call.
    'kwargs': any keyword args to supply to the swift manager call.
}

The result of the call, or an error, is returned as a json encoded result that
is printed to the STDOUT,  Any errors are printed to STDERR.

The format of the output has the same keys as, but in a compressed form:

{
    'result': <whatever the result of the function call was>
    'error': <if an error occured, the text of the error
}

This system is currently needed to decouple the majority of the charm from the
underlying package being used for keystone.
"""

JSON_ENCODE_OPTIONS = dict(
    sort_keys=True,
    allow_nan=False,
    indent=None,
    separators=(',', ':'),
)


# These are the main 'API' functions that are called in the manager.py file

def initialize_ring(path, part_power, replicas, min_hours):
    """Initialize a new swift ring with given parameters."""
    from swift.common.ring import RingBuilder
    ring = RingBuilder(part_power, replicas, min_hours)
    _write_ring(ring, path)


def exists_in_ring(ring_path, node):
    """Return boolean True if the node exists in the ring defined by the
    ring_path.

    :param ring_path: the file representing the ring
    :param node: a dictionary of the node (ip, region, port, zone, weight,
        device)
    :returns: boolean
    """
    ring = _load_builder(ring_path).to_dict()

    for dev in ring['devs']:
        # Devices in the ring can be None if there are holes from previously
        # removed devices so skip any that are None.
        if not dev:
            continue
        d = [(i, dev[i]) for i in dev if i in node and i != 'zone']
        n = [(i, node[i]) for i in node if i in dev and i != 'zone']
        if sorted(d) == sorted(n):
            return True
    return False


def add_dev(ring_path, dev):
    """Add a new device to the ring_path

    The dev is in the form of:

    new_dev = {
        'region': node['region'],
        'zone': node['zone'],
        'ip': node['ip'],
        'replication_ip': node['ip_rep']
        'port': port,
        'replication_port': port_rep,
        'device': node['device'],
        'weight': 100,
        'meta': '',
    }

    :param ring_path: a ring_path for _load_builder
    :parm dev: the device in the above format
    """
    ring = _load_builder(ring_path)
    ring.add_dev(dev)
    _write_ring(ring, ring_path)


def get_min_part_hours(ring_path):
    """Get the min_part_hours for a ring

    :param ring_path: The path for the ring
    :returns: integer that is the min_part_hours
    """
    builder = _load_builder(ring_path)
    return builder.min_part_hours


def get_current_replicas(ring_path):
    """ Gets replicas from the ring (lp1815879)

    :param ring_path: The path for the ring
    :type ring_path: str
    :returns: replicas
    :rtype: int
    """
    builder = _load_builder(ring_path)
    return builder.min_part_hours


def get_zone(ring_path):
    """Determine the zone for the ring_path

    If there is no zone in the ring's devices, then simple return 1 as the
    first zone.

    Otherwise, return the lowest numerically ordered unique zone being used
    across the devices of the ring if the number of unique zones is less that
    the number of replicas for that ring.

    If the replicas >= to the number of unique zones, the if all the zones are
    equal, start again at 1.

    Otherwise, if the zones aren't equal, return the lowest zone number across
    the devices

    :param ring_path: The path to the ring to get the zone for.
    :returns: <integer> zone id
    """
    builder = _load_builder(ring_path)
    replicas = builder.replicas
    zones = [d['zone'] for d in builder.devs]
    if not zones:
        return 1

    # zones is a per-device list, so we may have one
    # node with 3 devices in zone 1.  For balancing
    # we need to track the unique zones being used
    # not necessarily the number of devices
    unique_zones = list(set(zones))
    if len(unique_zones) < replicas:
        return sorted(unique_zones).pop() + 1

    zone_distrib = {}
    for z in zones:
        zone_distrib[z] = zone_distrib.get(z, 0) + 1

    if len(set(zone_distrib.values())) == 1:
        # all zones are equal, start assigning to zone 1 again.
        return 1

    return sorted(zone_distrib, key=zone_distrib.get).pop(0)


def has_minimum_zones(rings):
    """Determine if enough zones exist to satisfy minimum replicas

    Returns a structure with:

    {
        "result": boolean,
        "log": <Not present> | string to log to the debug_log
        "level": <string>
    }

    :param rings: list of strings of the ring_path
    :returns: structure with boolean and possible log
    """
    for ring in rings:
        if not os.path.isfile(ring):
            return {
                "result": False
            }
        builder = _load_builder(ring).to_dict()
        if not builder['devs']:
            return {
                "result": False
            }
        replicas = builder['replicas']
        regions = [dev['region'] for dev in builder['devs'] if dev]
        zones = [dev['zone'] for dev in builder['devs'] if dev]
        num_regions = len(set(regions))
        num_zones = len(set(zones))
        num_zones_in_regions = num_regions * num_zones
        if num_zones_in_regions < replicas:
            log = ("Not enough zones ({}) defined to satisfy minimum "
                   "replicas (need >= {})".format(num_zones, int(replicas)))
            return {
                "result": False,
                "log": log,
                "level": "INFO",
            }

    return {
        "result": True
    }


# These are utility functions that are for the 'API' functions above (i.e. they
# are not called from the main function)

def _load_builder(path):
    # lifted straight from /usr/bin/swift-ring-builder
    from swift.common.ring import RingBuilder
    try:
        builder = pickle.load(open(path, 'rb'))
        if not hasattr(builder, 'devs'):
            builder_dict = builder
            builder = RingBuilder(1, 1, 1)
            builder.copy_from(builder_dict)
    except ImportError:  # Happens with really old builder pickles
        builder = RingBuilder(1, 1, 1)
        builder.copy_from(pickle.load(open(path, 'rb')))
    for dev in builder.devs:
        if dev and 'meta' not in dev:
            dev['meta'] = ''

    return builder


def _write_ring(ring, ring_path):
    with open(ring_path, "wb") as fd:
        pickle.dump(ring.to_dict(), fd, protocol=2)


# The following code is just the glue to link the manager.py and swift_utils.py
# files together at a 'python' function level.


class ManagerException(Exception):
    pass


if __name__ == '__main__':
    # This script needs 1 argument which is the input json.  See file header
    # for details on how it is called.  It returns a JSON encoded result, in
    # the same file, which is overwritten
    result = None
    try:
        if len(sys.argv) != 2:
            raise ManagerException(
                "{} called without 2 arguments: must pass the filename"
                .format(__file__))
        spec = json.loads(sys.argv[1])
        _callable = sys.modules[__name__]
        for attr in spec['path']:
            _callable = getattr(_callable, attr)
        # now make the call and return the arguments
        result = {'result': _callable(*spec['args'], **spec['kwargs'])}
    except ManagerException as e:
        # deal with sending an error back.
        print(str(e), file=sys.stderr)
        import traceback
        print(traceback.format_exc(), file=sys.stderr)
        result = {'error': str(e)}
    except Exception as e:
        print("{}: something went wrong: {}".format(__file__, str(e)),
              file=sys.stderr)
        import traceback
        print(traceback.format_exc(), file=sys.stderr)
        result = {'error': str(e)}
    finally:
        if result is not None:
            result_json = json.dumps(result, **JSON_ENCODE_OPTIONS)
            print(result_json)

    # normal exit
    sys.exit(0)

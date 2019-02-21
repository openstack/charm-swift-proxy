import mock
import unittest

import manager


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


MOCK_SWIFT_RINGS = {
    'account': 'account.builder',
    'container': 'container.builder',
    'object': 'object.builder'
}


class TestSwiftManager(unittest.TestCase):

    @mock.patch('os.path.isfile')
    @mock.patch.object(manager, '_load_builder')
    def test_has_minimum_zones(self, mock_load_builder, mock_is_file):
        mock_rings = {}

        mock_load_builder.side_effect = create_mock_load_builder_fn(mock_rings)
        for ring in MOCK_SWIFT_RINGS:
            mock_rings[ring] = {
                'replicas': 3,
                'devs': [{'region': 1, 'zone': 1},
                         {'region': 1, 'zone': 2},
                         None,
                         {'region': 1, 'zone': 3}],
            }
        ret = manager.has_minimum_zones(MOCK_SWIFT_RINGS)
        self.assertTrue(ret['result'])

        # Increase the replicas to make sure that it returns false
        for ring in MOCK_SWIFT_RINGS:
            mock_rings[ring]['replicas'] = 4

        ret = manager.has_minimum_zones(MOCK_SWIFT_RINGS)
        self.assertFalse(ret['result'])

    @mock.patch.object(manager, '_load_builder')
    def test_exists_in_ring(self, mock_load_builder):
        mock_rings = {}

        mock_load_builder.side_effect = create_mock_load_builder_fn(mock_rings)
        ring = 'account'
        mock_rings[ring] = {
            'devs': [
                {'replication_port': 6000, 'zone': 1, 'weight': 100.0,
                 'ip': '172.16.0.2', 'region': 1, 'port': 6000,
                 'replication_ip': '172.16.0.2', 'parts': 2, 'meta': '',
                 'device': u'bcache10', 'parts_wanted': 0, 'id': 199},
                None,  # Ring can have holes, so add None to simulate
                {'replication_port': 6000, 'zone': 1, 'weight': 100.0,
                 'ip': '172.16.0.2', 'region': 1, 'id': 198,
                 'replication_ip': '172.16.0.2', 'parts': 2, 'meta': '',
                 'device': u'bcache13', 'parts_wanted': 0, 'port': 6000},
            ]
        }

        node = {
            'ip': '172.16.0.2',
            'region': 1,
            'account_port': 6000,
            'zone': 1,
            'replication_port': 6000,
            'weight': 100.0,
            'device': u'bcache10',
        }

        ret = manager.exists_in_ring(ring, node)
        self.assertTrue(ret)

        node['region'] = 2
        ret = manager.exists_in_ring(ring, node)
        self.assertFalse(ret)

    @mock.patch.object(manager, '_write_ring')
    @mock.patch.object(manager, '_load_builder')
    def test_add_dev(self, mock_load_builder, mock_write_ring):
        mock_rings = {}
        mock_load_builder.side_effect = create_mock_load_builder_fn(mock_rings)
        ring = 'account'
        mock_rings[ring] = {
            'devs': []
        }

        new_dev = {
            'meta': '',
            'zone': 1,
            'ip': '172.16.0.2',
            'device': '/dev/sdb',
            'port': 6000,
            'weight': 100
        }
        manager.add_dev(ring, new_dev)
        mock_write_ring.assert_called_once()
        self.assertTrue('id' not in mock_rings[ring]['devs'][0])

# pyinfra
# File: tests/test_api.py
# Desc: tests for the pyinfra API

from unittest import TestCase
from socket import gaierror, error as socket_error

from mock import patch, mock_open
from paramiko import SSHException, AuthenticationException

# Patch in paramiko fake classes
from pyinfra.api import ssh
from .paramiko_util import (
    FakeSSHClient, FakeSFTPClient, FakeRSAKey,
    FakeAgentRequestHandler
)
ssh.SSHClient = FakeSSHClient
ssh.SFTPClient = FakeSFTPClient
ssh.RSAKey = FakeRSAKey
ssh.AgentRequestHandler = FakeAgentRequestHandler


from pyinfra.api import Inventory, Config, State
from pyinfra.api.ssh import connect_all, connect
from pyinfra.api.operation import add_op
from pyinfra.api.operations import run_ops
from pyinfra.api.exceptions import PyinfraError

from pyinfra.modules import files, server

from .util import create_host


def make_inventory(hosts=('somehost', 'anotherhost'), **kwargs):
    return Inventory(
        (hosts, {}),
        test_group=([
            'somehost'
        ], {
            'group_data': 'hello world'
        }),
        ssh_user='vagrant',
        **kwargs
    )


class TestInventoryApi(TestCase):
    def test_inventory_creation(self):
        inventory = make_inventory()

        # Check length
        self.assertEqual(len(inventory.hosts), 2)

        # Get a host
        host = inventory['somehost']
        self.assertEqual(host.data.ssh_user, 'vagrant')

        # Check our group data
        self.assertEqual(
            inventory.get_group_data('test_group').dict(),
            {'group_data': 'hello world'}
        )

    def test_tuple_host_group_inventory_creation(self):
        inventory = make_inventory(
            hosts=[
                ('somehost', {'some_data': 'hello'}),
            ],
            tuple_group=([
                ('somehost', {'another_data': 'world'}),
            ], {
                'group_data': 'word'
            })
        )

        # Check host data
        host = inventory['somehost']
        self.assertEqual(host.data.some_data, 'hello')
        self.assertEqual(host.data.another_data, 'world')

        # # Check group data
        self.assertEqual(host.data.group_data, 'word')


class TestSSHApi(TestCase):
    def test_connect_all(self):
        inventory = make_inventory()
        state = State(inventory, Config())
        connect_all(state)

        self.assertEqual(len(inventory.connected_hosts), 2)

    def test_connect_all_password(self):
        '''
        Ensure we can connect using a password.
        '''

        inventory = make_inventory(ssh_password='test')

        # Get a host
        host = inventory['somehost']
        self.assertEqual(host.data.ssh_password, 'test')

        state = State(inventory, Config())
        connect_all(state)

        self.assertEqual(len(inventory.connected_hosts), 2)

    def test_connect_exceptions_fail(self):
        '''
        Ensure that connection exceptions are captured and return None.
        '''

        for exception in (
            AuthenticationException, SSHException,
            gaierror, socket_error, EOFError
        ):
            host = create_host(name='nowt', data={
                'ssh_hostname': exception
            })
            self.assertEqual(connect(host), None)

    def test_connect_with_ssh_key(self):
        state = State(make_inventory(hosts=(
            ('somehost', {'ssh_key': 'testkey'}),
        )), Config())

        state.deploy_dir = '/'

        with patch('pyinfra.modules.files.path.isfile', lambda *args, **kwargs: True):
            connect_all(state)


class TestStateApi(TestCase):
    def test_fail_percent(self):
        '''
        Ensure that ``Config.FAIL_PERCENT`` works as intended when connecting.
        '''

        inventory = make_inventory(('somehost', SSHException, 'anotherhost'))
        state = State(inventory, Config(FAIL_PERCENT=1))

        # Ensure we would fail at this point
        with self.assertRaises(PyinfraError) as context:
            connect_all(state)
            self.assertEqual(context.exception.message, 'Over 1% of hosts failed')

        # Ensure the other two did connect
        self.assertEqual(len(inventory.connected_hosts), 2)


class TestOperationsApi(TestCase):
    def test_op(self):
        state = State(make_inventory(), Config())
        connect_all(state)

        add_op(
            state, files.file,
            '/var/log/pyinfra.log',
            user='pyinfra',
            group='pyinfra',
            mode='644',
            sudo=True,
            sudo_user='test_sudo',
            su_user='test_su',
            ignore_errors=True
        )

        # Ensure we have an op
        self.assertEqual(len(state.op_order), 1)

        first_op_hash = state.op_order[0]

        # Ensure the op name
        self.assertEqual(
            state.op_meta[first_op_hash]['names'],
            {'Files/File'}
        )

        # Ensure the commands
        self.assertEqual(
            state.ops['somehost'][first_op_hash]['commands'],
            [
                'touch /var/log/pyinfra.log',
                'chmod 644 /var/log/pyinfra.log',
                'chown pyinfra:pyinfra /var/log/pyinfra.log'
            ]
        )

        # Ensure the meta
        meta = state.op_meta[first_op_hash]
        self.assertEqual(meta['sudo'], True)
        self.assertEqual(meta['sudo_user'], 'test_sudo')
        self.assertEqual(meta['su_user'], 'test_su')
        self.assertEqual(meta['ignore_errors'], True)

        # Ensure run ops works
        run_ops(state)

    def test_file_op(self):
        state = State(make_inventory(), Config())
        connect_all(state)

        with patch('pyinfra.modules.files.path.isfile', lambda *args, **kwargs: True):
            # Test normal
            add_op(
                state, files.put,
                'files/file.txt',
                '/home/vagrant/file.txt'
            )

            # And with sudo
            add_op(
                state, files.put,
                'files/file.txt',
                '/home/vagrant/file.txt',
                sudo=True,
                sudo_user='pyinfra'
            )

            # And with su
            add_op(
                state, files.put,
                'files/file.txt',
                '/home/vagrant/file.txt',
                su_user='pyinfra'
            )

        # Ensure we have all ops
        self.assertEqual(len(state.op_order), 3)

        first_op_hash = state.op_order[0]

        # Ensure first op is the right one
        self.assertEqual(
            state.op_meta[first_op_hash]['names'],
            {'Files/Put'}
        )

        # Ensure first op has the right (upload) command
        self.assertEqual(
            state.ops['somehost'][first_op_hash]['commands'],
            [
                ('files/file.txt', '/home/vagrant/file.txt')
            ]
        )

        # Check run ops works
        with patch('pyinfra.api.util.open', mock_open(read_data='test!'), create=True):
            run_ops(state)

    def test_run_ops(self):
        state = State(make_inventory(), Config())
        connect_all(state)

        add_op(
            state, server.shell,
            'echo "hello world"'
        )

        run_ops(state)

    def test_run_ops_serial(self):
        state = State(make_inventory(), Config())
        connect_all(state)

        add_op(
            state, server.shell,
            'echo "hello world"'
        )

        run_ops(state, serial=True)

    def test_run_ops_no_wait(self):
        state = State(make_inventory(), Config())
        connect_all(state)

        add_op(
            state, server.shell,
            'echo "hello world"'
        )

        run_ops(state, no_wait=True)

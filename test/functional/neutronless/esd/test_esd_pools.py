# coding=utf-8
# Copyright 2017 F5 Networks Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from copy import deepcopy
from f5_openstack_agent.lbaasv2.drivers.bigip.icontrol_driver import \
    iControlDriver
import json
import logging
import os
import pytest
import requests

from ..testlib.bigip_client import BigIpClient
from ..testlib.fake_rpc import FakeRPCPlugin
from ..testlib.service_reader import LoadbalancerReader
from ..testlib.resource_validator import ResourceValidator

requests.packages.urllib3.disable_warnings()

LOG = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def services():
    neutron_services_filename = (
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '../../testdata/service_requests/l7_esd_pools.json')
    )
    return (json.load(open(neutron_services_filename)))


@pytest.fixture()
def icd_config():
    oslo_config_filename = (
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '../../config/overcloud_basic_agent_config.json')
    )
    OSLO_CONFIGS = json.load(open(oslo_config_filename))

    config = deepcopy(OSLO_CONFIGS)
    config['icontrol_hostname'] = pytest.symbols.bigip_mgmt_ip_public
    config['icontrol_username'] = pytest.symbols.bigip_username
    config['icontrol_password'] = pytest.symbols.bigip_password

    return config


@pytest.fixture(scope="module")
def bigip():

    return BigIpClient(pytest.symbols.bigip_mgmt_ip_public,
                       pytest.symbols.bigip_username,
                       pytest.symbols.bigip_password)


@pytest.fixture
def fake_plugin_rpc(services):

    rpcObj = FakeRPCPlugin(services)

    return rpcObj


@pytest.fixture
def icontrol_driver(icd_config, fake_plugin_rpc):
    class ConfFake(object):
        def __init__(self, params):
            self.__dict__ = params
            for k, v in self.__dict__.items():
                if isinstance(v, unicode):
                    self.__dict__[k] = v.encode('utf-8')

        def __repr__(self):
            return repr(self.__dict__)

    icd = iControlDriver(ConfFake(icd_config),
                         registerOpts=False)

    icd.plugin_rpc = fake_plugin_rpc
    icd.connect()

    return icd


@pytest.fixture
def esd():
    esd_file = (
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '../../../../etc/neutron/services/f5/esd/demo.json')
    )
    return (json.load(open(esd_file)))


@pytest.mark.skip(reason="ESD is not properly initialized and no cleanup")
def test_esd_pools(track_bigip_cfg, bigip, services, icd_config,
                   icontrol_driver, esd):
    env_prefix = icd_config['environment_prefix']
    service_iter = iter(services)
    validator = ResourceValidator(bigip, env_prefix)

    # create loadbalancer
    # lbaas-loadbalancer-create --name lb1 mgmt_v4_subnet
    service = service_iter.next()
    lb_reader = LoadbalancerReader(service)
    folder = '{0}_{1}'.format(env_prefix, lb_reader.tenant_id())
    icontrol_driver._common_service_handler(service)
    assert bigip.folder_exists(folder)

    # create listener
    # lbaas-listener-create --name l1 --loadbalancer lb1 --protocol HTTP
    # --protocol-port 80
    service = service_iter.next()
    listener = service['listeners'][0]
    icontrol_driver._common_service_handler(service)
    validator.assert_virtual_valid(listener, folder)

    # create pool with session persistence
    # lbaas-pool-create --name p1 --listener l1 --protocol HTTP
    # --lb-algorithm ROUND_ROBIN --session-persistence type=SOURCE_IP
    service = service_iter.next()
    pool = service['pools'][0]
    icontrol_driver._common_service_handler(service)
    validator.assert_pool_valid(pool, folder)

    # apply ESD
    # lbaas-l7policy-create --name esd_demo_1 --listener l1 --action REJECT
    # --description "Override pool session persistence"
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    validator.assert_esd_applied(esd['esd_demo_1'], listener, folder)

    # delete pool
    # lbaas-pool-delete p1
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    validator.assert_pool_deleted(pool, None, folder)
    # deleting pool should NOT remove ESD
    validator.assert_esd_applied(esd['esd_demo_1'], listener, folder)

    # delete ESD
    # lbaas-l7policy-delete esd_demo_1
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    validator.assert_esd_removed(esd['esd_demo_1'], listener, folder)

    # create new ESD
    # lbaas-l7policy-create --name esd_demo_1 --listener l1 --action REJECT
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    validator.assert_esd_applied(esd['esd_demo_1'], listener, folder)

    # create pool with session persistence
    # lbaas-pool-create --name p1 --listener l1 --protocol HTTP
    # --lb-algorithm ROUND_ROBIN --session-persistence type=SOURCE_IP
    # --description "Should not override ESD session persistence"
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    pool = service['pools'][0]
    validator.assert_pool_valid(pool, folder)
    # creating pool should NOT remove ESD
    validator.assert_esd_applied(esd['esd_demo_1'], listener, folder)

    # delete pool
    # lbaas-pool-delete p1
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    validator.assert_pool_deleted(pool, None, folder)

    # delete ESD
    # lbaas-l7policy-delete esd_demo_1
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    validator.assert_esd_removed(esd['esd_demo_1'], listener, folder)

    # delete listener
    # lbaas-listener-delete l1
    service = service_iter.next()
    icontrol_driver._common_service_handler(service)
    validator.assert_virtual_deleted(listener, folder)

    # delete loadbalancer
    # neutron lbaas-loadbalancer-delete lb1
    service = service_iter.next()
    icontrol_driver._common_service_handler(service, delete_partition=True)
    assert not bigip.folder_exists(folder)

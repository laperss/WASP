#! /usr/bin/python
"""
VM Monitor for starting and closing VMs based on demand. 
"""
from __future__ import print_function
import time
import string
import random
import vmanager
import subprocess
import datetime
import io
from collections import Counter

BACKEND_SCRIPT = 'VM-deploy-scripts/backend.sh'
RABBITMQ_SCRIPT = 'VM-deploy-scripts/waspmq.sh'

NETWORK = 'sw_network'

MEAS_SAMPLES = 10 # Number of measurements to take
MEAS_SAMPLE_DELAY = 6 # Number of seconds per measurement
MODIFY_TIMER = 400 # Number of seconds before next modification is allowed
MODIFY_TIMER_ITERATIONS = int(MODIFY_TIMER / (MEAS_SAMPLES * MEAS_SAMPLE_DELAY))


def log(string):
    with open('log_monitor.tsv', 'a', encoding='utf-8') as file:
        time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        file.write(time + "\t" + string + "\n")

def id_generator(prefix, size=6):
    name = ''.join(random.choice(string.ascii_uppercase + string.digits) for i in range(size))
    name = prefix + '_' + name
    return name

def get_vms():
    """ Get a dict with all VMs in the network """
    #vms = manager.list()
    vms = {'backend':[], 'frontend':[], 'waspmq':[]}
    for server in manager.nova.servers.list():
        if NETWORK in server.networks:
            if 'backend' in server.name:
                vms['backend'] += [server.networks[NETWORK][0]]
            elif 'frontend' in server.name:
                vms['frontend'] += [server.networks[NETWORK][0]]
            elif 'waspmq' in server.name:
                vms['waspmq'] += [server.networks[NETWORK][0]]
    return vms

def terminate_vm(name):
    """ Terminate a VM """
    log("backend_terminate")
    print("Terminate " + name)
    manager.terminate(vm=name)

def create_backend():
    """ Start a new VM """
    log("backend_create")
    print("Create backend")
    name = id_generator('backend')
    manager.start_script = BACKEND_SCRIPT
    manager.create(name=name)

def create_frontend():
    """ Start a new VM """
    print("Create frontend")
    name = id_generator('frontend')
    manager.start_script = FRONTEND_SCRIPT
    manager.create(name=name)

def create_rabbitmq():
    """ Start a new VM """
    print("Create rabbitmq")
    name = id_generator('rabbitmq')
    manager.start_script = RABBITMQ_SCRIPT
    manager.create(name=name)

def get_load(user, host, key):
    """ Function for estimating the current demands on the application """
    ssh = "ssh -o ConnectTimeout=2 -o BatchMode=yes -o StrictHostKeyChecking=no "
    top = """ top -b -n 1 | awk 'NR > 7 { sum += $9 } END { print sum }'"""
    cmd = ssh + user + "@" + host + " -i " + key + top
    try:
        load = float(subprocess.check_output(cmd, shell=True))
        log("load\t" + host + "\t" + str(load))
        return load
    except ValueError:
        return -1

def get_name(ip):
    for server in manager.nova.servers.list():
        if NETWORK in server.networks:
            if server.networks[NETWORK][0] == ip:
                return server.name

def check_running(user, host, key):
    ssh = "ssh -o ConnectTimeout=2 -o BatchMode=yes -o StrictHostKeyChecking=no "
    ps = " \" ps -ef | grep '[b]ackend.py' \""
    cmd = ssh + user + "@" + host + " -i " + key + ps

    if subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL) == 0:
        return True
    else:
        return False

def start_backend_script(user, host, key):
    ssh = "ssh -o ConnectTimeout=2 -o BatchMode=yes -o StrictHostKeyChecking=no "
    script = " \" sudo python /usr/local/WASP/backend.py -c /usr/local/WASP/credentials.txt & \""
    cmd = ssh + user + "@" + host + " -i " + key + script
    subprocess.Popen(cmd, shell=True)

print("* Start up the manager...")
manager = vmanager.Manager()

print("* Start monitoring...")
modify_timer = 0
try:
    while True:
        t1 = datetime.datetime.now()
        vms = get_vms()

        # Create backend if none exists
        if (len(vms['backend']) < 1):
            create_backend()
            modify_timer = MODIFY_TIMER_ITERATIONS

        # Obtain loads of backends over SSH
        loads = {}
        for niter in range(MEAS_SAMPLES):
            t1 = datetime.datetime.now()
            # Obtain loads from backends
            for ip in vms['backend']:
                if ip in loads:
                    loads[ip] = loads[ip] + get_load("ubuntu", ip, "~/vm-key.pem")
                else:
                    loads[ip] = get_load("ubuntu", ip, "~/vm-key.pem")
            # Delay until MEAS_DELAY seconds is reached
            while True:
                t2 = datetime.datetime.now()
                delta = t2 - t1
                if delta.seconds < MEAS_SAMPLE_DELAY:
                    time.sleep(0.1)
                else:
                    break

        # Average loads
        for ip in loads:
            loads[ip] /= MEAS_SAMPLES
            log("avgload\t" + ip + "\t" + str(loads[ip]))

        print("Avg load: ", ' '.join('{}'.format(load) for load in loads.values()))

        log("number_backends\t" + str(len(vms['backend'])))

        # Scale up
        if all(load > 90 for load in loads.values()) and modify_timer == 0:
            create_backend()
            modify_timer = MODIFY_TIMER_ITERATIONS

        # Scale down
        if any(load < 5 for load in loads.values()) and modify_timer == 0 and len(vms['backend']) > 1:
            for ip, load in loads.items():
                if load < 5:
                    terminate_vm(get_name(ip))
                    break
            modify_timer = 5 # Scaling down is fast, no need to wait long

        if modify_timer > 0:
            modify_timer -= 1

        # Make sure scripts are running on VM
        for ip in vms['backend']:
            if not check_running("ubuntu", ip, "~/vm-key.pem"):
                print("Script not running, starting it up")
                log("start_script\t" + str(ip))
                start_backend_script("ubuntu", ip, "~/vm-key.pem")

except KeyboardInterrupt:
    print('* Shutting down VM monitor...')

# gradingOnVM.py: Grab a vm from cloud, copy submission+grader to it
# and execute. Then copy the log/grades back.

# Credit: Most of code is from the Autolab project (github.com/autolab).
# Assumption: The AMI used has Autlab's autodriver program (autodriver.c)
# preloaded and necessary user/credentials configured.

# *** read README.md for details. ***

import subprocess
import os
import re
import time
import logging
import tempfile
import shutil
from datetime import datetime
import types
import yaml
import atexit

import boto3
from botocore.exceptions import ClientError

# global config.  See config_defaults.yaml for attributes.
config = None

# cloud specific obj of class Ec2. make it global for cross-object reference.
cloudConnector = None

# vm of class VM.  make it global for cross-object reference.
vm = None
secGroup = "exp_sec_group_" + str(time.time())
secGroupID = None

# exit and exception final handler.
# last opportunity to destroy the vm.
def exitHandler():
    if cloudConnector:
        cloudConnector.destroyVM(notes="destroyVM initiated from exit handler")

class VM():
    def __init__(self, image_tag=None, instance_type=None, name=None):
        self.image_tag = image_tag
        self.name = name
        self.instance_type = instance_type
        self.public_ip = None
        self.image = None
        self.image_id = None
        self.instance = None
        self.instance_id = None

    def configStr(self):
        return "VM(name: %s, tag: %s, type: %s)" % (self.name, self.image_tag, self.instance_type)

    def __repr__(self):
        return "VM(id: %s, ip: %s)" % (self.instance_id, self.public_ip)
# end of class VM

# the cloud dependent module
class Ec2():
    def __init__(self):
        self.image = None
        images = []

        self.log = logging.getLogger("GraderEc2")
        self.log.setLevel(logging.DEBUG)
        logging.getLogger('boto3').setLevel(logging.WARNING)
        logging.getLogger('botocore').setLevel(logging.WARNING)

        try:
            self.boto3client = boto3.client("ec2", config.EC2_REGION,
                                            aws_access_key_id=config.ACCESS_KEY_ID,
                                            aws_secret_access_key=config.SECRET_ACCESS_KEY)
            self.boto3resource = boto3.resource("ec2", config.EC2_REGION,
                                                aws_access_key_id=config.ACCESS_KEY_ID,
                                                aws_secret_access_key=config.SECRET_ACCESS_KEY)
            images = self.boto3resource.images.filter(Owners=["self"])
        except Exception as e:
            self.log.error("Ec2SSH init Failed: %s"% e)
            raise  # serious error

        # Assumption: The image for grading vm is tagged with IMAGE_TAG in the config
        for image in images:
            if image.tags:
                for tag in image.tags:
                    if tag["Key"] == "Name":
                        if tag["Value"]:
                            if tag["Value"] == config.IMAGE_TAG:
                                if self.image is not None:
                                    self.log.warning("Found duplicate name tag %s on image %s, ignore" %
                                                     (config.IMAGE_TAG, image.id))
                                else:
                                    self.log.info("Found image %s with name tag %s" %
                                                  (image.id, config.IMAGE_TAG))
                                    self.image = image

        if (self.image is None):
            self.log.error("Failed to find image with tag %s" % config.IMAGE_TAG)
            exit(-1)
    # end of Ec2. __init__()

    def createSecurityGroup(self):
        # Create may-exist security group
        try:
            response = self.boto3client.create_security_group(
                GroupName=secGroup,
                Description="Autolab grading vm - allowing ssh and ping")
            global secGroupID
            secGroupID = response['GroupId']
            self.log.info("sec group created: %s %s" % (secGroup, secGroupID))
            self.boto3client.authorize_security_group_ingress(
                GroupId=secGroupID,
                # rules for ssh and ping
                IpPermissions=[
                    {'IpProtocol': 'tcp',
                     'ToPort': 22,
                     'FromPort': 22,
                     'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                    },
                    {'IpProtocol': 'icmp',
                     'FromPort': 8,
                     'ToPort': 0,
                     'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                     }
                ])
        except ClientError as e:
            pass

    def initializeVM(self):
        newInstance = None

        try:
            self.log.info("initializeVM: %s" % vm.configStr())

            # ensure that security group exists
            self.createSecurityGroup()

            reservation = self.boto3resource.create_instances(ImageId=self.image.id,
                                                              InstanceType=config.EC2_INST_TYPE,
                                                              KeyName=config.SECURITY_KEY_NAME,
                                                              SecurityGroups=[
                                                                  secGroup],
                                                              MaxCount=1,
                                                              MinCount=1)

            # Sleep for a while to prevent random transient errors observed
            # when the instance is not available yet
            time.sleep(config.TIMER_POLL_INTERVAL)

            newInstance = reservation[0]
            if not newInstance:
                raise ValueError("cannot find new instance for %s" % vm.configStr())

            # Wait for instance to reach 'running' state
            start_time = time.time()
            while True:
                # Note: You'd think we should be able to read the state from the
                # instance but that turns out not working.  So we round up all
                # running intances and find our instance by instance id

                filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
                instances = self.boto3resource.instances.filter(Filters=filters)
                instanceRunning = False

                newInstance.load()  # reload the state of the instance
                for inst in instances.filter(InstanceIds=[newInstance.id]):
                    self.log.info("VM is running. instance id: %s" % newInstance.id)
                    instanceRunning = True

                if instanceRunning:
                    break

                if time.time() - start_time > config.INITIALIZEVM_TIMEOUT:
                    raise ValueError("vm %s timeout (%d seconds) before reaching 'running' state" %
                                     (vm.configStr(), config.INITIALIZEVM_TIMEOUT))

                self.log.debug("Waiting for vm to reach 'running' from 'pending'")
                time.sleep(config.TIMER_POLL_INTERVAL)
            # end of while loop

            # Assign name to EC2 instance
            self.boto3resource.create_tags(Resources=[newInstance.id],
                                           Tags=[{"Key": "Name", "Value": vm.name}])
            self.log.debug("name tag %s created for the vm" % vm.name)

            self.log.info(
                "VM State %s | Reservation %s | Public DNS %s | Public IP %s" %
                (newInstance.state,
                 reservation,
                 newInstance.public_dns_name,
                 newInstance.public_ip_address))

            # Save domain and id ssigned by EC2 in vm object
            vm.public_ip = newInstance.public_ip_address
            vm.image = self.image
            vm.image_id = self.image.id
            vm.instance = newInstance
            vm.instance_id = newInstance.id
            return

        except Exception as e:
            self.log.error("initializeVM Failed: %s" % e)
            if newInstance:
                try:
                    self.boto3resource.instances.filter(InstanceIds=[newInstance.id]).terminate()
                except Exception as e:
                    self.log.error("Exception when terminating: %s" % e)
            exit(-1)
    # end of Ec2.initializeVM()

    # Note: Do NOT use exit() in destroyVM.
    # The caller may want to do more end-of-job work after calling it.
    def destroyVM(self, notes=None):
        global secGroupID
        if (vm is None or vm.instance is None) and secGroupID is None:
            return
        # mark the vm as being destroyed
        vm.instance = None

        self.log.info("destroyVM: %s" % vm)

        try:
            # Keep the vm and mark with meaningful tags for debugging
            if config.KEEP_VM_AFTER_FAILURE:
                self.log.info("Will keep VM %s for further debugging" % vm.name)
                instance = self.boto3resource.Instance(vm.instance_id)
                # delete original name tag "xyz" and replace it with "keep-xyz"
                # add notes tag to give a reason
                tag = self.boto3resource.Tag(vm.instance_id, "Name", vm.name)
                if tag:
                    tag.delete()
                instance.create_tags(Tags=[{"Key": "Name", "Value": "keep-" + vm.name}])
                if notes:
                    instance.create_tags(Tags=[{"Key": "Notes", "Value": notes}])
                return
            if vm.instance_id:
                self.boto3resource.instances.filter(InstanceIds=[vm.instance_id]).terminate()

            totalWait = 120
            while True:
                try:
                    self.boto3client.delete_security_group(GroupId=secGroupID)
                    self.log.info("sec group deleted: %s %s" % (secGroup, secGroupID))
                    secGroupID = None
                    break
                except Exception as e:
                    # before vm is actually terminated, sec group can't be deleted
                    # wait for a while before giving up
                    if totalWait <= 0:
                        self.log.error("failed to delete sec group: %s" % secGroup)
                        break
                    self.log.info("delete sec group exception: %s" % e)
                    self.log.info("delete sec group wait time left: %s" % totalWait)
                    time.sleep(15)
                    totalWait -= 15

        except Exception as e:
            self.log.error("destroyVM Failed: %s" % e)
            pass
    # end of Ec2.destroyVM()
# end of class Ec2

class Grader():
    _SECURITY_KEY_PATH_INDEX_IN_SSH_FLAGS = 1

    def __init__(self):
        error = []  # store errors before logging is ready

        # read config defaults and updates. Then convert to dot form
        with open('config_defaults.yaml', 'r') as f:
            defaults = yaml.load(f, Loader=yaml.FullLoader)
        with open(os.path.join(defaults['passedInDir'], 'config.yaml'), 'r') as f:
            updates = yaml.load(f, Loader=yaml.FullLoader)
        defaults.update(updates)

        # all config must be there
        for key in defaults:
            if defaults[key] is None:
                error.append("config %s is missing" % key)
                break

        # some size config are like "1024*1024".  eval here
        for a in defaults:
            if a.endswith('_SIZE'):
                defaults[a] = int(eval(defaults[a]))

        # set global config in dot form, such as config.IMAGE_TAG
        global config
        config = types.SimpleNamespace(**defaults)

        # course number as image tag can be read as a number. convert to str
        config.IMAGE_TAG = str(config.IMAGE_TAG)

        # set timezone for logging
        os.environ["TZ"] = config.TIMEZONE
        time.tzset()

        self.logfile = os.path.join(config.passedInDir, "grader.log")
        # empty log file instead of "rm".  Make it easy to watch with "tail -f"
        if os.path.exists(self.logfile):
            open(self.logfile, 'w').close()
        logging.basicConfig(
            filename=self.logfile,
            format="%(levelname)s|%(name)s|%(asctime)s|%(message)s")
        self.log = logging.getLogger("Grader")
        self.log.setLevel(logging.DEBUG)

        # chicken and egg problem.  When config parsing has errors, the
        # logger is not ready yet.  Now log and quit if there are errors.
        for e in error:
            self.log.error(e)
        if error:
            exit(-1)

        # output files
        self.output = os.path.join(config.passedInDir, "output")
        self.tmpOutput = os.path.join(config.passedInDir, "tmpOutput")
        if os.path.exists(self.output):
            os.remove(self.output)
        if os.path.exists(self.tmpOutput):
            os.remove(self.tmpOutput)

        # assemble the local file and vm file pair for each input file
        self.inputFiles = []
        for f in config.inputFiles:
            self.inputFiles.append([os.path.join(config.passedInDir, f["src"]), f["dest"]])

        self.ssh_flags = ["-i", config.SECURITY_KEY_PATH,
                          "-o", "StrictHostKeyChecking no",
                          "-o", "GSSAPIAuthentication no"]
        self.vmUser = config.AUTODRIVER_USER_NAME
    # end of Grade. __init__()

    def cmdWithTimeout(self, command, time_out):
        self.log.debug("executing cmd: %s" % command)
        p = subprocess.Popen(command,
                             stdout=open("/dev/null", 'w'),
                             stderr=subprocess.STDOUT)

        # Wait for the command to complete
        t = 0.0
        while t < time_out and p.poll() is None:
            time.sleep(config.TIMER_POLL_INTERVAL)
            t += config.TIMER_POLL_INTERVAL

        # Determine why the while loop terminated
        if p.poll() is None:
            try:
                os.kill(p.pid, 9)
            except OSError:
                pass
            returncode = -1
        else:
            returncode = p.poll()

        self.log.debug("executing cmd: return code %s" % returncode)
        return returncode

    # simply exit on timeout
    def waitVM(self):
        instance_down = 1
        start_time = time.time()

        self.log.info("WaitVM: wait for VM to be ready")
        # First, wait for ping to the vm instance to work
        while instance_down:
            self.log.debug("ping vm at %s" % vm.public_ip)
            instance_down = subprocess.call("ping -c 1 %s" % vm.public_ip,
                                            shell=True,
                                            stdout=open('/dev/null', 'w'),
                                            stderr=subprocess.STDOUT)

            # Wait a bit and try again if we haven't exceeded timeout
            if instance_down:
                time.sleep(config.TIMER_POLL_INTERVAL)
                elapsed_secs = time.time() - start_time
                if (elapsed_secs > config.WAITVM_TIMEOUT):
                    self.log.warning("WAITVM: timeout after %s seconds" % elapsed_secs)
                    exit(-1)

        # The ping worked, so now wait for SSH to work before
        # declaring that the VM is ready
        self.log.debug("VM ping completed")
        while(True):
            elapsed_secs = time.time() - start_time

            # Give up if the elapsed time exceeds the allowable time
            if elapsed_secs > config.WAITVM_TIMEOUT:
                self.log.warning("ssh probe timeout after %d secs" % elapsed_secs)
                exit(-1)

            # If ssh returns neither timeout (-1) nor ssh error
            # (255), then success. Otherwise, keep trying until we run
            # out of time.
            self.log.debug("send ssh probe to vm")
            ret = self.cmdWithTimeout(["ssh"] + self.ssh_flags +
                          ["%s@%s" % (self.vmUser, vm.public_ip),
                           "(:)"], config.WAITVM_TIMEOUT - elapsed_secs)

            if (ret != -1) and (ret != 255):
                self.log.info("WaitVM return normal after %s seconds" % elapsed_secs)
                return

            # Sleep a bit before trying again
            time.sleep(config.TIMER_POLL_INTERVAL)
    # end of Grader.WaitVM()

    # simply exit on error
    def copyIn(self):
        # Create a fresh input directory
        ret = subprocess.call(["ssh"] + self.ssh_flags +
                              ["%s@%s" % (self.vmUser, vm.public_ip),
                               "(rm -rf autolab; mkdir autolab)"])

        # Copy the input files to the input directory
        for pair in self.inputFiles:
            self.log.info("copy to vm: %s as %s" % (pair[0], pair[1]))
            ret = self.cmdWithTimeout(["scp"] +
                               self.ssh_flags +
                               [pair[0], "%s@%s:autolab/%s" %
                                (self.vmUser, vm.public_ip, pair[1])],
                               config.COPYIN_TIMEOUT)
            if ret != 0:
                self.log.error("copy failed. exit")
                exit(-1)

    # runJob() doesn't exit on error.  It lets the caller decide
    # how to handle error, such as still copying data off the vm.
    def runJob(self):
        self.log.info("Running job on VM")

        runcmd = "/usr/bin/time --output=time.out autodriver \
        -u %d -f %d -t %d -o %d " % (
            config.VM_ULIMIT_USER_PROC,
            config.VM_ULIMIT_FILE_SIZE,
            config.RUNJOB_TIMEOUT,
            config.MAX_OUTPUT_FILE_SIZE)
        runcmd = runcmd + ("-z %s " % config.TIMEZONE)
        runcmd = runcmd + ("-i %d " % config.AUTODRIVER_TIMESTAMP_INTERVAL)
        runcmd = runcmd + "autolab &> output"

        # timeout * 2 is a conservative estimate.
        # most likely autodriver already returned a timeout error
        ret = self.cmdWithTimeout(["ssh"] + self.ssh_flags +
                      ["%s@%s" % (self.vmUser, vm.public_ip), runcmd],
                                  config.RUNJOB_TIMEOUT * 2)
        return ret

    # runJob() doesn't exit on error.  It lets the caller decide
    # how to handle error, such as add grader messages into output file.
    def copyOut(self, vm):
        self.log.info("copy from vm to %s" % self.output)
        return self.cmdWithTimeout(["scp"] + self.ssh_flags +
                            ["%s@%s:output" % (self.vmUser, vm.public_ip), self.tmpOutput],
                            config.COPYOUT_TIMEOUT)

    # write msg to output file which will be appended with grading vm's output
    def appendMsg(self, msg):
        f = open(self.output, "a")
        f.write("Grader Container [%s]: %s\n" % (datetime.now().ctime(), msg))
        f.close()
        self.log.info(msg)  # also log it

    def afterJob(self, msg):
        self.appendMsg(msg)
        self.log.info("handling after-job operations")

        # output from grading may not exist
        if os.path.exists(self.tmpOutput):
            self.appendMsg("FOUND output of autodriver from grading VM:\n")
            f1 = open(self.output, "a")
            # append grading vm's output to output
            with open(self.tmpOutput, "r", encoding="ISO-8859-1") as f2:
                shutil.copyfileobj(f2, f1)
            os.remove(self.tmpOutput)
            f1.close()
        else:
            self.appendMsg("NO OUTPUT FILE FROM GRADING VM\n")

        os.chmod(self.output, 0o644)
        cloudConnector.destroyVM(notes=msg)

    def run(self):
        """run - Step a job through its execution sequence
        """
        try:
            ret = {}
            ret["runjob"] = None
            ret["copyout"] = None
            msg = ""

            # Header message for user
            self.appendMsg("Received job %s" % config.SUBMISSION_ID)

            global cloudConnector
            cloudConnector = Ec2()

            global vm
            vm = VM()
            vm.image_tag = config.IMAGE_TAG
            vm.name = "vm_" + config.SUBMISSION_ID
            vm.instance_type = config.EC2_INST_TYPE

            # the call will exit on exception
            cloudConnector.initializeVM()
            self.appendMsg("initialized VM %s" % vm)

            # Wait for the instance to be ready. will exit on failure
            self.waitVM()
            self.appendMsg("VM is ready")

            # Copy input files to VM. will exit on failure
            self.copyIn()
            self.appendMsg("Files copied to VM")

            # Run the job on the virtual machine.
            ret["runjob"] = self.runJob()
            self.appendMsg("Job run on VM.  return code %s" % ret["runjob"])

            # Copy the output back, even if runjob has failed
            ret["copyout"] = self.copyOut(vm)
            self.appendMsg("after copying from VM. return code %s" % ret["copyout"])

            # handle failure(s) of runjob and/or copyout.  runjob error takes priority.
            if ret["runjob"] != 0:
                if ret["runjob"] == 1:  # This should never happen
                    msg = "Error: Autodriver usage error"
                elif ret["runjob"] == -1 or ret["runjob"] == 2:  # both are timeouts
                    msg = "Error: Job timed out. timeout setting: %d seconds" % (
                        config.WAITVM_TIMEOUT)
                elif ret["runjob"] == 3:  # EXIT_OSERROR in Autodriver
                    msg = "Error: OS error while running job on VM"
                else:  # This should never happen
                    msg = "Error: Unknown autodriver error (status=%d)" % (ret["runjob"])
            elif ret["copyout"] != 0:
                self.copyout_errors += 1
                msg += "Error: Copy out from VM failed (status=%d)" % (ret["copyout"])
            else:
                msg = "Success: Autodriver returned normally"

            self.afterJob(msg)

        except Exception as err:
            self.appendMsg("exception %s" % err)
            self.afterJob(msg)
    # end of Grader.run()
# end of class Grader

atexit.register(exitHandler)
Grader().run()

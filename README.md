### Quick introduction

Autolab (github.com/autolab) is an auto-grading system created at CMU.
Over the years, many courses inside and outside CMU have used it as a
grading platform and a large collection of graders have been
developed.  A grader is a program that drives the student submitted
work (summission) and evaluates it.  With the prevalence of cloud
computing, some Autolab grader/submission pairs have been packaged as
containers to run in a different grading system, such as one of the
following -- the list is by no means comprehensive.

* Diderot by CMU: http://www.umut-acar.org/home#diderot
* Gradescope Autograder: https://gradescope-autograders.readthedocs.io/en/latest/ (Andy Pavlo is a user.)
* The Project Zone by CMU: Contact Majd Sakr at cs.cmu.edu/~msakr

Those systems are container-based, that is, they run the
grader/submission pair in a container -- this seems the norm of
current-day grading systems.

Some Autolab grader/submission pairs, however, require the environment
of a VM instead of a container to execute. For example, a programming
project may need FUSE filesystems or privileged mode.  For the purpose
of re-using those valuable VM-dependent Autolab graders in a
container-based grading system we have built this program, referred as
the Grader below.

The Grader creates a VM (AWS EC2 Instance), copies an
Autolab grader/submission pair to the VM and executes it.  After the
execution the Grader copies the log file and grades from the VM to
a specified file location.  Although the
Grader is made for the AWS cloud it can be used in
another public cloud with minor modifications.

In essence, the Grader is not specific for a particular course/project but 
a general purpose grader.  It is designed to be deployed
in a container-based grading system and to run on a VM any Autolab
grader/submission appropriately configured in its environment.

If you are a teaching staff with existing VM-dependent Autolab graders
and look to adapting your graders to the environment of a
container-based grading system, the Grader may be useful to you.

### Adapting to a grading system (for teaching staff)

##### Quick look at the Grader

To adjust the Grader for your chosen grading system, it's
useful to have a quick look at the Grader.  The Grader
is a python program grader.py. The program assumes
there is a directory, /var/run/grader, that should typically contain the following
files:

|     filename            |                 description                   |
|-------------------------|-----------------------------------------------|
|`grader_vm.pem`          |private key credentials to SSH into grading VM |
|`Makefile`               |top level Makefile for autodriver, see ./autodriver|
|`autograde.tar`          |tests and evaluator built by the course staff  |
|`student_submission_file`|can be a simple file or a tarball              |
|`config.yaml`            |see sample file below                          |


The Grader's *execution* generates two more files there:

|  filename  |          description                |
|------------|-------------------------------------|
|`grader.log`|log file from grader.py              |
|`output`    |output and scores from the grading vm|

The config.yaml file contains the config variables for grader.py.  It
overrides ./config_defaults.yaml.    A sample file:
```
inputFiles:
  - {"src": "Makefile", "dest": "Makefile"}
  - {"src": "autograde.tar", "dest": "autograde.tar"}
  - {"src": "student@univ.edu_my1stAssignment.cpp", "dest": "problem1.cpp"}
SUBMISSION_ID: student@univ.edu_my1stAssignment_4
IMAGE_TAG: 'myCourse_fall19_image'

# ec2 related
EC2_REGION: us-east-2
EC2_INST_TYPE: t3.micro

# for boto3
ACCESS_KEY_ID: key_id
SECRET_ACCESS_KEY: key

# for ssh
SECURITY_GROUP: sec_group
SECURITY_KEY_NAME: key_name
SECURITY_KEY_PATH: /var/run/grader/grader_vm.pem  (.pem file matching key_name)
```

With the above setup complete, simply run the Grader:
```
python3 grader.py
```

##### Assumptions and adjustments

Suppose that you have graders (autograde.tar/Makefile) built for
Autolab that you'd like to run in a container-based grading system.  Now
you need to have the following ready.

 * A cloud account to create grading VMs.  It can be independently managed or
from your chosen grading system if it provides such capability.  Here is a
couple of considerations on the cloud account administration:
   * AWS recommends using IAM users rather than root account.
   * You may also need to request an increase of max simultaneous instances of
the type you are using.
   * This grader package tries hard to terminate the instance and security group it creates, but it's good to check your EC2 console for "stray" instances and security groups.

* An VM image (AMI) capable of grading Autolab jobs, see
  ./autodriver/README for details.
  *  The image belongs to the same AWS account and has
a tag keyed with "Name" and with the value of IMAGE_TAG.  Use quotes for IMAGE_TAG in config.yaml

Adjustments and additions needed to fit the Grader into the
grading system:

* Determine the location of the input/output files.  The Grader
assumes they are all in one directory
/var/run/grader.  But depending on the grading systm, this will be
different. 

* Provide a script/program to the grading system as the entry point of the
grading process.  The script does the following:

  - Assemble the audograde.tar/Makefile and student file to the determined locations.
  - Generate the config.yaml file to reflect the environment and configuration.
  - Run grader.py.
  - Parse the scores at the end of the generated output file to the specifications of the grading system.
  - Move the output file and log file to the locations specific to the grading system.
